"""Build selected components from the TouchDesigner Textport via runpy.run_path."""

from pathlib import Path


def _build(blocks=None, *, output=None, panic_chop="", panic_channel="", scene_tag=""):
    source = Path(__file__).resolve().parent
    blocks_dir = source / "blocks"
    available = {path.stem: path for path in blocks_dir.glob("*.py") if not path.stem.startswith("_")}
    names = sorted(available) if blocks is None else list(blocks)
    missing = [name for name in names if name not in available]
    if missing:
        raise ValueError("No block file for: " + ", ".join(missing))
    if bool(panic_chop) != bool(panic_channel):
        raise ValueError("Set both panic_chop and panic_channel to enable panic")

    output_dir = source / "dist" if output is None else Path(output).expanduser().resolve()
    kit = dict(
        globals(),
        ENGINE_SOURCE=(source / "engine.py").read_text(encoding="utf-8"),
        INTEGRATION={"PANIC_CHOP": panic_chop, "PANIC_CHANNEL": panic_channel, "SCENE_TAG": scene_tag},
    )
    for filename in ("palettes.py", "kit.py"):
        path = source / filename
        exec(compile(path.read_bytes(), str(path), "exec"), kit)

    # Load every requested module before changing the project or writing files.
    builders = []
    for name in names:
        namespace = dict(kit)
        path = available[name]
        exec(compile(path.read_bytes(), str(path), "exec"), namespace)
        builders.append(namespace["build"])

    if builders:
        kit["build_library"](builders, output_dir)


if __name__ == "__main__":
    _build(**globals().get("FXBLOX", {}))
