from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    session_id: str
    ip_hash: str
    csrf_token: str
    created_at: float = field(default_factory=time.monotonic)
    last_job_at: float = 0.0


class AuthManager:
    def __init__(self, pin: str, require_pin: bool, ttl_seconds: int) -> None:
        self._require_pin = require_pin
        self._pin_hash = hashlib.sha256(pin.encode()).hexdigest() if require_pin else ""
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl_seconds

    @property
    def require_pin(self) -> bool:
        return self._require_pin

    def verify_pin(self, pin: str) -> bool:
        if not self._require_pin:
            return True
        return secrets.compare_digest(
            hashlib.sha256(pin.encode()).hexdigest(),
            self._pin_hash,
        )

    def create_session(self, ip_hash: str) -> Session:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_hex(32)
        session = Session(session_id=session_id, ip_hash=ip_hash, csrf_token=csrf_token)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        return self._sessions.get(session_id)

    def validate_csrf(self, session_id: str | None, csrf_token: str) -> bool:
        session = self.get_session(session_id)
        if not session:
            return False
        return secrets.compare_digest(session.csrf_token, csrf_token)

    def record_job(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if session:
            session.last_job_at = time.monotonic()

    def cleanup_expired(self) -> None:
        now = time.monotonic()
        expired = [sid for sid, s in self._sessions.items() if now - s.created_at > self._ttl]
        for sid in expired:
            del self._sessions[sid]
