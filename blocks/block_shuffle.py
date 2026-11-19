# Block Shuffle: hold, the wall snaps into butted rectangles that trade pieces of the picture,
# then on release the blocks cut out smallest first back to the live video.
#
# Color Block Slam's layout and timing with the video itself in place of brand color. Three passes
# keep the full wall cheap. "layout" has no inputs and draws, once per press, which block every point
# belongs to and how far it sits from a seam. "table" is one pixel per block holding where that block
# reads the picture from, so Scatter and Flip can be played live while the key is held for the cost
# of a 24 x 1 redraw. "shuffle" looks both up at full size, so its cost doesn't depend on block count.

SLOT_COUNT = 24
LAYOUT_UNIFORMS = ["uRect%d" % i for i in range(SLOT_COUNT)] + ["uData%d" % i for i in range(SLOT_COUNT)]
TABLE_UNIFORMS = ["uMap%d" % i for i in range(SLOT_COUNT)]
UNIFORMS = ["uMotion", "uCut", "uLook"]

LAYOUT_TEMPLATE = r'''
// Each rect is left, bottom, right, top in uv. A zero-size rect is an empty slot.
// Each data is cut time. Output is slot, cut time, and the distance in uv to the nearest
// seam across and up. The wall's own border is not a seam.
@@DECLARATIONS@@

layout(location = 0) out vec4 fragColor;

const float FAR = 1.0;

bool inside(vec4 rect)
{
    // Half-open, so a pixel on a seam belongs to exactly one block.
    return vUV.s >= rect.x && vUV.s < rect.z && vUV.t >= rect.y && vUV.t < rect.w;
}

vec4 packed(float slot, vec4 rect, vec4 data)
{
    vec2 low = vUV.st - rect.xy;
    vec2 high = rect.zw - vUV.st;
    float across = min(rect.x > 0.0 ? low.x : FAR, rect.z < 1.0 ? high.x : FAR);
    float up = min(rect.y > 0.0 ? low.y : FAR, rect.w < 1.0 ? high.y : FAR);
    return vec4(slot, data.x, across, up);
}

void main()
{
@@TESTS@@
    fragColor = vec4(0.0, 0.0, FAR, FAR);
}
'''

LAYOUT_SHADER = (
    LAYOUT_TEMPLATE
    .replace("@@DECLARATIONS@@", "\n".join("uniform vec4 %s;" % name for name in LAYOUT_UNIFORMS))
    .replace("@@TESTS@@", "\n".join(
        "    if (inside(uRect%d)) { fragColor = packed(%d.0, uRect%d, uData%d); return; }" % (i, i, i, i)
        for i in range(SLOT_COUNT)
    ))
)

TABLE_TEMPLATE = r'''
// One pixel per block: scale x, scale y, offset x, offset y. The block shows the picture
// at uv * scale + offset, so an untouched block is 1, 1, 0, 0.
@@DECLARATIONS@@

layout(location = 0) out vec4 fragColor;

void main()
{
    int slot = int(gl_FragCoord.x);
@@TESTS@@
    fragColor = vec4(1.0, 1.0, 0.0, 0.0);
}
'''

TABLE_SHADER = (
    TABLE_TEMPLATE
    .replace("@@DECLARATIONS@@", "\n".join("uniform vec4 %s;" % name for name in TABLE_UNIFORMS))
    .replace("@@TESTS@@", "\n".join(
        "    if (slot == %d) { fragColor = uMap%d; return; }" % (i, i) for i in range(SLOT_COUNT)
    ))
)

