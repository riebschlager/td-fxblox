# Color Block Slam: stateless hit, the wall snaps into butted brand-color rectangles
# with the video printed in black ink, then the blocks cut out smallest first.
#
# Built to stay cheap on the full wall. A small pass with no video input draws the
# layout once per hit, and it holds everything that varies per block. The full-size
# pass only looks the block up, so its cost doesn't depend on how many blocks there are.

SLOT_COUNT = 24
LAYOUT_UNIFORMS = ["uRect%d" % i for i in range(SLOT_COUNT)] + ["uData%d" % i for i in range(SLOT_COUNT)]
UNIFORMS = ["uMotion", "uCut", "uLook"]

FIELD_SHADER = r'''
// A coarse map of the picture's average brightness, for thresholding against local contrast.
layout(location = 0) out vec4 fragColor;

const int TAPS = 8;
const vec3 LUMA = vec3(0.299, 0.587, 0.114);
const vec2 CELL = vec2(1.0 / 48.0, 1.0 / 15.0);     // one pixel of this pass

void main()
{
    float sum = 0.0;
    for (int y = 0; y < TAPS; y++) {
        for (int x = 0; x < TAPS; x++) {
            vec2 offset = (vec2(x, y) + 0.5) / float(TAPS) - 0.5;
            sum += dot(texture(sTD2DInputs[0], vUV.st + offset * CELL).rgb, LUMA);
        }
    }
    fragColor = vec4(vec3(sum / float(TAPS * TAPS)), 1.0);
}
'''

LAYOUT_TEMPLATE = r'''
// Each rect is left, bottom, right, top in uv. A zero-size rect is an empty slot.
// Each data is color index, cut time. Output is color index, cut time, block center.
@@DECLARATIONS@@

layout(location = 0) out vec4 fragColor;

bool inside(vec4 rect)
{
    // Half-open, so a pixel on a seam belongs to exactly one block.
    return vUV.s >= rect.x && vUV.s < rect.z && vUV.t >= rect.y && vUV.t < rect.w;
}

vec4 packed(vec4 rect, vec4 data)
{
    return vec4(data.x, data.y, (rect.xy + rect.zw) * 0.5);
}

void main()
{
@@TESTS@@
    fragColor = vec4(0.0);
}
'''

LAYOUT_SHADER = (
    LAYOUT_TEMPLATE
    .replace("@@DECLARATIONS@@", "\n".join("uniform vec4 %s;" % name for name in LAYOUT_UNIFORMS))
    .replace("@@TESTS@@", "\n".join(
        "    if (inside(uRect%d)) { fragColor = packed(uRect%d, uData%d); return; }" % (i, i, i)
        for i in range(SLOT_COUNT)
    ))
)

SHADER = r'''
uniform vec4 uMotion;   // warp x, warp y, wave x, wave y
uniform vec4 uCut;      // cut-out progress, palette (0 is brand)
uniform vec4 uLook;     // seed, scatter, ink bias, mix

layout(location = 0) out vec4 fragColor;

const vec3 LUMA = vec3(0.299, 0.587, 0.114);
const float TAU = 6.2831853;
const float MAX_ZOOM = 1.2;
const float MAX_SHIFT = 0.3;
const float BLACK_FLOOR = 0.02;     // below this average there is only sensor noise to ink
@@SWATCHES@@
@@PALETTE_GLSL@@

float hash(float n)
{
    n = fract(n * 0.1031);
    n *= n + 33.33;
    n *= n + n;
    return fract(n);
}

vec2 mirrored(vec2 place)
{
    return abs(mod(place + 1.0, 2.0) - 1.0);
}

// Bends one axis. It keeps the ends fixed and never reorders seams while the amount is under 1.
float bent(float value, float amount, float wave)
{
    float turn = TAU * wave;
    return value - amount * sin(turn * value) / turn;
}

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec3 color = source.rgb;

    vec2 size = vec2(textureSize(sTD2DInputs[2], 0));
    vec2 lookup = clamp(vec2(bent(vUV.s, uMotion.x, uMotion.z), bent(vUV.t, uMotion.y, uMotion.w)), 0.0, 1.0);
    // texelFetch keeps seams hard while this pass filters the video and the brightness map.
    vec4 cell = texelFetch(sTD2DInputs[2], ivec2(min(lookup * size, size - 1.0)), 0);

    if (uCut.x < cell.g) {
        vec2 place = vUV.st;
        vec3 picture = source.rgb;
        // Scatter 0 keeps one continuous picture and skips the extra video read.
        if (uLook.y > 0.0) {
            float key = cell.b * 97.0 + cell.a * 53.0 + uLook.x;
            float zoom = 1.0 + uLook.y * MAX_ZOOM * hash(key + 7.3);
            vec2 shift = uLook.y * MAX_SHIFT * (vec2(hash(key + 11.7), hash(key + 53.1)) * 2.0 - 1.0);
            place = mirrored(cell.ba + (vUV.st - cell.ba) / zoom + shift);
            picture = texture(sTD2DInputs[0], place).rgb;
        }
        float luma = dot(picture, LUMA);
        float local = texture(sTD2DInputs[1], place).r;
        // Thresholding against the neighborhood means dark clips still draw. Black or
        // flat areas fall through to pure color, which is still color blocking.
        bool ink = local > BLACK_FLOOR && luma < local * (1.0 + uLook.z);
        int stop = int(cell.r + 0.5);
        int palette = int(uCut.y) - 1;
        color = ink ? vec3(0.0) : (palette < 0 ? PALETTE[stop] : paletteStop(palette, stop));
    }

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uLook.w), source.a));
}
'''

