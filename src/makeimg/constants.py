APP_NAME = "makeImg"
APP_VERSION = "0.1.0"
GITHUB_REPO = "dikmri/makeAiFactory"
RUNTIME_VERSION = "2026.06.14.1"

COMFY_HOST = "127.0.0.1"
COMFY_PORT_RANGE = (17860, 17960)
COMFY_STARTUP_TIMEOUT = 300

POSITIVE_PROMPT_NODE_ID = "16"
NEGATIVE_PROMPT_NODE_ID = "40"
KSAMPLER_NODE_ID = "3"
SAVE_IMAGE_NODE_ID = "9"
EMPTY_LATENT_NODE_ID = "53"

VRAM_MINIMUM_GB = 8
VRAM_RECOMMENDED_GB = 16
DISK_BUFFER_GB = 20

VRAM_MODE_FLAGS: dict[str, list[str]] = {
    "normal": [],
    "novram": ["--novram"],
}
VRAM_MODE_LABELS: dict[str, str] = {
    "normal": "通常モード",
    "novram": "超省VRAMモード",
}

MODEL_PRESETS: dict[str, dict] = {
    "normal": {
        "label": "通常モード",
        "desc": "最高品質 | VRAM ~14 GB / RAM ~48 GB+",
    },
}
_VALID_PRESETS = set(MODEL_PRESETS.keys())

WORKFLOW_DIR_NAME = "workflows"
DEFAULT_WORKFLOW_FILE = "画像用_master_api.json"