SHADER = r'''
uniform vec4 uMotion;   // warp x, warp y, wave x, wave y
uniform vec4 uCut;      // how far the cut-out has run
uniform vec4 uLook;     // line half width in wall heights, mix

layout(location = 0) out vec4 fragColor;

const float TAU = 6.2831853;

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

    vec2 size = vec2(textureSize(sTD2DInputs[1], 0));
    vec2 lookup = clamp(vec2(bent(vUV.s, uMotion.x, uMotion.z), bent(vUV.t, uMotion.y, uMotion.w)), 0.0, 1.0);
    // texelFetch keeps seams hard. The seam distances are read filtered, so lines stay sharp at full size.
    vec4 cell = texelFetch(sTD2DInputs[1], ivec2(min(lookup * size, size - 1.0)), 0);

    if (uCut.x < cell.g) {
        vec4 map = texelFetch(sTD2DInputs[2], ivec2(int(cell.r + 0.5), 0), 0);
        color = texture(sTD2DInputs[0], mirrored(vUV.st * map.xy + map.zw)).rgb;
        if (uLook.x > 0.0) {
            vec2 wall = vec2(textureSize(sTD2DInputs[0], 0));
            vec2 apart = texture(sTD2DInputs[1], lookup).ba;
            float seam = min(apart.x * wall.x / wall.y, apart.y);
            float soft = fwidth(seam);
            color *= smoothstep(uLook.x - soft, uLook.x + soft, seam);
        }
    }

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uLook.y), source.a));
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
SOFT_SCATTER = 0.5      # share of Scatter a feather-light press still gets
HARMONICS = (1, 2, 3)
LAST_STANDING = 0.3     # share of the release the biggest block has the wall to itself
MIN_SCALE = 0.5         # a small piece shown in a big block is magnified at most 2x
MAX_SCALE = 2.0
LINE_MAX = 0.008        # line half width at full Ink, in wall heights
FLIPS = ((-1.0, 1.0), (1.0, -1.0), (-1.0, -1.0))
UNTOUCHED = (1.0, 1.0, 0.0, 0.0)
EMPTY = (0.0, 0.0, 0.0, 0.0)

_rng = random.Random()
_hit = None
_written = {}


def reset(block):
    global _hit, _written
    _hit = None
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


def _cut_times(rects):
    # Smallest goes first, and the biggest is left alone until the block returns to idle.
    count = len(rects)
    ranked = sorted(range(count), key=lambda i: _area(rects[i]))
    times = [2.0] * count
    for place_in_line, index in enumerate(ranked[:-1]):
        times[index] = (place_in_line + 1.0) / max(1, count - 1) * (1.0 - LAST_STANDING)
    return times


def _roll(block, velocity):
    # The layout and the order pieces get picked in are fixed for the life of a press.
    # How many of them swap or flip is read live, so Scatter and Flip can be played while held.
    node = op("in1")
    aspect = max(0.01, float(node.width) / max(1.0, float(node.height)))
    most = MOST_MIN + int(round(_clamped(block.par.Blocks) * MOST_RANGE))
    count = min(SLOTS, FEWEST + int(round((most - FEWEST) * velocity)))
    rects = _layout(count, aspect)
    order = list(range(len(rects)))
    _rng.shuffle(order)
    strength = _clamped(block.par.Spring) * WARP_MAX * (SOFT_WARP + (1.0 - SOFT_WARP) * velocity)
    return {
        "rects": rects,
        "cuts": _cut_times(rects),
        "order": order,
        "flip_roll": [_rng.random() for _ in rects],
        "flip_axes": [_rng.choice(FLIPS) for _ in rects],
        "velocity": velocity,
        "warp_x": strength * _rng.choice((-1.0, 1.0)),
        "warp_y": strength * _rng.choice((-1.0, 1.0)),
        "wave_x": _rng.choice(HARMONICS),
        "wave_y": _rng.choice(HARMONICS),
        "started": absTime.seconds,
    }


def _sources(hit, scatter):
    # The first few blocks in the press's order pass their pieces one along, in a ring,
    # so nobody keeps their own. Turning Scatter up adds blocks to the end of the ring.
    count = len(hit["rects"])
    share = scatter * (SOFT_SCATTER + (1.0 - SOFT_SCATTER) * hit["velocity"])
    swapped = int(round(share * count))
    if share > 0.0:
        swapped = max(2, swapped)
    swapped = min(count, swapped)
    sources = list(range(count))
    ring = hit["order"][:swapped]
    for place, index in enumerate(ring):
        sources[index] = ring[(place + 1) % len(ring)]
    return sources


def _map(dest, source, flip):
    # Shows the source block's piece of the picture in this block, cropped to this block's
    # shape without stretching. Returns scale x, scale y, offset x, offset y.
    dest_w, dest_h = dest[2] - dest[0], dest[3] - dest[1]
    source_w, source_h = source[2] - source[0], source[3] - source[1]
    scale = max(MIN_SCALE, min(MAX_SCALE, min(source_w / dest_w, source_h / dest_h)))
    scale_x, scale_y = scale * flip[0], scale * flip[1]
    dest_x, dest_y = (dest[0] + dest[2]) * 0.5, (dest[1] + dest[3]) * 0.5
    source_x, source_y = (source[0] + source[2]) * 0.5, (source[1] + source[3]) * 0.5
    return (scale_x, scale_y, source_x - dest_x * scale_x, source_y - dest_y * scale_y)


def _table_entries(hit, scatter, flip):
    rects = hit["rects"]
    sources = _sources(hit, scatter)
    entries = [UNTOUCHED] * SLOTS
    for index, rect in enumerate(rects):
        axes = hit["flip_axes"][index] if hit["flip_roll"][index] < flip else (1.0, 1.0)
        if sources[index] == index and axes == (1.0, 1.0):
            continue
        entries[index] = _map(rect, rects[sources[index]], axes)
    return entries


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
        entries[SLOTS + index] = (hit["cuts"][index], 0.0, 0.0, 0.0)
    return entries


def apply(block, env):
    global _hit
    switch = op("switch")
    shuffle = op("shuffle")
    if env.stage == "idle":
        switch.par.index = 0
        _hit = None
        _send(op("layout"), [EMPTY] * (SLOTS * 2))
        _send(op("table"), [UNTOUCHED] * SLOTS)
        _send(shuffle, [EMPTY] * 3)
        return

    switch.par.index = 1
    if env.started or _hit is None:
        _hit = _roll(block, env.velocity)
        # The only time the layout pass is asked to redraw.
        _send(op("layout"), _layout_entries(_hit))

    _send(op("table"), _table_entries(_hit, _clamped(block.par.Scatter), _clamped(block.par.Flip)))

    shaped = 0.0
    if env.stage == "release":
        shaped = 1.0 - (1.0 - env.progress) ** max(0.01, block.par.Curve.eval())

    settle = _settle(absTime.seconds - _hit["started"])
    _send(shuffle, [
        (_hit["warp_x"] * settle, _hit["warp_y"] * settle, float(_hit["wave_x"]), float(_hit["wave_y"])),
        (shaped, 0.0, 0.0, 0.0),
        (LINE_MAX * _clamped(block.par.Ink), _clamped(block.par.Mix), 0.0, 0.0),
    ])
'''