EFFECT_SOURCE = r'''
import math
import random

SLOTS = 24
FEWEST = 3
MOST_MIN = 4
MOST_RANGE = 20
MIN_SIDE = 0.12         # wall heights
SPLIT_MARGIN = 0.32     # no cut closer to a block's edge than this share of its side
SQUARE_ENOUGH = 1.25    # below this ratio a block may split either way
AREA_POWER = 1.5        # above 1 favors cutting big blocks, leaving a mix of sizes
LANDING_SECONDS = 0.14
WARP_MAX = 0.9          # must stay under 1 so seams can never pass each other
SOFT_WARP = 0.3
HARMONICS = (1, 2, 3)
LAST_STANDING = 0.3     # share of the release the biggest block has the wall to itself
INK_LOW = -0.6
INK_RANGE = 0.8
SEED_RANGE = 100.0
MAX_PAINT_STEPS = 400
PALETTE_INDEX = @@PALETTE_INDEX@@
COLOR_COUNT = @@STOP_COUNT@@
EMPTY = (0.0, 0.0, 0.0, 0.0)

_rng = random.Random()
_hit = None
_previous = []
_written = {}


def reset(block):
    global _hit, _previous, _written
    _hit = None
    _previous = []
    _written = {}


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _send(node, entries):
    # Only touch what changed, because every write makes the pass cook again.
    sent = _written.setdefault(node.name, [None] * len(entries))
    par = node.par
    for index, entry in enumerate(entries):
        if entry != sent[index]:
            setattr(par, "vec%dvaluex" % index, entry[0])
            setattr(par, "vec%dvaluey" % index, entry[1])
            setattr(par, "vec%dvaluez" % index, entry[2])
            setattr(par, "vec%dvaluew" % index, entry[3])
            sent[index] = entry


def _area(rect):
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


def _sides(rect, aspect):
    return (rect[2] - rect[0]) * aspect, rect[3] - rect[1]


def _layout(count, aspect):
    # Guillotine cuts: every split runs the full length of its block, so blocks tile exactly.
    rects = [(0.0, 0.0, 1.0, 1.0)]
    first = True
    while len(rects) < count:
        open_rects = [r for r in rects if max(_sides(r, aspect)) >= 2.0 * MIN_SIDE]
        if not open_rects:
            break
        weights = [_area(r) ** AREA_POWER for r in open_rects]
        rect = _rng.choices(open_rects, weights)[0]
        wide, tall = _sides(rect, aspect)

        across = wide >= tall
        if first:
            across = wide >= 2.0 * MIN_SIDE or across
        elif max(wide, tall) / max(min(wide, tall), 1e-6) < SQUARE_ENOUGH and min(wide, tall) >= 2.0 * MIN_SIDE:
            across = _rng.random() < 0.5
        first = False

        side = wide if across else tall
        margin = max(SPLIT_MARGIN, MIN_SIDE / side)
        cut = _rng.uniform(margin, 1.0 - margin)
        rects.remove(rect)
        x0, y0, x1, y1 = rect
        if across:
            seam = x0 + (x1 - x0) * cut
            rects += [(x0, y0, seam, y1), (seam, y0, x1, y1)]
        else:
            seam = y0 + (y1 - y0) * cut
            rects += [(x0, y0, x1, seam), (x0, seam, x1, y1)]
    return rects


def _touching(a, b):
    # Sharing a stretch of edge counts. Meeting only at a corner does not.
    if a[2] == b[0] or b[2] == a[0]:
        return min(a[3], b[3]) > max(a[1], b[1])
    if a[3] == b[1] or b[3] == a[1]:
        return min(a[2], b[2]) > max(a[0], b[0])
    return False


def _color_before(rect):
    x = (rect[0] + rect[2]) * 0.5
    y = (rect[1] + rect[3]) * 0.5
    for old, color in _previous:
        if old[0] <= x < old[2] and old[1] <= y < old[3]:
            return color
    return None


def _paint(rects):
    # Returns None if the search runs long, so one hit can never stall the frame.
    # Four colors always suffice for flat rectangles and there are five.
    count = len(rects)
    neighbors = [[j for j in range(count) if j != i and _touching(rects[i], rects[j])] for i in range(count)]
    biggest = max(range(count), key=lambda i: _area(rects[i]))
    before = [_color_before(rect) for rect in rects]
    paint = [None] * count
    paint[biggest] = 0
    steps = [0]

    def taken(index):
        return {paint[j] for j in neighbors[index] if paint[j] is not None}

    def place(remaining):
        if remaining == 0:
            return True
        steps[0] += 1
        if steps[0] > MAX_PAINT_STEPS:
            return False
        # The block with the fewest colors left goes next, which avoids dead ends.
        index = max(
            (i for i in range(count) if paint[i] is None),
            key=lambda i: (len(taken(i)), len(neighbors[i])),
        )
        options = [c for c in range(COLOR_COUNT) if c not in taken(index)]
        _rng.shuffle(options)
        # Prefer a color this part of the wall did not just have. The neighbor rule still wins.
        options.sort(key=lambda c: c == before[index])
        for color in options:
            paint[index] = color
            if place(remaining - 1):
                return True
        paint[index] = None
        return False

    return paint if place(count - 1) else None


def _cut_times(rects):
    # Smallest goes first, and the biggest is left alone until the block returns to idle.
    count = len(rects)
    ranked = sorted(range(count), key=lambda i: _area(rects[i]))
    times = [2.0] * count
    for place_in_line, index in enumerate(ranked[:-1]):
        times[index] = (place_in_line + 1.0) / max(1, count - 1) * (1.0 - LAST_STANDING)
    return times


def _roll(block, velocity):
    global _previous
    # The layout is fixed for the life of a hit.
    node = op("in1")
    aspect = max(0.01, float(node.width) / max(1.0, float(node.height)))
    most = MOST_MIN + int(round(_clamped(block.par.Blocks) * MOST_RANGE))
    count = min(SLOTS, FEWEST + int(round((most - FEWEST) * velocity)))
    rects = _layout(count, aspect)
    paint = _paint(rects)
    while paint is None:
        rects = _layout(count, aspect)
        paint = _paint(rects)
    _previous = list(zip(rects, paint))
    strength = _clamped(block.par.Spring) * WARP_MAX * (SOFT_WARP + (1.0 - SOFT_WARP) * velocity)
    return {
        "rects": rects,
        "paint": paint,
        "cuts": _cut_times(rects),
        "seed": _rng.random() * SEED_RANGE,
        "warp_x": strength * _rng.choice((-1.0, 1.0)),
        "warp_y": strength * _rng.choice((-1.0, 1.0)),
        "wave_x": _rng.choice(HARMONICS),
        "wave_y": _rng.choice(HARMONICS),
        "started": absTime.seconds,
    }


def _settle(elapsed):
    # 1 on the slam, swings past home, and is exactly 0 once landed.
    t = elapsed / LANDING_SECONDS
    if t >= 1.0:
        return 0.0
    return (1.0 - t) ** 3 * math.cos(t * math.pi * 3.0)


def _layout_entries(hit):
    entries = [EMPTY] * (SLOTS * 2)
    for index, rect in enumerate(hit["rects"]):
        entries[index] = tuple(rect)
        entries[SLOTS + index] = (float(hit["paint"][index]), hit["cuts"][index], 0.0, 0.0)
    return entries


def apply(block, env):
    global _hit
    switch = op("switch")
    slam = op("slam")
    if env.stage == "idle":
        switch.par.index = 0
        _hit = None
        _send(op("layout"), [EMPTY] * (SLOTS * 2))
        _send(slam, [EMPTY] * 3)
        return

    switch.par.index = 1
    if env.started or _hit is None:
        _hit = _roll(block, env.velocity)
        # The only time the layout pass is asked to redraw.
        _send(op("layout"), _layout_entries(_hit))

    shaped = 0.0
    if env.stage == "release":
        shaped = 1.0 - (1.0 - env.progress) ** max(0.01, block.par.Curve.eval())

    settle = _settle(absTime.seconds - _hit["started"])
    _send(slam, [
        (_hit["warp_x"] * settle, _hit["warp_y"] * settle, float(_hit["wave_x"]), float(_hit["wave_y"])),
        (shaped, PALETTE_INDEX.get(block.par.Palette.eval(), -1) + 1, 0.0, 0.0),
        (
            _hit["seed"],
            _clamped(block.par.Scatter),
            INK_LOW + INK_RANGE * _clamped(block.par.Ink),
            _clamped(block.par.Mix),
        ),
    ])
'''


