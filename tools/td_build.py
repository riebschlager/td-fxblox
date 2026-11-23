"""Run inside the dedicated TouchDesigner build project."""

import json
from pathlib import Path
import runpy
import traceback

import td


def main():
    job = Path(__file__).resolve().parent
    result = {"build": str(td.app.build)}
    try:
        request = json.loads((job / "request.json").read_text(encoding="utf-8"))
        output = job / "exports"
        runpy.run_path(
            request["builder"],
            init_globals=dict(vars(td), FXBLOX={"blocks": request["blocks"], "output": str(output)}),
            run_name="__main__",
        )
        result["files"] = sorted(path.name for path in output.glob("*.tox") if path.stat().st_size)
    except Exception:
        result["error"] = traceback.format_exc()

    (job / "result.json").write_text(json.dumps(result), encoding="utf-8")
    td.project.quit(force=True)


if __name__ == "__main__":
    main()
