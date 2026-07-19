from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RemoteRoomConfig:
    enabled: bool = False
    local_host: str = "127.0.0.1"
    local_port: int | None = None          # None = 自動選択
    tunnel_enabled: bool = True             # False = トンネルを張らずローカルのみ待受(ブラウザ連携用)
    local_token: str | None = None          # ローカルAPI認証トークン(ブラウザ連携用。設定時 session/CSRF を免除)
    room_ttl_minutes: int = 180             # RLC-01: 0以下は「無期限」を意味する (TTL監視タスク自体を作らない。常駐のローカルブリッジ向け)
    require_pin: bool = True
    max_upload_mb: int = 20
    max_image_px: int = 4096
    max_queue_size: int = 3
    per_session_cooldown_seconds: int = 600
    output_retention_hours: int = 24
    allowed_extensions: tuple[str, ...] = field(
        default_factory=lambda: ("jpg", "jpeg", "png", "webp")
    )

    def to_dict(self) -> dict:
        return {
            "room_ttl_minutes": self.room_ttl_minutes,
            "require_pin": self.require_pin,
            "max_upload_mb": self.max_upload_mb,
            "max_queue_size": self.max_queue_size,
            "per_session_cooldown_seconds": self.per_session_cooldown_seconds,
            "output_retention_hours": self.output_retention_hours,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RemoteRoomConfig":
        return cls(
            room_ttl_minutes=int(data.get("room_ttl_minutes", 180)),
            require_pin=bool(data.get("require_pin", True)),
            max_upload_mb=int(data.get("max_upload_mb", 20)),
            max_queue_size=int(data.get("max_queue_size", 3)),
            per_session_cooldown_seconds=int(data.get("per_session_cooldown_seconds", 600)),
            output_retention_hours=int(data.get("output_retention_hours", 24)),
        )


def build_qr_url(url: str, pin: str, include_pin: bool) -> str:
    """QRコードに載せるURLを組み立てる。

    RLC-01 (5): 「QRコードにPINを含める」設定がOFFのとき、QRを読み取るだけで
    PIN入力を省略して入室できてしまわないよう、URLへのPIN埋め込みを止める
    (PIN自体は引き続き必須のまま。ブラウザ側で別途入力してもらう)。
    include_pin=False、または pin が空(PIN無し設定)の場合は url をそのまま返す。
    """
    if include_pin and pin:
        return f"{url}?pin={pin}"
    return url
