import json
from pathlib import Path
import subprocess

import pytest

from tools import build_tox


@pytest.mark.parametrize("complete", [True, False])
def test_exports_are_published_only_after_the_whole_build_succeeds(tmp_path, monkeypatch, complete):
    output = tmp_path / "export folder"
    output.mkdir()
    (output / "zoom_punch.tox").write_bytes(b"previous export")
    (output / "stutter.tox").write_bytes(b"unrelated export")

    def run(command, *, cwd, env, **kwargs):
        job = Path(cwd)
        assert Path(command[1]) == job / "Build.toe"
        assert env["FXBLOX_BUILD_JOB"] == str(job)
        request = json.loads((job / "request.json").read_text())
        assert request["blocks"] == ["zoom_punch", "shockwave"]
        exports = job / "exports"
        exports.mkdir()
        (exports / "zoom_punch.tox").write_bytes(b"new zoom")
        if complete:
            (exports / "shockwave.tox").write_bytes(b"new shockwave")
        result = {"build": "test", "files": [path.name for path in exports.iterdir()]}
        (job / "result.json").write_text(json.dumps(result))
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(build_tox.subprocess, "run", run)
    blocks = ["zoom_punch", "shockwave"]
    if complete:
        build_tox.export(blocks, output, Path("TouchDesigner"), timeout=10)
        assert (output / "zoom_punch.tox").read_bytes() == b"new zoom"
        assert (output / "shockwave.tox").read_bytes() == b"new shockwave"
    else:
        with pytest.raises(RuntimeError, match=r"Expected shockwave\.tox, zoom_punch\.tox"):
            build_tox.export(blocks, output, Path("TouchDesigner"), timeout=10)
        assert (output / "zoom_punch.tox").read_bytes() == b"previous export"
        assert not (output / "shockwave.tox").exists()
    assert (output / "stutter.tox").read_bytes() == b"unrelated export"
    assert not list(output.glob(".fxblox-*"))
