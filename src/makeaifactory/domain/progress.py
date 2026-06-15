from dataclasses import dataclass, field
from enum import Enum


class SetupState(str, Enum):
    NOT_INSTALLED = "NOT_INSTALLED"
    CHECKING_SYSTEM = "CHECKING_SYSTEM"
    PREPARING_RUNTIME_DIR = "PREPARING_RUNTIME_DIR"
    INSTALLING_UV = "INSTALLING_UV"
    INSTALLING_PYTHON = "INSTALLING_PYTHON"
    CREATING_VENV = "CREATING_VENV"
    INSTALLING_TORCH = "INSTALLING_TORCH"
    INSTALLING_COMFYUI = "INSTALLING_COMFYUI"
    INSTALLING_CUSTOM_NODES = "INSTALLING_CUSTOM_NODES"
    DOWNLOADING_MODELS = "DOWNLOADING_MODELS"
    VERIFYING_FILES = "VERIFYING_FILES"
    BUILDING_WORKFLOW_TEMPLATE = "BUILDING_WORKFLOW_TEMPLATE"
    VALIDATING_COMFYUI = "VALIDATING_COMFYUI"
    READY = "READY"
    FAILED = "FAILED"


class JobState(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    QUEUED = "queued"
    GENERATING = "generating"
    RESOLVING_OUTPUT = "resolving_output"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SetupProgress:
    state: SetupState = SetupState.NOT_INSTALLED
    message: str = ""
    detail: str = ""
    percent: float = 0.0


@dataclass
class JobProgress:
    state: JobState = JobState.PENDING
    message: str = ""
    step: int = 0
    total_steps: int = 0

    @property
    def percent(self) -> float:
        if self.total_steps <= 0:
            return 0.0
        return min(100.0, self.step / self.total_steps * 100)


@dataclass
class ComfyProgressEvent:
    event_type: str
    prompt_id: str = ""
    node_id: str = ""
    step: int = 0
    max_steps: int = 0
    raw: dict = field(default_factory=dict)
