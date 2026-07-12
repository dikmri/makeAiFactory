"""aiohttp ローカル Web サーバー。ブラウザからの画像投入・ジョブ管理・動画配信を行う。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

logger = logging.getLogger(__name__)

# セキュリティヘッダー
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; "
        "media-src 'self' blob:; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}

# エラーコードのユーザー向けメッセージ
_ERROR_MESSAGES: dict[str, str] = {
    "INVALID_PIN": "PINが正しくありません。",
    "SESSION_EXPIRED": "セッションが期限切れです。ページを再読み込みしてください。",
    "ROOM_EXPIRED": "投入口の有効期限が切れました。",
    "ROOM_STOPPED": "投入口は終了しました。必要な場合は、投入口を開いた人にもう一度URLを発行してもらってください。",
    "QUEUE_FULL": "現在、投入口が混雑しています。しばらくしてからもう一度お試しください。",
    "RATE_LIMITED": "リクエストが多すぎます。しばらく待ってからお試しください。",
    "INVALID_FILE_TYPE": "対応していないファイル形式です。JPG / PNG / WEBP のみアップロードできます。",
    "FILE_TOO_LARGE": "ファイルサイズが大きすぎます。20MB以下の画像を選択してください。",
    "IMAGE_TOO_LARGE": "画像の解像度が大きすぎます。4096px以下の画像を使用してください。",
    "GENERATION_BUSY": "現在フォルダ一括生成中のため、インターネット投入口からの受付は停止しています。",
    "GENERATION_FAILED": "生成に失敗しました。しばらくしてからもう一度お試しください。",
}


@dataclass
class RemoteJob:
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str = ""
    ip_hash: str = ""
    image_path: str = ""
    output_path: str | None = None
    status: Literal["queued", "running", "completed", "failed", "cancelled", "expired"] = "queued"
    position: int = 0
    progress_pct: float = 0.0
    progress_label: str = "待機中"
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    error_message: str | None = None
    video_url: str | None = None
    workflow: str | None = None   # ブラウザ連携で指定された生成ワークフロー (None=既定)

    def to_api_dict(self, queue_position: int = 0) -> dict:
        return {
            "jobId": self.job_id,
            "status": self.status,
            "position": queue_position,
            "progressPct": self.progress_pct,
            "progressLabel": self.progress_label,
            "createdAt": self.created_at.isoformat(),
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "errorMessage": self.error_message,
            "videoUrl": self.video_url,
        }


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _get_client_ip(request) -> str:  # type: ignore[no-untyped-def]
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote or "0.0.0.0"


def job_is_owned(job, session_id: str | None, local_token_ok: bool) -> bool:
    """ジョブの所有権を判定する。情報を漏らさないため、判定は厳格にする。

    - ローカルブリッジ経由のジョブ(job.session_id == "")は、正当なローカル
      トークンを提示した要求のみ所有者とみなす。
    - 通常ジョブ(job.session_id が非空)は、その session_id と一致する
      cookie セッションのみ所有者とみなす。ローカルトークンでは通常ジョブに
      アクセスできない。
    """
    owner_sid = job.session_id
    if owner_sid == "":
        # ローカルブリッジ経由のジョブ: ローカルトークンでのみ所有者とみなす
        return local_token_ok
    # 通常ジョブ: cookie セッションが一致する場合のみ所有者とみなす
    return bool(session_id) and session_id == owner_sid


class RoomServer:
    def __init__(
        self,
        config,           # RemoteRoomConfig
        auth_manager,     # AuthManager
        rate_limiter,     # RateLimiter
        jobs: dict,       # dict[str, RemoteJob] — コントローラと共有
        job_queue: asyncio.Queue,
        static_dir: Path,
        ip_salt: str,
        on_stats_changed: Callable | None = None,
        accepting_ref: list | None = None,  # [bool] — ミュータブルフラグ
    ) -> None:
        self._config = config
        self._auth = auth_manager
        self._limiter = rate_limiter
        self._jobs = jobs
        self._job_queue = job_queue
        self._static_dir = static_dir
        self._ip_salt = ip_salt
        self._on_stats_changed = on_stats_changed
        self._accepting_ref = accepting_ref or [True]
        self._runner = None
        self._site = None

    def _hash_ip(self, ip: str) -> str:
        return hashlib.sha256((self._ip_salt + ip).encode()).hexdigest()

    def _make_error_response(self, error_code: str, status: int = 400):
        from aiohttp import web
        return web.Response(
            status=status,
            content_type="application/json",
            text=json.dumps({
                "error": error_code,
                "message": _ERROR_MESSAGES.get(error_code, error_code),
            }),
        )

    def _get_queue_position(self, job_id: str) -> int:
        queued = sorted(
            (j for j in self._jobs.values() if j.status == "queued"),
            key=lambda j: j.created_at,
        )
        for i, j in enumerate(queued):
            if j.job_id == job_id:
                return i + 1
        return 0

    def _emit_stats(self) -> None:
        if self._on_stats_changed:
            stats = {
                "queued": sum(1 for j in self._jobs.values() if j.status == "queued"),
                "running": sum(1 for j in self._jobs.values() if j.status == "running"),
                "completed": sum(1 for j in self._jobs.values() if j.status == "completed"),
                "failed": sum(1 for j in self._jobs.values() if j.status in ("failed", "cancelled")),
            }
            self._on_stats_changed(stats)

    def _local_token_ok(self, request) -> bool:
        """要求ヘッダーのローカルトークンが正当か検証する。"""
        token = getattr(self._config, "local_token", None)
        req = request.headers.get("X-MAF-Local-Token", "")
        return bool(token) and secrets.compare_digest(req, str(token))

    def _get_owned_job_or_404(self, request):
        """要求元が所有するジョブのみを返す。所有権がなければ 404 とする。

        存在確認そのものを漏らさないよう、ジョブ不在と未所有を区別しない。
        """
        from aiohttp import web
        job_id = request.match_info["job_id"]
        job = self._jobs.get(job_id)
        session_id = request.cookies.get("maf_room_session")
        session = self._auth.get_session(session_id)
        ok_session_id = session_id if session is not None else None  # 無効セッションは None 扱い
        if job is None or not job_is_owned(job, ok_session_id, self._local_token_ok(request)):
            raise web.HTTPNotFound()
        return job

    # ── ルート: GET / ──────────────────────────────────────────────────────────

    async def _handle_index(self, request) -> object:
        from aiohttp import web
        index = self._static_dir / "index.html"
        logger.debug("index path: %s (exists: %s)", index, index.exists())
        if not index.exists():
            logger.warning("index.html が見つかりません: %s", index)
            return web.Response(status=404, text="Web UI が見つかりません")
        try:
            content = index.read_bytes()
            return web.Response(body=content, content_type="text/html", charset="utf-8")
        except Exception as e:
            logger.error("index.html 読み込みエラー: %s", e)
            return web.Response(status=500, text=f"Web UI 読み込みエラー: {e}")

    # ── ルート: GET /static/{filename} ────────────────────────────────────────

    _STATIC_MIME: dict[str, str] = {
        ".css": "text/css",
        ".js": "application/javascript",
        ".html": "text/html",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }

    async def _handle_static(self, request) -> object:
        from aiohttp import web
        filename = request.match_info["filename"]
        # パストラバーサル対策
        if ".." in filename or "/" in filename or "\\" in filename:
            raise web.HTTPForbidden()
        file_path = self._static_dir / filename
        if not file_path.exists() or not file_path.is_file():
            raise web.HTTPNotFound()
        ext = file_path.suffix.lower()
        mime = self._STATIC_MIME.get(ext, "application/octet-stream")
        try:
            content = file_path.read_bytes()
            if mime.startswith("text/"):
                return web.Response(body=content, content_type=mime, charset="utf-8")
            return web.Response(body=content, content_type=mime)
        except Exception as e:
            logger.error("static file 読み込みエラー %s: %s", filename, e)
            raise web.HTTPInternalServerError()

    # ── ルート: POST /api/auth ─────────────────────────────────────────────────

    async def _handle_auth(self, request) -> object:
        from aiohttp import web
        try:
            body = await request.json()
        except Exception:
            return self._make_error_response("INVALID_PIN", 400)

        ip = _get_client_ip(request)
        ip_hash = self._hash_ip(ip)

        # PIN 総当たり対策: ロック中の IP は検証せず 429 を返す
        if self._auth.is_pin_locked(ip_hash):
            return self._make_error_response("RATE_LIMITED", 429)

        pin = str(body.get("pin", ""))
        if not self._auth.verify_pin(pin):
            self._auth.record_pin_failure(ip_hash)
            return self._make_error_response("INVALID_PIN", 401)

        # 成功: 失敗記録を解除し、ついでに期限切れ session を清掃する
        self._auth.reset_pin_failures(ip_hash)
        self._auth.cleanup_expired()
        session = self._auth.create_session(ip_hash)

        response = web.Response(
            content_type="application/json",
            text=json.dumps({"ok": True, "csrfToken": session.csrf_token}),
        )
        response.set_cookie(
            "maf_room_session",
            session.session_id,
            httponly=True,
            samesite="Lax",
            max_age=self._config.room_ttl_minutes * 60,
        )
        return response

    # ── ルート: GET /api/room ─────────────────────────────────────────────────

    async def _handle_room_info(self, request) -> object:
        from aiohttp import web
        # 認証不要で公開
        queue_size = sum(1 for j in self._jobs.values() if j.status == "queued")
        running = sum(1 for j in self._jobs.values() if j.status == "running")
        return web.Response(
            content_type="application/json",
            text=json.dumps({
                "status": "running",
                "queueSize": queue_size + running,
                "maxQueueSize": self._config.max_queue_size,
                "accepting": self._accepting_ref[0],
            }),
        )

    # ── ルート: GET /api/workflows (ブラウザ連携用。利用可能なワークフロー一覧) ──

    async def _handle_workflows(self, request) -> object:
        from aiohttp import web
        from ..constants import WORKFLOW_PRESETS
        # ローカルブリッジ起動時に templates/<wf>.json を生成済みのものだけ返す
        job_base = Path(getattr(self._config, "_job_base_dir", "") or ".")
        templates_dir = job_base.parent / "templates"
        items = []
        for key, info in WORKFLOW_PRESETS.items():
            if (templates_dir / f"{key}.json").exists():
                items.append({
                    "key": key,
                    "label": info.get("label", key),
                    "desc": info.get("desc", ""),
                })
        return web.Response(
            content_type="application/json",
            text=json.dumps({"workflows": items}, ensure_ascii=False),
        )

    # ── ルート: GET /userscript.user.js (Tampermonkeyスクリプトを配信) ──────────

    async def _handle_userscript(self, request) -> object:
        from aiohttp import web
        tpl = self._static_dir / "userscript.user.js"
        if not tpl.exists():
            raise web.HTTPNotFound()
        port = request.url.port or self._config.local_port or 0
        token = getattr(self._config, "local_token", "") or ""
        script = tpl.read_text(encoding="utf-8")
        script = script.replace("__MAF_PORT__", str(port)).replace("__MAF_TOKEN__", token)
        return web.Response(
            text=script,
            content_type="application/javascript",
            charset="utf-8",
            headers={"Content-Disposition": "inline; filename=makeaifactory.user.js"},
        )

    # ── ルート: POST /api/jobs ────────────────────────────────────────────────

    async def _handle_create_job(self, request) -> object:
        from aiohttp import web

        if not self._accepting_ref[0]:
            return self._make_error_response("ROOM_STOPPED", 503)

        # === 認証: ブラウザ連携(固定トークン) か、通常のセッション+CSRF か ===
        local_token = getattr(self._config, "local_token", None)
        req_token = request.headers.get("X-MAF-Local-Token", "")
        is_local = bool(local_token) and secrets.compare_digest(req_token, str(local_token))

        if is_local:
            session_id = None
            session = None
            rate_key = "local"
        else:
            session_id = request.cookies.get("maf_room_session")
            session = self._auth.get_session(session_id)
            if session is None:
                return self._make_error_response("SESSION_EXPIRED", 401)
            csrf = request.headers.get("X-MAF-CSRF", "")
            if not self._auth.validate_csrf(session_id, csrf):
                return self._make_error_response("SESSION_EXPIRED", 403)
            rate_key = session.ip_hash

        # キューの混雑確認
        active = sum(1 for j in self._jobs.values() if j.status in ("queued", "running"))
        if active >= self._config.max_queue_size:
            return self._make_error_response("QUEUE_FULL", 429)

        # レート制限確認 (ブラウザ連携は自分のPCからの操作なので免除)
        if not is_local and not self._limiter.is_allowed(rate_key):
            wait_sec = self._limiter.seconds_until_allowed(rate_key)
            return web.Response(
                status=429,
                content_type="application/json",
                text=json.dumps({
                    "error": "RATE_LIMITED",
                    "message": _ERROR_MESSAGES["RATE_LIMITED"],
                    "retryAfterSeconds": wait_sec,
                }),
            )

        # multipart アップロード受信 (image + 任意の workflow フィールド。順序非依存)
        filename = "upload.png"
        image_data = None
        workflow = None
        try:
            reader = await request.multipart()
            while True:
                field = await reader.next()
                if field is None:
                    break
                if field.name == "image":
                    filename = field.filename or "upload.png"
                    image_data = await field.read(decode=True)
                elif field.name == "workflow":
                    workflow = (await field.text()).strip() or None
        except Exception as e:
            logger.warning("multipart 読み込みエラー: %s", e)
            return self._make_error_response("INVALID_FILE_TYPE", 400)
        if image_data is None:
            return self._make_error_response("INVALID_FILE_TYPE", 400)

        # ワークフロー名の検証 (不正値は既定にフォールバック)
        if workflow is not None:
            from ..constants import _VALID_WORKFLOWS
            if workflow not in _VALID_WORKFLOWS:
                logger.warning("不正なworkflow指定を無視: %r", workflow)
                workflow = None
        if is_local:
            logger.info("ローカルブリッジ ジョブ受付: workflow=%s file=%s", workflow or "(既定)", filename)

        # 画像検証 + サニタイズ
        from .upload_validator import validate_upload
        png_data, err = await validate_upload(
            image_data,
            filename,
            self._config.max_upload_mb,
            self._config.max_image_px,
            self._config.allowed_extensions,
        )
        if err:
            head = bytes(image_data or b"")[:80]
            logger.warning("画像検証で却下: code=%s file=%s size=%dB head=%r",
                           err, filename, len(image_data or b""), head)
            return self._make_error_response(err, 400)

        # ジョブ作成
        job = RemoteJob(
            session_id=session_id or "",
            ip_hash=session.ip_hash if session else "local",
            workflow=workflow,
        )

        # 入力画像を保存
        job_dir = Path(self._config._job_base_dir) / job.job_id  # type: ignore[attr-defined]
        job_dir.mkdir(parents=True, exist_ok=True)
        input_path = job_dir / "input.png"
        input_path.write_bytes(png_data)
        job.image_path = str(input_path)

        self._jobs[job.job_id] = job
        if not is_local:
            self._limiter.record_job(rate_key)
            self._auth.record_job(session_id or "")

        await self._job_queue.put(job.job_id)
        position = self._get_queue_position(job.job_id)
        self._emit_stats()

        logger.info("ジョブ追加: %s (セッション: ...%s)", job.job_id, (session_id or "")[-6:])
        return web.Response(
            content_type="application/json",
            text=json.dumps({
                "jobId": job.job_id,
                "status": "queued",
                "position": position,
            }),
        )

    # ── ルート: GET /api/jobs/{job_id} ────────────────────────────────────────

    async def _handle_get_job(self, request) -> object:
        from aiohttp import web
        job = self._get_owned_job_or_404(request)
        job_id = job.job_id

        position = self._get_queue_position(job_id) if job.status == "queued" else 0
        return web.Response(
            content_type="application/json",
            text=json.dumps(job.to_api_dict(position)),
        )

    # ── ルート: GET /api/jobs/{job_id}/video ──────────────────────────────────

    async def _handle_get_video(self, request) -> object:
        from aiohttp import web
        job = self._get_owned_job_or_404(request)
        job_id = job.job_id
        if job.output_path is None:
            raise web.HTTPNotFound()

        video_path = Path(job.output_path)
        if not video_path.exists():
            raise web.HTTPNotFound()

        # Range/Content-Type は aiohttp の FileResponse に委譲する
        return web.FileResponse(
            video_path,
            headers={"Content-Disposition": f'inline; filename="makeAiFactory_{job_id}.mp4"'},
        )

    # ── サーバー起動・停止 ─────────────────────────────────────────────────────

    async def start(self, host: str, port: int) -> None:
        from aiohttp import web

        app = web.Application(
            client_max_size=(self._config.max_upload_mb + 1) * 1024 * 1024
        )

        @web.middleware
        async def _security_middleware(request, handler):
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                response = exc
            for k, v in _SECURITY_HEADERS.items():
                response.headers[k] = v
            return response

        app.middlewares.append(_security_middleware)

        app.router.add_get("/", self._handle_index)
        app.router.add_get("/static/{filename}", self._handle_static)
        app.router.add_post("/api/auth", self._handle_auth)
        app.router.add_get("/api/room", self._handle_room_info)
        app.router.add_get("/api/workflows", self._handle_workflows)
        app.router.add_get("/userscript.user.js", self._handle_userscript)
        app.router.add_post("/api/jobs", self._handle_create_job)
        app.router.add_get("/api/jobs/{job_id}", self._handle_get_job)
        app.router.add_get("/api/jobs/{job_id}/video", self._handle_get_video)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        logger.info("Room server started on %s:%d", host, port)

    async def stop(self) -> None:
        if self._runner:
            try:
                await self._runner.cleanup()
            except Exception as e:
                logger.warning("Room server 停止中にエラー: %s", e)
            self._runner = None
            self._site = None
            logger.info("Room server stopped")
