# Fault Plates: tail hit, jagged cracks break the wall into plates that jolt apart in aftershocks
#
# Two passes keep the full-size wall affordable. "layout" works out the plates at reduced
# resolution (the Detail menu): for every point, how far its picture slid and how far it sits from
# the nearest plate edge and shadow edge. "plates" then samples the picture at full resolution and
# redraws the edges from those distances, so the picture and the ink stay sharp.

LAYOUT_SHADER = r'''
uniform vec4 uPlates;  // plate size and how far the break reaches (both in wall heights), seed, jag
uniform vec4 uMotion;  // spread (of a plate's size), tilt (radians, signed), unused, unused
uniform vec4 uOrigin;  // where the break starts, in UV

layout(location = 0) out vec4 fragColor;

const float JITTER = 0.6;         // how far a plate's center wanders inside its grid cell
const float FALLOFF = 0.7;        // plates at the edge of the break move this much less
const float OUTWARD = 0.6;        // pull away from the origin, added to each plate's random heave
const float COARSE_RUN = 0.55;    // length of the cracks' segments, in plate sizes
const float FINE_RUN = 0.13;
const float COARSE_SWING = 0.13;
const float FINE_SWING = 0.05;
const float SHADOW_DROP = 0.55;   // of the spread, how far a plate's shadow falls
const float SHADOW_SLIDE = 0.35;  // of the spread, how far that shadow shifts sideways
const float REACH_CELLS = 1.2;    // no point of a plate is farther than this from its center, in plate sizes
const float FAR = 1.0;            // distances are clamped to this, in wall heights

float hash(float n)
{
    n = fract(n * 0.1031);
    n *= n + 33.33;
    n *= n + n;
    return fract(n);
}

float cellHash(vec2 c, float k)
{
    return hash(dot(c, vec2(157.0, 113.0)) + k + uPlates.z);
}

// Straight runs between random points, so the cracks stay angular like cut paper.
float zig(float x, float seed)
{
    float cell = floor(x);
    return mix(hash(cell + seed), hash(cell + 1.0 + seed), x - cell) * 2.0 - 1.0;
}

float jagged(float t, float seed)
{
    return COARSE_SWING * zig(t / COARSE_RUN, seed) + FINE_SWING * zig(t / FINE_RUN, seed + 17.0);
}

// Bends the straight plate edges into jagged ones. Every plate uses the same bend, so they still tile.
vec2 warp(vec2 q)
{
    float size = uPlates.x;
    float seed = uPlates.z;
    vec2 t = q / size;
    return q + uPlates.w * size * vec2(jagged(t.y, seed), jagged(t.x, seed + 5.3));
}

// Distance inside plate a, measured from the edge it shares with plate b.
float side(vec2 w, vec2 a, vec2 b)
{
    vec2 d = a - b;
    return dot(w - 0.5 * (a + b), d) * inversesqrt(dot(d, d));
}

// Plate centers are recomputed from their cell whenever they're needed. Hashing is cheap, while a
// lookup table indexed inside a loop that breaks early spills out of registers and costs far more.
vec2 centerOf(vec2 cell)
{
    return (cell + 0.5 + JITTER * (vec2(cellHash(cell, 1.3), cellHash(cell, 7.9)) - 0.5)) * uPlates.x;
}

bool brokenAt(vec2 center, vec2 origin)
{
    vec2 away = center - origin;
    return dot(away, away) < uPlates.y * uPlates.y;
}

// How far w sits inside the plate of this cell. Stops early once w is clearly outside.
float inside(vec2 w, vec2 cell, vec2 center, float cut)
{
    float edge = FAR;
    for (int b = -1; b <= 1; b++) {
        for (int a = -1; a <= 1; a++) {
            if (a == 0 && b == 0) {
                continue;
            }
            edge = min(edge, side(w, center, centerOf(cell + vec2(float(a), float(b)))));
            if (edge <= -cut) {
                return edge;
            }
        }
    }
    return edge;
}

// Where a broken plate's picture comes from at screen point p, after the plate slides and rocks.
// Also gives the turned shadow offset. False when the plate can't reach p or its shadow.
bool heave(vec2 cell, vec2 center, vec2 p, vec2 origin, vec2 shadowStep, float bound, out vec2 q, out vec2 shadowShift)
{
    float reach = uPlates.y;
    float spread = uMotion.x * uPlates.x;
    vec2 away = center - origin;
    float distanceOut = length(away);
    vec2 wobble = vec2(cellHash(cell, 23.1), cellHash(cell, 31.7)) * 2.0 - 1.0;
    vec2 heading = OUTWARD * away / max(distanceOut, 1e-4) + wobble;
    heading /= max(length(heading), 1e-4);
    float strength = 1.0 - FALLOFF * min(1.0, distanceOut / reach);
    vec2 shift = heading * spread * strength * (0.6 + 0.4 * cellHash(cell, 47.3));
    float angle = uMotion.y * strength * (cellHash(cell, 59.9) * 2.0 - 1.0);
    float co = cos(angle);
    float si = sin(angle);
    mat2 back = mat2(co, -si, si, co);  // turns by -angle, from the screen into the plate

    q = back * (p - center - shift) + center;
    shadowShift = back * shadowStep;  // the warp is close enough to flat over a shadow's length
    vec2 fromQ = q - center;
    vec2 fromS = fromQ - shadowShift;
    return dot(fromQ, fromQ) <= bound * bound || dot(fromS, fromS) <= bound * bound;
}

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec2 p = vUV.st * aspect;
    // Distances within two layout pixels of an edge must be exact, so they scale up cleanly.
    float cut = 2.0 * uTDOutputInfo.res.y;

    float size = uPlates.x;
    float spread = uMotion.x * size;
    vec2 origin = uOrigin.xy * aspect;
    vec2 shadowStep = spread * vec2(SHADOW_SLIDE, -SHADOW_DROP);
    vec2 home = floor(p / size);

    // Plates outside the break stay put as ground. Its distance is signed: positive on the ground,
    // negative inside the break.
    vec2 wp = warp(p);
    vec2 nearCell = home;
    vec2 nearCenter = vec2(0.0);
    float nearest = 1e9;
    for (int v = -1; v <= 1; v++) {
        for (int u = -1; u <= 1; u++) {
            vec2 cell = home + vec2(float(u), float(v));
            vec2 center = centerOf(cell);
            vec2 d = wp - center;
            float dd = dot(d, d);
            if (dd < nearest) {
                nearest = dd;
                nearCell = cell;
                nearCenter = center;
            }
        }
    }
    bool onGround = !brokenAt(nearCenter, origin);
    float groundEdge = FAR;
    for (int v = -1; v <= 1; v++) {
        for (int u = -1; u <= 1; u++) {
            vec2 center = centerOf(nearCell + vec2(float(u), float(v)));
            if ((u != 0 || v != 0) && brokenAt(center, origin) == onGround) {
                groundEdge = min(groundEdge, side(wp, nearCenter, center));
            }
        }
    }
    float ground = onGround ? groundEdge : -groundEdge;

    // Broken plates heave and rock about their centers. Where two overlap, the higher one sits on top.
    // No point of a plate is more than REACH_CELLS plate sizes from its center, so plates that can't
    // reach this pixel even at full slide are skipped before any of the costly work.
    float plateRadius = REACH_CELLS * size + (COARSE_SWING + FINE_SWING) * uPlates.w * size;
    float slack = plateRadius + spread + abs(uMotion.y) * plateRadius + length(shadowStep) + cut;
    float bound = plateRadius + cut;
    float topRise = -1.0;
    float topEdge = -FAR;
    vec2 topHome = p;
    float nearestEdge = -FAR;  // the plate this point is closest to landing in, for the gaps
    vec2 nearestHome = p;
    for (int v = -1; v <= 1; v++) {
        for (int u = -1; u <= 1; u++) {
            vec2 cell = home + vec2(float(u), float(v));
            vec2 center = centerOf(cell);
            vec2 toPixel = p - center;
            if (dot(toPixel, toPixel) > slack * slack || !brokenAt(center, origin)) {
                continue;
            }
            vec2 q;
            vec2 shadowShift;
            if (!heave(cell, center, p, origin, shadowStep, bound, q, shadowShift)) {
                continue;
            }
            float edge = inside(warp(q), cell, center, cut);
            if (edge > nearestEdge) {
                nearestEdge = edge;
                nearestHome = q;
            }
            if (edge <= 0.0) {
                continue;
            }
            float rise = cellHash(cell, 71.3);
            if (rise > topRise) {
                topRise = rise;
                topEdge = edge;
                topHome = q;
            }
        }
    }

    // What shows here: the highest plate covering it, else the ground, else the nearest plate's
    // distance so its edge still lands in the right place over the gap.
    float edge = ground;
    vec2 source = p;
    if (topRise >= 0.0) {
        edge = topEdge;
        source = topHome;
    } else if (ground <= 0.0 && nearestEdge > ground) {
        edge = nearestEdge;
        source = nearestHome;
    }

    // Shadows fall on the gaps and the ground, so skip them where a lifted plate fully covers.
    float shadowEdge = -FAR;
    if (!(topRise >= 0.0 && topEdge > cut) && spread > 0.0) {
        for (int v = -1; v <= 1; v++) {
            for (int u = -1; u <= 1; u++) {
                vec2 cell = home + vec2(float(u), float(v));
                vec2 center = centerOf(cell);
                vec2 toPixel = p - center;
                if (shadowEdge >= cut || dot(toPixel, toPixel) > slack * slack || !brokenAt(center, origin)) {
                    continue;
                }
                vec2 q;
                vec2 shadowShift;
                if (!heave(cell, center, p, origin, shadowStep, bound, q, shadowShift)) {
                    continue;
                }
                shadowEdge = max(shadowEdge, inside(warp(q) - shadowShift, cell, center, cut));
            }
        }
    }

    fragColor = TDOutputSwizzle(vec4(source - p, clamp(edge, -FAR, FAR), clamp(shadowEdge, -FAR, FAR)));
}
'''

