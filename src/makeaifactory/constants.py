APP_NAME = "makeAiFactory"
APP_VERSION = "1.1.0"
GITHUB_REPO = "dikmri/makeAiFactory"
RUNTIME_VERSION = "2026.06.14.1"
WORKFLOW_TEMPLATE_VERSION = "1"
MANIFEST_VERSION = "1"

# エラー報告(diagnostics.py/error_reporter.py)の送信先 Discord Webhook URL。
# 投稿専用権限のみのためソースに残しても実害は小さいが、release.yml の
# Secrets注入ステップでビルド時に実際の値へ置換する。空文字の間は報告機能を無効化する。
ERROR_REPORT_WEBHOOK_URL = ""

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
    "novram": ["--novram"],
}
VRAM_MODE_LABELS: dict[str, str] = {
    "normal": "通常モード",
    "novram": "超省VRAMモード",
}

LOADIMAGE_NODE_ID = "189"
OUTPUT_VIDEO_NODE_ID = "188"
BASE_VIDEO_NODE_ID = "129"
SEED_NODE_ID = "251"
RESOLUTION_PICKER_NODE_ID = "291"
UNET_HIGH_NODE_ID = "295"   # UnetLoaderGGUF — 高ノイズ段 (FastMix)
UNET_LOW_NODE_ID  = "296"   # UnetLoaderGGUF — 低ノイズ段 (LowNoise)
SAGE_ATTN_HIGH_NODE_ID = "6"   # PathchSageAttentionKJ — 高ノイズ段
SAGE_ATTN_LOW_NODE_ID  = "7"   # PathchSageAttentionKJ — 低ノイズ段

# ── モデルプリセット ──────────────────────────────────────────────────────────
# unet_high / unet_low はワークフローのノード 295 / 296 に動的にパッチされる
MODEL_PRESETS: dict[str, dict] = {
    "normal": {
        "label":    "通常モード",
        "desc":     "最高品質 | VRAM ~14 GB / RAM ~48 GB+",
        "unet_high": "Wan22-I2V-FastMix_v10-H-Q4_K_M.gguf",
        "unet_low":  "Wan2.2-I2V-A14B-LowNoise-Q6_K.gguf",
    },
    "lite": {
        "label":    "軽量モード",
        "desc":     "高品質 | VRAM ~9 GB / RAM ~32 GB+",
        "unet_high": "Wan22-I2V-FastMix_v10-H-Q3_K_M.gguf",
        "unet_low":  "Wan2.2-I2V-A14B-LowNoise-Q3_K_M.gguf",
    },
    "ultralite": {
        "label":    "超軽量モード",
        "desc":     "標準品質 | VRAM ~8 GB / RAM ~24 GB+",
        "unet_high": "Wan22-I2V-FastMix_v10-H-Q3_K_M.gguf",
        "unet_low":  "Wan2.2-I2V-A14B-LowNoise-Q2_K.gguf",
    },
}
_VALID_PRESETS = set(MODEL_PRESETS.keys())

# ── ワークフロープリセット ────────────────────────────────────────────────────
# 設定メニューの「ワークフロー」で選択する生成ワークフローの種類。
# source は app/workflow/presets/ 配下の API版ワークフローJSON。選択時にサニタイズ
# して runtime_template を再生成する。label / desc は i18n の原文キー。
WORKFLOW_PRESETS: dict[str, dict] = {
    "default": {
        "label": "通常",
        "desc":  "標準の動画生成",
        "source": "default.json",
    },
    "pai": {
        "label": "パイズリ",
        "desc":  "パイズリ向けLoRA・プロンプト構成",
        "source": "pai.json",
    },
    "fe": {
        "label": "フェラ",
        "desc":  "フェラ向けLoRA・プロンプト構成",
        "source": "fe.json",
    },
}
_VALID_WORKFLOWS = set(WORKFLOW_PRESETS.keys())
DEFAULT_WORKFLOW = "default"