def _sequence(node, names):
    # Vectors is a sequence: entries past the first don't exist until it grows.
    node.seq.vec.numBlocks = len(names)
    for index, name in enumerate(names):
        setattr(node.par, "vec%dname" % index, name)


def build(library):
    block = new_block(library, "block_shuffle")
    # A hold: the shuffle stays up while the key is down. Release is how long the blocks take to
    # cut out, and Curve above 1 starts them sooner.
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.2, "release": 0.5, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Block Shuffle")
    add_float(page, "Blocks", "Most Blocks", 0.8, 0.0, 1.0, True, True)
    add_float(page, "Scatter", "Scatter", 0.8, 0.0, 1.0, True, True)
    add_float(page, "Flip", "Flip", 0.25, 0.0, 1.0, True, True)
    add_float(page, "Spring", "Landing Spring", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Ink", "Ink", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "layout_shader", LAYOUT_SHADER, -500, 300)
    add_text(block, "table_shader", TABLE_SHADER, -500, -150)
    add_text(block, "shuffle_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -700)

    # Neither of these has inputs, on purpose: with nothing feeding them they only cook when
    # their uniforms change.
    layout = add_top(block, glslTOP, "layout", -500)
    layout.nodeY = 150
    layout.par.pixeldat = "layout_shader"
    layout.par.outputresolution = "custom"
    layout.par.resolutionw = 1680
    layout.par.resolutionh = 512
    layout.par.format = "rgba32float"
    _sequence(layout, LAYOUT_UNIFORMS)

    table = add_top(block, glslTOP, "table", -500)
    table.par.pixeldat = "table_shader"
    table.par.outputresolution = "custom"
    table.par.resolutionw = SLOT_COUNT
    table.par.resolutionh = 1
    table.par.format = "rgba32float"
    _sequence(table, TABLE_UNIFORMS)

    shuffle = add_top(block, glslTOP, "shuffle", -300)
    shuffle.par.pixeldat = "shuffle_shader"
    shuffle.par.outputresolution = "useinput"
    shuffle.par.format = "useinput"
    shuffle.par.inputfiltertype = "linear"
    _sequence(shuffle, UNIFORMS)

    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    shuffle.inputConnectors[0].connect(source)
    shuffle.inputConnectors[1].connect(layout)
    shuffle.inputConnectors[2].connect(table)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(shuffle)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
