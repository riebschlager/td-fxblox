# kit.py
# Builder helpers shared by every block. build.py runs this inside TouchDesigner
# and supplies ENGINE_SOURCE and INTEGRATION. Blocks use these helpers
# directly, without importing anything.

LIBRARY_NAME = "vj_block_library"

# Five 8-bit RGB stops per palette, in menu order. The Matplotlib maps are sampled
# at quarter intervals. ColorBrewer maps use its published five-class colors, with
# the two sequential maps ordered from dark to light.
# https://matplotlib.org/stable/gallery/color/colormap_reference.html
# https://colorbrewer2.org/export/colorbrewer.json
GRADIENT_PALETTES = {
    "Viridis":   "440154 3b528b 21918c 5ec962 fde725",
    "Plasma":    "0d0887 7e03a8 cc4778 f89540 f0f921",
    "Inferno":   "000004 57106e bc3754 f98e09 fcffa4",
    "Magma":     "000004 51127c b73779 fc8961 fcfdbf",
    "Cividis":   "00224e 434e6c 7d7c78 bcae6c fee838",
    "Turbo":     "30123b 28bceb a4fc3c fb7e21 7a0403",
    "Spectral":  "d7191c fdae61 ffffbf abdda4 2b83ba",
    "RdBu":      "ca0020 f4a582 f7f7f7 92c5de 0571b0",
    "PuOr":      "e66101 fdb863 f7f7f7 b2abd2 5e3c99",
    "PiYG":      "d01c8b f1b6da f7f7f7 b8e186 4dac26",
    "YlGnBu":    "253494 2c7fb8 41b6c4 a1dab4 ffffcc",
    "YlOrRd":    "bd0026 f03b20 fd8d3c fecc5c ffffb2",
}
GRADIENT_PALETTE_INDEX = {label.lower(): index for index, label in enumerate(GRADIENT_PALETTES)}

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


def add_gradient_palette_menu(page):
    names = list(GRADIENT_PALETTE_INDEX)
    add_menu(page, "Palette", "Palette", names, list(GRADIENT_PALETTES), names[0])


def gradient_palette_glsl():
    rows = []
    for colors in GRADIENT_PALETTES.values():
        stops = []
        for color in colors.split():
            channels = ", ".join(f"{channel}.0" for channel in bytes.fromhex(color))
            stops.append(f"vec3({channels}) / 255.0")
        rows.append("    " + ", ".join(stops))
    return (
        "const int STOP_COUNT = 5;\n"
        f"const int PALETTES = {len(rows)};\n"
        "const vec3 STOPS[PALETTES * STOP_COUNT] = vec3[PALETTES * STOP_COUNT](\n"
        + ",\n".join(rows) + "\n);"
    )


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