PLATES_SHADER = r'''
uniform vec4 uLook;  // spread (wall heights), ink width (wall heights), unused, unused
uniform vec4 uGap;   // gap color, mix

layout(location = 0) out vec4 fragColor;

vec4 picture(vec2 q, vec2 aspect)
{
    vec2 uv = q / aspect;
    return texture(sTD2DInputs[0], 1.0 - abs(1.0 - mod(uv, 2.0)));
}

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 p = vUV.st * aspect;
    float px = uTD2DInfos[0].res.y;
    float ink = uLook.y;
    float inked = min(1.0, ink / px);  // no hairlines left behind when the plates are shut

    // slid offset, distance inside what shows here, distance inside a shadow
    vec4 layout_ = texture(sTD2DInputs[1], vUV.st);

    vec4 color = vec4(uGap.rgb, 1.0);
    vec4 plate = picture(p + layout_.xy, aspect);
    plate.rgb *= 1.0 - (1.0 - smoothstep(ink - px, ink + px, layout_.z)) * inked;
    color = mix(color, plate, smoothstep(-px, px, layout_.z));

    // Lifted plates throw hard shadows onto the gaps and the ground.
    color.rgb *= 1.0 - smoothstep(-px, px, layout_.w);

    // Under a pixel of motion the plates still sit edge to edge, so show the picture whole.
    float open = clamp(uLook.x / px, 0.0, 1.0);
    fragColor = TDOutputSwizzle(mix(source, color, uGap.a * open));
}
'''

