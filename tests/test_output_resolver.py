import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.comfy.output_resolver import resolve_output_mp4
from makeaifactory.domain.errors import OutputNotFoundError


def test_resolve_from_history():
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir)
        job_id = "test_job"
        prompt_id = "prompt_abc"

        subfolder = outdir / "makeAiFactory" / job_id
        subfolder.mkdir(parents=True)
        mp4 = subfolder / "output_00001.mp4"
        mp4.write_bytes(b"fake mp4")

        history = {
            prompt_id: {
                "outputs": {
                    "188": {
                        "videos": [
                            {"filename": "output_00001.mp4", "subfolder": f"makeAiFactory/{job_id}"}
                        ]
                    }
                }
            }
        }

        result = resolve_output_mp4(history, prompt_id, outdir, job_id)
        assert result == mp4


def test_resolve_empty_history_not_found():
    """historyが空ならOutputNotFoundError (fallback探索は行わない)。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir)
        history = {}
        with pytest.raises(OutputNotFoundError):
            resolve_output_mp4(history, "prompt_abc", outdir, "test_job")


def test_no_latest_fallback():
    """無関係なmp4がoutdir直下に存在しても、prompt出力が空ならfallbackしない。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir)
        random_mp4 = outdir / "random_video.mp4"
        random_mp4.write_bytes(b"fake mp4")

        history = {"some_id": {"outputs": {}}}
        with pytest.raises(OutputNotFoundError):
            resolve_output_mp4(history, "some_id", outdir, "nojob")


def test_reject_path_traversal():
    """subfolderに'..'を含む候補はcomfyui_output_dir配下から外れるため採用しない。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir) / "output"
        outdir.mkdir()
        prompt_id = "prompt_evil"

        # comfyui_output_dirの外(親ディレクトリ)に実ファイルを用意する
        evil_dir = Path(tmpdir) / "evil"
        evil_dir.mkdir()
        evil_mp4 = evil_dir / "output_00001.mp4"
        evil_mp4.write_bytes(b"fake mp4")

        history = {
            prompt_id: {
                "outputs": {
                    "188": {
                        "videos": [
                            {"filename": "output_00001.mp4", "subfolder": "../evil"}
                        ]
                    }
                }
            }
        }

        with pytest.raises(OutputNotFoundError):
            resolve_output_mp4(history, prompt_id, outdir, "test_job")


def test_ignores_other_prompt_id():
    """別のprompt_idキー配下にだけ動画があっても、対象prompt_idでは見つからない。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir)
        other_prompt_id = "prompt_other"

        subfolder = outdir / "makeAiFactory" / "other_job"
        subfolder.mkdir(parents=True)
        mp4 = subfolder / "output_00001.mp4"
        mp4.write_bytes(b"fake mp4")

        history = {
            other_prompt_id: {
                "outputs": {
                    "188": {
                        "videos": [
                            {"filename": "output_00001.mp4", "subfolder": "makeAiFactory/other_job"}
                        ]
                    }
                }
            }
        }

        with pytest.raises(OutputNotFoundError):
            resolve_output_mp4(history, "prompt_target", outdir, "test_job")


def test_resolve_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir)
        history = {}
        with pytest.raises(OutputNotFoundError):
            resolve_output_mp4(history, "missing_id", outdir, "nojob")
