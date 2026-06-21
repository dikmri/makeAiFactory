from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.core.diagnostics import (
    build_diagnostic_payload,
    sanitize_text,
    tail_log,
)
from makeaifactory.runtime.system_probe import GpuInfo, SystemInfo


def test_sanitize_text_masks_user_path():
    text = r"C:\Users\daiki\AppData\Local\makeAiFactory\runtime"
    assert sanitize_text(text) == "<PATH_REDACTED>"


def test_sanitize_text_masks_full_path_with_filename():
    text = r"C:\Users\daiki\Pictures\secret_photo.png でエラー"
    result = sanitize_text(text)
    assert "daiki" not in result
    assert "secret_photo" not in result
    assert result == "<PATH_REDACTED> でエラー"


def test_sanitize_text_masks_standalone_filename():
    text = "フォルダ生成 (1/5): my_private_photo.png"
    result = sanitize_text(text)
    assert "my_private_photo" not in result
    assert result == "フォルダ生成 (1/5): <FILENAME_REDACTED>"


def test_sanitize_text_masks_discord_token_shaped_string():
    # GitHubのpush protectionに実トークンと誤検知されないよう、
    # snowflake風にデコードされない非数字のダミー文字列にしている。
    text = "token=MFAKETOKENNOTAREALONE123.FAKEID.NotARealDiscordTokenAtAll1234567890"
    assert "<REDACTED_TOKEN>" in sanitize_text(text)
    assert "NotARealDiscordTokenAtAll" not in sanitize_text(text)


def test_sanitize_text_leaves_normal_text_unchanged():
    text = "通常のエラーメッセージです"
    assert sanitize_text(text) == text


def test_tail_log_returns_empty_for_missing_file(tmp_path):
    assert tail_log(tmp_path / "no_such.log") == ""


def test_tail_log_truncates_to_max_lines(tmp_path):
    log_path = tmp_path / "app.log"
    log_path.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
    excerpt = tail_log(log_path, max_lines=3)
    assert excerpt.splitlines() == ["line7", "line8", "line9"]


def test_tail_log_truncates_to_max_bytes(tmp_path):
    log_path = tmp_path / "app.log"
    log_path.write_text("x" * 1000, encoding="utf-8")
    excerpt = tail_log(log_path, max_bytes=10)
    assert len(excerpt.encode("utf-8")) <= 10


def test_tail_log_drops_sensitive_lines(tmp_path):
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "normal line\ndiscord_token=abc123\nanother normal line",
        encoding="utf-8",
    )
    excerpt = tail_log(log_path)
    assert "discord_token" not in excerpt
    assert "normal line" in excerpt
    assert "another normal line" in excerpt


def test_tail_log_masks_user_path_in_excerpt(tmp_path):
    log_path = tmp_path / "app.log"
    log_path.write_text(r"loaded C:\Users\daiki\input.png", encoding="utf-8")
    excerpt = tail_log(log_path)
    assert "daiki" not in excerpt
    assert "input" not in excerpt
    assert "<PATH_REDACTED>" in excerpt


def test_build_diagnostic_payload_structure(tmp_path):
    system_info = SystemInfo(
        os_name="Windows",
        os_version="11",
        cpu="Test CPU",
        ram_gb=32.0,
        gpus=[GpuInfo(name="Test GPU", vram_mb=16384, driver_version="1.0")],
        disk_free_gb=100.0,
        nvidia_smi_available=True,
    )
    log_path = tmp_path / "app.log"
    log_path.write_text("起動しました", encoding="utf-8")

    payload = build_diagnostic_payload(
        title=r"エラー C:\Users\daiki\foo",
        message="メッセージ",
        detail="詳細",
        system_info=system_info,
        vram_mode="low",
        model_preset="default",
        sage_attention_enabled=True,
        runtime_state="ready",
        app_log_path=log_path,
        user_comment="コメント",
    )

    d = payload.to_dict()
    assert "daiki" not in d["title"]
    assert d["system"]["gpu_name"] == "Test GPU"
    assert d["system"]["vram_gb"] == 16.0
    assert d["vram_mode"] == "low"
    assert d["model_preset"] == "default"
    assert d["sage_attention_enabled"] is True
    assert d["runtime_state"] == "ready"
    assert d["log_excerpt"] == "起動しました"
    assert d["user_comment"] == "コメント"
    assert d["report_id"]
    assert d["timestamp"]


def test_build_diagnostic_payload_handles_missing_system_info(tmp_path):
    payload = build_diagnostic_payload(
        title="title",
        message="message",
        detail="",
        system_info=None,
        vram_mode="default",
        model_preset="default",
        sage_attention_enabled=False,
        runtime_state="idle",
        app_log_path=tmp_path / "missing.log",
    )
    assert payload.to_dict()["system"] == {}
    assert payload.to_dict()["log_excerpt"] == ""
