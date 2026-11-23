"""Export FX Blox components using a dedicated TouchDesigner process."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


TOOLS = Path(__file__).resolve().parent
SOURCE = TOOLS.parent
MAC_EXECUTABLE = "/Applications/TouchDesigner.app/Contents/MacOS/TouchDesigner"


def export(blocks, output, executable, timeout):
    expected = {f"{block}.tox" for block in blocks}
    output.mkdir(parents=True, exist_ok=True)
    # Stage on the destination filesystem so each completed file can be renamed into place.
    with TemporaryDirectory(prefix=".fxblox-", dir=output) as directory:
        job = Path(directory).resolve()
        project = job / "Build.toe"
        shutil.copyfile(TOOLS / project.name, project)
        shutil.copyfile(TOOLS / "td_build.py", job / "td_build.py")
        (job / "request.json").write_text(
            json.dumps({"builder": str(SOURCE / "build.py"), "blocks": blocks}), encoding="utf-8"
        )
        env = dict(os.environ, FXBLOX_BUILD_JOB=str(job))
        try:
            process = subprocess.run(
                [str(executable), str(project)],
                cwd=job,
                env=env,
                timeout=timeout,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"TouchDesigner timed out after {timeout} seconds. Check for startup or license dialogs."
            ) from exc

        result_file = job / "result.json"
        if not result_file.is_file():
            raise RuntimeError(
                f"TouchDesigner exited with code {process.returncode} before reporting a build result.\n"
                + process.stdout.strip()
            )
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if "error" in result:
            raise RuntimeError(result["error"].rstrip())
        if process.returncode:
            raise RuntimeError(f"TouchDesigner exited with code {process.returncode}.\n{process.stdout.strip()}")

        staged = job / "exports"
        produced = {
            path.name for path in staged.glob("*.tox") if path.is_file() and path.stat().st_size
        }
        if expected != produced or expected != set(result["files"]):
            raise RuntimeError(
                f"Expected {', '.join(sorted(expected))}. Found {', '.join(sorted(produced)) or 'no exports'}."
            )

        for name in sorted(expected):
            if (output / name).is_dir():
                raise RuntimeError(f"Export path is a directory: {output / name}")
        for name in sorted(expected):
            (staged / name).replace(output / name)
        print(f"TouchDesigner {result['build']} exported {len(expected)} component(s).")
        for name in sorted(expected):
            print(output / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("blocks", nargs="*", help="block filenames without the .py extension")
    parser.add_argument("--all", action="store_true", help="export every block")
    parser.add_argument("--output", type=Path, default=SOURCE / "dist", help="export directory")
    parser.add_argument(
        "--touchdesigner",
        type=Path,
        default=os.environ.get("TOUCHDESIGNER_BIN", MAC_EXECUTABLE if sys.platform == "darwin" else None),
        help="path to the TouchDesigner executable (or set TOUCHDESIGNER_BIN)",
    )
    parser.add_argument("--timeout", type=int, default=120, help="maximum seconds for the build")
    args = parser.parse_args()
    if args.all == bool(args.blocks):
        parser.error("provide block names or --all")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    available = {
        path.stem for path in (SOURCE / "blocks").glob("*.py") if not path.stem.startswith("_")
    }
    blocks = sorted(available) if args.all else list(dict.fromkeys(args.blocks))
    missing = sorted(set(blocks) - available)
    if missing:
        parser.error("unknown block(s): " + ", ".join(missing))
    if args.touchdesigner is None:
        parser.error("set --touchdesigner to the TouchDesigner executable")
    executable = args.touchdesigner.expanduser().resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        parser.error(f"TouchDesigner executable not found or not executable: {executable}")

    try:
        export(blocks, args.output.expanduser().resolve(), executable, args.timeout)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Build interrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
