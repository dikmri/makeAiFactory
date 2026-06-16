import json
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


def test_resolve_fallback_job_id():
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir)
        job_id = "myjob123"

        mp4 = outdir / f"video_{job_id}_output.mp4"
        mp4.write_bytes(b"fake mp4")

        history = {job_id: {"outputs": {}}}

        result = resolve_output_mp4(history, job_id, outdir, job_id)
        assert result.suffix == ".mp4"


def test_resolve_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir)
        history = {}
        with pytest.raises(OutputNotFoundError):
            resolve_output_mp4(history, "missing_id", outdir, "nojob")


def test_resolve_latest_fallback():
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir)
        mp4 = outdir / "random_video.mp4"
        mp4.write_bytes(b"fake mp4")

        history = {}
        result = resolve_output_mp4(history, "some_id", outdir, "nojob")
        assert result == mp4