def _sequence(node, names):
    # Vectors is a sequence: entries past the first don't exist until it grows.
    node.seq.vec.numBlocks = len(names)
    for index, name in enumerate(names):
        setattr(node.par, "vec%dname" % index, name)


def build(library):
    block = new_block(library, "color_block_slam")
    # Release is how long the blocks take to cut out. Curve above 1 starts them sooner.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.2, "release": 0.5, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Color Block Slam")
    add_float(page, "Blocks", "Most Blocks", 0.8, 0.0, 1.0, True, True)
    add_float(page, "Spring", "Landing Spring", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Ink", "Ink", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Scatter", "Scatter", 0.0, 0.0, 1.0, True, True)
    add_palette_menu(page, presets={"brand": "Brand"}, default="brand")
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "field_shader", FIELD_SHADER, -500, -150)
    add_text(block, "layout_shader", LAYOUT_SHADER, -500, 300)
    shader = palette_shader(SHADER, SWATCHES=glsl_swatches("PALETTE", BRAND_COLORS))
    add_text(block, "slam_shader", shader, -300, -150)

    source = add_top(block, inTOP, "in1", -700)
    field = add_top(block, glslTOP, "field", -500)
    field.par.pixeldat = "field_shader"
    field.par.outputresolution = "custom"
    field.par.resolutionw = 48
    field.par.resolutionh = 15
    field.par.format = "rgba32float"
    field.par.inputfiltertype = "linear"

    # No inputs on purpose: with nothing feeding it, it only cooks when a new layout arrives.
    layout = add_top(block, glslTOP, "layout", -500)
    layout.nodeY = 150
    layout.par.pixeldat = "layout_shader"
    layout.par.outputresolution = "custom"
    layout.par.resolutionw = 1680
    layout.par.resolutionh = 512
    layout.par.format = "rgba32float"
    _sequence(layout, LAYOUT_UNIFORMS)

    slam = add_top(block, glslTOP, "slam", -300)
    slam.par.pixeldat = "slam_shader"
    slam.par.outputresolution = "useinput"
    slam.par.format = "useinput"
    slam.par.inputfiltertype = "linear"
    _sequence(slam, UNIFORMS)

    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(field)
    slam.inputConnectors[0].connect(source)
    slam.inputConnectors[1].connect(field)
    slam.inputConnectors[2].connect(layout)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(slam)
    switch.outputConnectors[0].connect(output)

    effect = palette_effect(EFFECT_SOURCE, PALETTE_INDEX=PALETTE_INDEX, STOP_COUNT=STOP_COUNT)
    add_text(block, "effect", effect, -500, -300)
    add_callbacks(block)
    return block
