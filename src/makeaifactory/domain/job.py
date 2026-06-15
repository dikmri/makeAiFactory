from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .progress import JobState


def _make_job_id() -> str:
    now = datetime.now()
    short = uuid.uuid4().hex[:6]
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{short}"


@dataclass
class Job:
    job_id: str = field(default_factory=_make_job_id)
    status: JobState = JobState.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    input_path: str = ""
    uploaded_image_name: str = ""
    prompt_id: str = ""
    output_path: str = ""
    workflow_template_version: str = "1"
    seed: int | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "input_path": self.input_path,
            "uploaded_image_name": self.uploaded_image_name,
            "prompt_id": self.prompt_id,
            "output_path": self.output_path,
            "workflow_template_version": self.workflow_template_version,
            "seed": self.seed,
            "error": self.error,
        }

    @property
    def date_str(self) -> str:
        return self.created_at.strftime("%Y-%m-%d")
