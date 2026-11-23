"""Source for the Execute DAT embedded in Build.toe."""

import os
from pathlib import Path
import runpy


def onStart():
    job = Path(project.folder).resolve()
    if os.environ.get("FXBLOX_BUILD_JOB") == str(job):
        runpy.run_path(str(job / "td_build.py"), run_name="__main__")