EFFECT_SOURCE = r'''
import math
import random

SPREAD_SCALE = 0.3     # at full Slip each plate slides this much of its own size
SOFT_SPREAD = 0.25
TILT_SCALE = 0.15      # radians a plate rocks at full Tilt
INK_SCALE = 0.018      # thinner than Fault Line, because every plate edge carries ink
SOFT_INK = 0.6
FULL_INK_SPREAD = 0.05  # the ink reaches full width once plates have slid this much of their size
ROWS_FEWEST = 1.5      # plates down the wall's height, at Chunks 0 and 1
ROWS_RANGE = 5.5
REACH_MIN = 0.4        # wall heights from the origin that break, at Reach 0
REACH_RANGE = 3.1
SOFT_REACH = 0.35      # a soft hit breaks this share of the reach
DRIFT_X = 0.08
DRIFT_Y = 0.15
SEED_RANGE = 100.0
FEWEST_SHOCKS = 0.25  # a quarter cycle closes the plates with no aftershock
SHOCK_RANGE = 3.75
HOT_ORDER = ("pink", "red", "yellow")
COLORS = {
    "pink": (1.0, 0.5529, 1.0),
    "red": (1.0, 0.2588, 0.3020),
    "yellow": (0.9529, 0.8667, 0.2431),
    "blue": (0.1451, 0.4510, 1.0),
    "cyan": (0.2667, 0.9725, 1.0),
    "white": (1.0, 1.0, 1.0),
    "black": (0.0, 0.0, 0.0),
}

_rng = random.Random()
_hit = None
_hits = 0


def reset(block):
    global _hit, _hits
    _hit = None
    _hits = 0


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest(layout, plates):
    _write(layout, 0, 0.25, 0.0, 0.0, 0.0)
    _write(layout, 1, 0.0, 0.0, 0.0, 0.0)
    _write(layout, 2, 0.5, 0.5, 0.0, 0.0)
    _write(plates, 0, 0.0, 0.0, 0.0, 0.0)
    _write(plates, 1, 0.0, 0.0, 0.0, 0.0)


def _roll(block, velocity):
    # The layout is fixed for the life of a hit, because changing it mid-air would pop.
    global _hits
    choice = block.par.Gapcolor.eval()
    name = HOT_ORDER[_hits % len(HOT_ORDER)] if choice == "hot" else choice
    rows = ROWS_FEWEST + ROWS_RANGE * _clamped(block.par.Chunks)
    reach = (REACH_MIN + REACH_RANGE * _clamped(block.par.Reach)) * (SOFT_REACH + (1.0 - SOFT_REACH) * velocity)
    origin_x = _clamped(block.par.Origin) + _rng.uniform(-DRIFT_X, DRIFT_X)
    hit = {
        "seed": _rng.random() * SEED_RANGE,
        "size": 1.0 / rows,
        "reach": reach,
        "origin": (max(0.0, min(1.0, origin_x)), 0.5 + _rng.uniform(-DRIFT_Y, DRIFT_Y)),
        "heading": _rng.choice((-1.0, 1.0)),
        "color": COLORS[name],
        "velocity": velocity,
    }
    _hits += 1
    return hit


def apply(block, env):
    global _hit
    switch = op("switch")
    layout = op("layout")
    plates = op("plates")
    if env.stage == "idle":
        switch.par.index = 0
        _hit = None
        _write_rest(layout, plates)
        return

    switch.par.index = 1
    if env.started or _hit is None:
        _hit = _roll(block, env.velocity)

    slip = env.level
    if env.stage == "release":
        shocks = FEWEST_SHOCKS + _clamped(block.par.Aftershock) * SHOCK_RANGE
        slip *= math.cos(env.progress * math.pi * 2.0 * shocks)

    velocity = _hit["velocity"]
    amount = _clamped(block.par.Slip) * SPREAD_SCALE * (SOFT_SPREAD + (1.0 - SOFT_SPREAD) * velocity)
    spread = amount * abs(slip)
    tilt = _clamped(block.par.Tilt) * TILT_SCALE * slip * _hit["heading"]
    ink = _clamped(block.par.Ink) * INK_SCALE * (SOFT_INK + (1.0 - SOFT_INK) * velocity)
    ink *= min(1.0, spread / FULL_INK_SPREAD)
    red, green, blue = _hit["color"]

    _write(layout, 0, _hit["size"], _hit["reach"], _hit["seed"], _clamped(block.par.Jag))
    _write(layout, 1, spread, tilt, 0.0, 0.0)
    _write(layout, 2, _hit["origin"][0], _hit["origin"][1], 0.0, 0.0)
    _write(plates, 0, spread * _hit["size"], ink, 0.0, 0.0)
    _write(plates, 1, red, green, blue, _clamped(block.par.Mix))
'''

