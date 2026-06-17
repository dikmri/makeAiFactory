from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RemoteRoomConfig:
    enabled: bool = False
    local_host: str = "127.0.0.1"
    local_port: int | None = None          # None = 自動選択
    room_ttl_minutes: int = 180
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
