"""RES-01: workflowサニタイズ派生物 (api_source/runtime_template/report) の
書き込み先を bundled側 (app/workflow, 読み取り専用リソース) から runtime_root配下
(workflow_runtime_dir) へ分離したことの単体テスト。

対象:
- AppPaths.api_source_json() / runtime_template_json() / workflow_analysis_report_md()
  が runtime_root 配下を指すこと (bundled側の workflow_dir/patch_rules_json は
  従来どおり読み取り専用リソースのまま変わらないこと)
- ensure_workflow_runtime() の初回生成・移行の優先順位
  (旧bundled配置 → アクティブpreset → defaultpreset)
- AppPaths.ensure_dirs() で workflow_runtime_dir が作られること

AppPaths.app_root は実行環境 (frozen/非frozen) から自動計算されるプロパティで、
コンストラクタ引数では差し替えられないため、実リポジトリの app/workflow を
汚さないよう monkeypatch で bundled側の app_root を tmp_path 配下のダミー
ディレクトリへ差し替えてテストする。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.core.app_controller import ensure_workflow_runtime
from makeaifactory.core.paths import AppPaths


def _make_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppPaths:
    """bundled側(app_root)をtmp_path/bundled、runtime側をtmp_path/runtimeに固定したAppPathsを作る。"""
    bundled_root = tmp_path / "bundled"
    runtime_root = tmp_path / "runtime"
    (bundled_root / "app").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(AppPaths, "app_root", property(lambda self: bundled_root))
    return AppPaths(runtime_root=runtime_root)


def _write_preset(paths: AppPaths, filename: str, content: dict) -> None:
    paths.workflow_presets_dir.mkdir(parents=True, exist_ok=True)
    (paths.workflow_presets_dir / filename).write_text(json.dumps(content), encoding="utf-8")


def _write_legacy_bundled_api_source(paths: AppPaths, content: dict) -> None:
    paths.workflow_dir.mkdir(parents=True, exist_ok=True)
    (paths.workflow_dir / "makeAiFactory_api_source.json").write_text(
        json.dumps(content), encoding="utf-8"
    )


# ── パス解決: runtime側/bundled側の切り分け ──────────────────────────────

def test_api_source_json_points_to_runtime_root(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path, monkeypatch)
    p = paths.api_source_json()
    assert p == paths.runtime_root / "workflow" / "makeAiFactory_api_source.json"
    assert paths.runtime_root in p.parents


def test_runtime_template_json_points_to_runtime_root(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path, monkeypatch)
    p = paths.runtime_template_json()
    assert p == paths.runtime_root / "workflow" / "makeAiFactory_runtime_template.json"
    assert paths.runtime_root in p.parents


def test_workflow_analysis_report_md_points_to_runtime_root(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path, monkeypatch)
    p = paths.workflow_analysis_report_md()
    assert p == paths.runtime_root / "workflow" / "workflow_analysis_report.md"
    assert paths.runtime_root in p.parents


def test_patch_rules_json_stays_in_bundled_workflow_dir(tmp_path, monkeypatch):
    # workflow_patch_rules.json は読み取り専用リソースのため bundled側 (workflow_dir) のまま。
    paths = _make_paths(tmp_path, monkeypatch)
    p = paths.patch_rules_json()
    assert p == paths.workflow_dir / "workflow_patch_rules.json"
    assert paths.app_root in p.parents
    assert paths.runtime_root not in p.parents


def test_ensure_dirs_creates_workflow_runtime_dir(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path, monkeypatch)
    assert not paths.workflow_runtime_dir.exists()
    paths.ensure_dirs()
    assert paths.workflow_runtime_dir.is_dir()


# ── ensure_workflow_runtime: 初回生成・移行の優先順位 ────────────────────

def test_uses_active_preset_when_no_runtime_and_no_legacy(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path, monkeypatch)
    _write_preset(paths, "pai.json", {"marker": "pai-preset"})
    _write_preset(paths, "default.json", {"marker": "default-preset"})

    ensure_workflow_runtime(paths, "pai")

    assert paths.api_source_json().exists()
    assert json.loads(paths.api_source_json().read_text(encoding="utf-8")) == {"marker": "pai-preset"}


def test_falls_back_to_default_when_active_workflow_unknown(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path, monkeypatch)
    _write_preset(paths, "default.json", {"marker": "default-preset"})

    # WORKFLOW_PRESETS に存在しないワークフローID (異常値/旧設定の残骸を想定)
    ensure_workflow_runtime(paths, "nonexistent_workflow")

    assert json.loads(paths.api_source_json().read_text(encoding="utf-8")) == {"marker": "default-preset"}


def test_falls_back_to_default_when_active_preset_file_missing(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path, monkeypatch)
    # "pai" は WORKFLOW_PRESETS に存在するキーだが、対応するpresetファイルが無い状況
    _write_preset(paths, "default.json", {"marker": "default-preset"})

    ensure_workflow_runtime(paths, "pai")

    assert json.loads(paths.api_source_json().read_text(encoding="utf-8")) == {"marker": "default-preset"}


def test_prefers_legacy_bundled_over_presets(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path, monkeypatch)
    _write_preset(paths, "default.json", {"marker": "default-preset"})
    _write_legacy_bundled_api_source(paths, {"marker": "legacy-bundled"})

    ensure_workflow_runtime(paths, "default")

    assert json.loads(paths.api_source_json().read_text(encoding="utf-8")) == {"marker": "legacy-bundled"}


def test_noop_when_runtime_already_exists(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path, monkeypatch)
    _write_preset(paths, "default.json", {"marker": "default-preset"})
    _write_legacy_bundled_api_source(paths, {"marker": "legacy-bundled"})
    paths.api_source_json().parent.mkdir(parents=True, exist_ok=True)
    paths.api_source_json().write_text(json.dumps({"marker": "existing-runtime"}), encoding="utf-8")

    ensure_workflow_runtime(paths, "default")

    # 既存のruntime側api_sourceは上書きされない
    assert json.loads(paths.api_source_json().read_text(encoding="utf-8")) == {"marker": "existing-runtime"}