LAYOUT_UNIFORMS = ["uPlates", "uMotion", "uOrigin"]
PLATES_UNIFORMS = ["uLook", "uGap"]


def _glsl(block, name, dat, uniforms, x):
    node = add_top(block, glslTOP, name, x)
    node.par.pixeldat = dat
    node.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    node.seq.vec.numBlocks = len(uniforms)
    for index, uniform in enumerate(uniforms):
        setattr(node.par, "vec%dname" % index, uniform)
    return node


def build(library):
    block = new_block(library, "fault_plates")
    # Same timing as Fault Line: at 116 BPM a beat is 0.52 s, so this settles before the next stab.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.0, "release": 0.45, "curve": 1.2, "velocitydepth": 1.0})
    page = block.appendCustomPage("Fault Plates")
    add_float(page, "Slip", "Slip", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Chunks", "Chunks", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Reach", "Reach", 0.7, 0.0, 1.0, True, True)
    add_float(page, "Origin", "Origin", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Jag", "Jag", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Tilt", "Tilt", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Aftershock", "Aftershock", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Ink", "Ink", 0.5, 0.0, 1.0, True, True)
    add_menu(
        page,
        "Gapcolor",
        "Gap Color",
        ["hot", "pink", "red", "yellow", "blue", "cyan", "white", "black"],
        ["Hot (cycles)", "Pink", "Red", "Yellow", "Blue", "Cyan", "White", "Black"],
        "hot",
    )
    # Resolution the plate layout is worked out at. The picture is always sampled at full size.
    add_menu(page, "Detail", "Detail", ["useinput", "half", "quarter", "eighth"], ["Full", "Half", "Quarter", "Eighth"], "quarter")
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "layout_shader", LAYOUT_SHADER, -400, -150)
    add_text(block, "plates_shader", PLATES_SHADER, -200, -150)

    source = add_top(block, inTOP, "in1", -600)
    layout = _glsl(block, "layout", "layout_shader", LAYOUT_UNIFORMS, -400)
    layout.par.outputresolution.expr = "parent().par.Detail.eval()"
    # 16-bit float filters smoothly on every GPU, and its error stays under a wall pixel here.
    layout.par.format = "rgba16float"
    plates = _glsl(block, "plates", "plates_shader", PLATES_UNIFORMS, -200)
    plates.par.outputresolution = "useinput"
    plates.par.format = "useinput"
    switch = add_top(block, switchTOP, "switch", 0)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 200)

    source.outputConnectors[0].connect(layout)
    plates.inputConnectors[0].connect(source)
    plates.inputConnectors[1].connect(layout)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(plates)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
