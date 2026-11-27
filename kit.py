# kit.py
# Builder helpers shared by every block. build.py runs this inside TouchDesigner
# and supplies ENGINE_SOURCE and INTEGRATION. Blocks use these helpers
# directly, without importing anything.

from pprint import pformat

LIBRARY_NAME = "vj_block_library"

TICK_SOURCE = r'''
def onFrameStart(*args, **kwargs):
    op("engine").module.tick()
    return
'''

RESET_SOURCE = r'''
def onPulse(*args, **kwargs):
    op("engine").module.reset()
    return
'''

# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _set_range(par, default, low, high, clamp_low=False, clamp_high=False):
    par.default = default
    par.val = default
    par.normMin = low
    par.normMax = high
    par.min = low
    par.max = high
    par.clampMin = clamp_low
    par.clampMax = clamp_high


def add_float(page, name, label, default, low, high, clamp_low=False, clamp_high=False):
    par = page.appendFloat(name, label=label)[0]
    _set_range(par, default, low, high, clamp_low, clamp_high)
    return par


def add_menu(page, name, label, names, labels, default):
    par = page.appendMenu(name, label=label)[0]
    par.menuNames = names
    par.menuLabels = labels
    par.default = default
    par.val = default
    return par


def add_palette_menu(page, name="Palette", label="Palette", *, presets=None, default=None):
    presets = presets or {}
    overlap = presets.keys() & PALETTE_LABELS.keys()
    if overlap:
        raise ValueError("Shared palette names already used by presets: " + ", ".join(sorted(overlap)))
    choices = {**presets, **PALETTE_LABELS}
    return add_menu(page, name, label, list(choices), list(choices.values()), default or next(iter(choices)))


def glsl_rgb(color):
    channels = ", ".join(f"{channel}.0" for channel in color)
    return f"vec3({channels}) / 255.0"


def glsl_swatches(name, swatches):
    colors = ", ".join(glsl_rgb(SWATCH_RGB8[swatch]) for swatch in swatches)
    count = len(swatches)
    return f"const vec3 {name}[{count}] = vec3[{count}]({colors});"


def palette_shader(source, **declarations):
    if "@@PALETTE_GLSL@@" in source:
        rows = ["    " + ", ".join(map(glsl_rgb, row)) for row in PALETTE_RGB8.values()]
        table = (
            f"const int STOP_COUNT = {STOP_COUNT};\n"
            f"const int PALETTES = {len(rows)};\n"
            "const vec3 STOPS[PALETTES * STOP_COUNT] = vec3[PALETTES * STOP_COUNT](\n"
            + ",\n".join(rows) + "\n);\n"
            "vec3 paletteStop(int palette, int stop) { return STOPS[palette * STOP_COUNT + stop]; }"
        )
        source = source.replace("@@PALETTE_GLSL@@", table)
    if "@@PALETTE_HIGHLIGHTS@@" in source:
        stops = ", ".join(map(str, PALETTE_HIGHLIGHT_STOPS.values()))
        source = source.replace(
            "@@PALETTE_HIGHLIGHTS@@",
            f"const int HIGHLIGHTS[PALETTES] = int[PALETTES]({stops});\n"
            "vec3 paletteHighlight(int palette) { return paletteStop(palette, HIGHLIGHTS[palette]); }",
        )
    source = source.replace("@@PALETTE_CYCLE@@", """vec3 paletteCycle(int palette, float phase)
{
    float position = fract(phase) * float(STOP_COUNT);
    int stop = int(position);
    return mix(paletteStop(palette, stop), paletteStop(palette, (stop + 1) % STOP_COUNT), fract(position));
}""")
    for key, declaration in declarations.items():
        source = source.replace(f"@@{key}@@", declaration)
    return source


def palette_effect(source, **values):
    for key, value in values.items():
        source = source.replace(f"@@{key}@@", pformat(value, sort_dicts=False))
    return source


def add_control_page(block, defaults):
    page = block.appendCustomPage("Control")
    add_float(page, "Trigger", "Trigger", 0.0, 0.0, 1.0)
    add_menu(page, "Mode", "Mode", ["hit", "gate"], ["Hit", "Gate"], defaults["mode"])
    add_menu(page, "Retrigger", "Retrigger", ["restart", "ignore"], ["Restart", "Ignore"], "restart")
    add_float(page, "Attack", "Attack (s)", defaults["attack"], 0.0, 1.0, clamp_low=True)
    add_float(page, "Hold", "Hold (s, Hit mode)", defaults["hold"], 0.0, 2.0, clamp_low=True)
    add_float(page, "Release", "Release (s)", defaults["release"], 0.0, 2.0, clamp_low=True)
    add_float(page, "Curve", "Release Curve", defaults["curve"], 0.25, 6.0, clamp_low=True)
    add_float(page, "Velocitydepth", "Velocity Depth", defaults["velocitydepth"], 0.0, 1.0, True, True)
    add_float(page, "Maxhold", "Max Hold (s)", 30.0, 0.0, 120.0, clamp_low=True)
    page.appendPulse("Reset", label="Reset")


def add_text(block, name, source, x, y):
    dat = block.create(textDAT, name)
    dat.text = source
    dat.nodeX = x
    dat.nodeY = y
    return dat


def add_callbacks(block):
    source = ENGINE_SOURCE
    for name, value in INTEGRATION.items():
        source = source.replace(f"'@@{name}@@'", repr(value))
    add_text(block, "engine", source, -300, -300)

    tick = block.create(executeDAT, "tick")
    tick.text = TICK_SOURCE
    tick.par.framestart = True
    tick.par.active = True
    tick.nodeX = -100
    tick.nodeY = -300

    reset_watch = block.create(parameterexecuteDAT, "reset_watch")
    reset_watch.text = RESET_SOURCE
    reset_watch.par.op = ".."
    reset_watch.par.pars = "Reset"
    reset_watch.par.custom = True
    reset_watch.par.builtin = False
    reset_watch.par.valuechange = False
    reset_watch.par.onpulse = True
    reset_watch.par.active = True
    reset_watch.nodeX = 100
    reset_watch.nodeY = -300

    if not INTEGRATION["SCENE_TAG"]:
        return

    # The engine finds the enclosing tagged scene after the block is placed.
    scene_watch = block.create(parameterexecuteDAT, "scene_watch")
    scene_watch.text = RESET_SOURCE
    scene_watch.par.pars = "Init"
    scene_watch.par.custom = True
    scene_watch.par.builtin = False
    scene_watch.par.valuechange = False
    scene_watch.par.onpulse = True
    scene_watch.par.active = True
    scene_watch.nodeX = 300
    scene_watch.nodeY = -300


def new_block(library, name):
    existing = library.op(name)
    if existing is not None:
        existing.destroy()
    block = library.create(baseCOMP, name)
    block.viewer = True
    return block


def add_top(block, op_type, name, x):
    node = block.create(op_type, name)
    node.nodeX = x
    node.nodeY = 0
    return node


def build_library(builds, output_dir):
    """Replace only the given blocks, then lay the library out by name."""
    output_dir.mkdir(parents=True, exist_ok=True)
    root = op("/")
    library = root.op(LIBRARY_NAME)
    if library is None:
        library = root.create(baseCOMP, LIBRARY_NAME)

    for builder in builds:
        block = builder(library)
        target = (output_dir / f"{block.name}.tox").as_posix()
        block.save(target)
        print("  saved " + target)

    for index, block in enumerate(sorted(library.children, key=lambda child: child.name)):
        block.nodeX = index * 220

    print("VJ blocks built in /" + LIBRARY_NAME)
