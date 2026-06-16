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
    INSTALLING_SAGEATTENTION = "INSTALLING_SAGEATTENTION"
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
    overall_percent: float = 0.0  # 複数ファイルDL時の全体進捗 (該当しない場合は0)


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
class BenchmarkResult:
    """1回の動画生成で計測したパフォーマンス情報。"""
    elapsed_sec: float = 0.0
    # VRAM
    vram_peak_gb: float = 0.0    # nvidia-smi 計測値 (0 = 取得不可)
    vram_avg_gb: float = 0.0
    vram_total_gb: float = 0.0   # GPU 搭載 VRAM 合計
    gpu_name: str = ""
    vram_mode: str = "normal"    # "normal" | "novram"
    # システム RAM (novram モードで重要)
    ram_peak_gb: float = 0.0     # 生成中の RAM 使用量ピーク
    ram_avg_gb: float = 0.0
    ram_total_gb: float = 0.0    # 搭載 RAM 合計

    @property
    def vram_available(self) -> bool:
        return self.vram_peak_gb > 0.0

    @property
    def ram_available(self) -> bool:
        return self.ram_peak_gb > 0.0


@dataclass
class ComfyProgressEvent:
    event_type: str
    prompt_id: str = ""
    node_id: str = ""
    step: int = 0
    max_steps: int = 0
    raw: dict = field(default_factory=dict)
