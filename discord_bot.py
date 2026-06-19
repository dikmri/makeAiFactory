#!/usr/bin/env python3
"""
makeAiFactory Discord Bot

Discord で画像を送ると動画で返信するBotです。

=== ユーザーが用意するもの ===

1. Discord Developer Portal でBotを作成してトークンを取得
   https://discord.com/developers/applications
   - 「New Application」でアプリを作成
   - 「Bot」タブを開き「Reset Token」でトークンを取得・コピー
   - 同タブの「Privileged Gateway Intents」で「MESSAGE CONTENT INTENT」を有効化

2. BotをDiscordサーバーに招待
   - 「OAuth2」→「URL Generator」を開く
   - スコープ: 「bot」にチェック
   - 権限: Send Messages / Attach Files / Read Message History にチェック
   - 生成されたURLをブラウザで開いてサーバーに招待

3. チャンネルIDを取得
   - Discordの設定→詳細→「開発者モード」を有効化
   - Botを使いたいチャンネルを右クリック→「チャンネルIDをコピー」

4. discord_bot_config.json を作成
   discord_bot_config.json.example をコピーして編集してください

=== セットアップ（開発者作業） ===

   .venv\\Scripts\\pip install "discord.py>=2.3"
   .venv\\Scripts\\python discord_bot.py

=== 動作仕様 ===

- 指定チャンネルに画像を投稿すると動画を返信します
- makeAiFactory で「単体生成中」→ キューで順番待ち（完了後に生成）
- makeAiFactory で「フォルダ生成中」→ リクエストを拒否
- makeAiFactory が起動していない → エラーを返信
- Bot 自体のキューが max_queue_size を超えたら受付拒否
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# ── discord.py ───────────────────────────────────────────────────────────────
try:
    import discord
except ImportError:
    sys.exit(
        "discord.py がインストールされていません。\n"
        "以下のコマンドでインストールしてください:\n"
        "  .venv\\Scripts\\pip install \"discord.py>=2.3\""
    )

# ── makeaifactory パッケージ ──────────────────────────────────────────────────
try:
    from makeaifactory.comfy.api_client import ComfyApiClient
    from makeaifactory.comfy.output_resolver import resolve_output_mp4
    from makeaifactory.comfy.workflow_patcher import (
        WorkflowPatchContext,
        make_output_prefix,
        patch_workflow,
    )
    from makeaifactory.constants import COMFY_HOST, MODEL_PRESETS
    from makeaifactory.core.paths import AppPaths
except ImportError as e:
    sys.exit(
        f"makeaifactory パッケージが見つかりません: {e}\n"
        "プロジェクトルートで実行し、パッケージをインストールしてください:\n"
        "  .venv\\Scripts\\pip install -e ."
    )

# ─────────────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "discord_bot_config.json"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("makeaifactory.discord_bot")


# ─────────────────────────────────────────────────────────────────────────────
# 設定 / 状態ファイル読み込み
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(
            f"設定ファイルが見つかりません: {CONFIG_PATH}\n"
            "discord_bot_config.json.example を参考に discord_bot_config.json を作成してください。"
        )
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    missing = [k for k in ("token", "runtime_root") if not cfg.get(k)]
    if missing:
        sys.exit(f"discord_bot_config.json に必須項目がありません: {missing}")
    return cfg


def read_app_state(runtime_root: Path) -> tuple[str, int]:
    """bot_state.json から (state, comfy_port) を読む。
    5分以上更新されていない場合は "offline" 扱い。"""
    path = runtime_root / "bot_state.json"
    if not path.exists():
        return "offline", 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("updated_at", 0) > 300:
            return "offline", data.get("port", 0)
        return data.get("state", "offline"), data.get("port", 0)
    except Exception:
        return "offline", 0


def load_app_settings(runtime_root: Path) -> dict:
    try:
        return json.loads((runtime_root / "settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 動画生成
# ─────────────────────────────────────────────────────────────────────────────

async def generate_video(image_path: Path, runtime_root: Path, comfy_port: int) -> Path:
    """ComfyUI を使って画像から動画を生成し、出力 MP4 のパスを返す。"""
    settings = load_app_settings(runtime_root)
    model_preset = settings.get("model_preset", "normal")
    preset_def = MODEL_PRESETS.get(model_preset, MODEL_PRESETS["normal"])

    paths = AppPaths(runtime_root=runtime_root)

    template_path = paths.runtime_template_json()
    if not template_path.exists():
        raise FileNotFoundError(f"ワークフローテンプレートが見つかりません: {template_path}")
    with template_path.open(encoding="utf-8") as f:
        template = json.load(f)

    job_id = uuid.uuid4().hex[:8]
    date_str = datetime.now().strftime("%Y%m%d")
    job_dir = paths.job_dir(job_id, date_str)
    job_dir.mkdir(parents=True, exist_ok=True)

    input_copy = job_dir / ("input" + image_path.suffix)
    shutil.copy2(image_path, input_copy)

    base_url = f"http://{COMFY_HOST}:{comfy_port}"
    client = ComfyApiClient(base_url)

    logger.info("ComfyUI 接続確認: %s", base_url)
    await client.wait_until_ready(timeout_sec=30)

    upload_name = f"discord_{job_id}{image_path.suffix}"
    renamed = job_dir / upload_name
    shutil.copy2(image_path, renamed)
    uploaded_name = await client.upload_image(renamed)
    logger.info("画像アップロード: %s", uploaded_name)

    seed = random.randint(0, 2**32 - 1)
    ctx = WorkflowPatchContext(
        job_id=job_id,
        uploaded_image_name=uploaded_name,
        output_prefix=make_output_prefix(job_id),
        seed=seed,
        unet_high_name=preset_def["unet_high"],
        unet_low_name=preset_def["unet_low"],
        sage_attention_mode="disabled",
    )
    patched = patch_workflow(template, ctx)

    prompt_id = await client.queue_prompt(patched)
    logger.info("生成開始: job=%s prompt=%s", job_id, prompt_id)

    async for event in client.watch_progress(prompt_id):
        if event.event_type == "execution_error":
            raise RuntimeError("ComfyUI で生成エラーが発生しました")

    history = await client.get_history(prompt_id)
    output_mp4 = resolve_output_mp4(history, prompt_id, paths.comfyui_output_dir, job_id)

    final_output = job_dir / "output.mp4"
    shutil.copy2(output_mp4, final_output)
    logger.info("生成完了: %s → %s", job_id, final_output)
    return final_output


# ─────────────────────────────────────────────────────────────────────────────
# Discord Bot
# ─────────────────────────────────────────────────────────────────────────────

class MakeAiFactoryBot:
    def __init__(self, config: dict):
        self._runtime_root = Path(config["runtime_root"])
        self._channel_ids: set[int] = set(config.get("channel_ids", []))
        self._max_queue_size: int = config.get("max_queue_size", 5)

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._queue: asyncio.Queue[tuple[discord.Message, discord.Attachment]] = asyncio.Queue()

        self._client.event(self._on_ready)
        self._client.event(self._on_message)

    def run(self, token: str) -> None:
        async def _main() -> None:
            asyncio.create_task(self._worker())
            async with self._client:
                await self._client.start(token)

        asyncio.run(_main())

    async def _on_ready(self) -> None:
        logger.info("Bot 起動: %s (ID: %s)", self._client.user, self._client.user.id)
        if self._channel_ids:
            logger.info("監視チャンネル: %s", self._channel_ids)
        else:
            logger.info("監視チャンネル: 全チャンネル（channel_ids 未設定）")

    async def _on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if self._channel_ids and message.channel.id not in self._channel_ids:
            return

        image_att = next(
            (a for a in message.attachments
             if Path(a.filename).suffix.lower() in SUPPORTED_EXTENSIONS),
            None,
        )
        if image_att is None:
            return

        if self._queue.qsize() >= self._max_queue_size:
            await message.reply("リクエストが集中しています。しばらく待ってからもう一度お試しください。")
            return

        state, _ = read_app_state(self._runtime_root)

        if state == "batch":
            await message.reply(
                "フォルダ生成中のため、現在リクエストを受け付けられません。\n"
                "フォルダ生成が完了してからもう一度お試しください。"
            )
            return

        if state == "offline":
            await message.reply("makeAiFactory が起動していないため、生成できません。")
            return

        # idle or single → キューに追加
        pos = self._queue.qsize() + 1
        if pos == 1:
            await message.reply("受け付けました。生成を開始します...")
        else:
            await message.reply(f"受け付けました。現在 {pos} 番目に並んでいます。しばらくお待ちください。")

        await self._queue.put((message, image_att))

    async def _worker(self) -> None:
        while True:
            message, attachment = await self._queue.get()
            try:
                await self._process(message, attachment)
            except Exception as e:
                logger.exception("生成処理エラー")
                try:
                    await message.reply(f"生成中にエラーが発生しました。\n`{e}`")
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    async def _process(self, message: discord.Message, attachment: discord.Attachment) -> None:
        # デキュー直前に状態を再確認（バッチが始まっていたらキャンセル）
        state, comfy_port = read_app_state(self._runtime_root)
        if state == "batch":
            await message.reply("フォルダ生成が始まったため、このリクエストはキャンセルされました。")
            return
        if state == "offline":
            await message.reply("makeAiFactory がオフラインになったため、生成できません。")
            return
        if comfy_port == 0:
            await message.reply("ComfyUI のポートが不明です。makeAiFactory を再起動してください。")
            return

        tmp_dir = self._runtime_root / "downloads" / f"discord_{uuid.uuid4().hex[:6]}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(attachment.filename).suffix.lower() or ".png"
        image_path = tmp_dir / f"input{suffix}"

        try:
            await attachment.save(image_path)
            logger.info("Discord 画像保存: %s (%s)", attachment.filename, image_path)

            await message.reply("生成中です。数分お待ちください...")
            output_path = await generate_video(image_path, self._runtime_root, comfy_port)
            await message.reply(file=discord.File(str(output_path), filename="output.mp4"))
            logger.info("Discord 返信完了: %s", output_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    config = load_config()
    bot = MakeAiFactoryBot(config)
    logger.info("makeAiFactory Discord Bot を起動します...")
    bot.run(config["token"])


if __name__ == "__main__":
    main()
