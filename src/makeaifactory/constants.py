APP_NAME = "makeAiFactory"
APP_VERSION = "0.3.5"
GITHUB_REPO = "dikmri/makeAiFactory"
RUNTIME_VERSION = "2026.06.14.1"
WORKFLOW_TEMPLATE_VERSION = "1"
MANIFEST_VERSION = "1"

COMFY_HOST = "127.0.0.1"
COMFY_PORT_RANGE = (17860, 17960)
COMFY_STARTUP_TIMEOUT = 300

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

VRAM_MINIMUM_GB = 8          # 低VRAMモード最低要件 (これ未満は警告のみで続行)
VRAM_RECOMMENDED_GB = 16     # 通常モード推奨要件
DISK_BUFFER_GB = 20

# ComfyUI 起動フラグ (VRAMモード別)
VRAM_MODE_FLAGS: dict[str, list[str]] = {
    "normal": [],
    "low": ["--lowvram"],
    "novram": ["--novram"],
}
VRAM_MODE_LABELS: dict[str, str] = {
    "normal": "通常モード",
    "low": "低VRAMモード",
    "novram": "超省VRAMモード",
}

LOADIMAGE_NODE_ID = "189"
OUTPUT_VIDEO_NODE_ID = "188"
BASE_VIDEO_NODE_ID = "129"
SEED_NODE_ID = "251"
RESOLUTION_PICKER_NODE_ID = "291"
