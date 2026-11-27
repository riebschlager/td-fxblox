# Fault Line: tail hit, a jagged crack splits the wall and the halves jolt back in aftershocks

SHADER = r'''
uniform vec4 uFault;   // height of the crack, seed, jag, forks
uniform vec4 uMotion;  // gap, shear, share of the gap taken by the top half, ink width (all in wall heights but share)
uniform vec4 uGap;     // gap color, mix

layout(location = 0) out vec4 fragColor;

const float COARSE_RUN = 0.62;    // length of the crack's segments, in wall heights
const float MIDDLE_RUN = 0.19;
const float FINE_RUN = 0.06;
const float COARSE_SWING = 0.16;
const float MIDDLE_SWING = 0.08;
const float FINE_SWING = 0.03;
const float SLOPE_STEP = 0.002;
const float SHADOW_DROP = 0.32;   // of the gap, how far the top half's shadow falls
const float SHADOW_SLIDE = 0.22;  // of the gap, how far that shadow shifts sideways
const float FORK_SPACING = 0.3;  // one possible fork per this much wall
const float FORK_WIDTH = 1.0;    // of the ink width
const float FORK_SHORTEST = 0.08;
const float FORK_RANGE = 0.34;
const float FORK_SLANT = 1.2;
const float FORK_WANDER = 0.12;   // of a fork's length
const float FORK_RUN = 0.05;

float hash(float n)
{
    n = fract(n * 0.1031);
    n *= n + 33.33;
    n *= n + n;
    return fract(n);
}

// Straight runs between random points, so the line stays angular like cut paper.
float zig(float x, float seed)
{
    float cell = floor(x);
    return mix(hash(cell + seed), hash(cell + 1.0 + seed), x - cell) * 2.0 - 1.0;
}

float crack(float x)
{
    float seed = uFault.y;
    float path = COARSE_SWING * zig(x / COARSE_RUN, seed)
        + MIDDLE_SWING * zig(x / MIDDLE_RUN, seed + 17.0)
        + FINE_SWING * zig(x / FINE_RUN, seed + 41.0);
    return uFault.x + uFault.z * path;
}

// Vertical distance above the crack, corrected so steep runs keep their ink width.
float above(vec2 p, float x, float y)
{
    float slope = (crack(x + SLOPE_STEP) - crack(x - SLOPE_STEP)) / (2.0 * SLOPE_STEP);
    return (p.y - y) / sqrt(1.0 + slope * slope);
}

vec4 picture(vec2 q, vec2 aspect)
{
    vec2 uv = q / aspect;
    return texture(sTD2DInputs[0], 1.0 - abs(1.0 - mod(uv, 2.0)));
}

// Hairline cracks running off the main one, drawn in the half's own coordinates.
float forks(vec2 q, float side, float width, float px)
{
    if (uFault.w <= 0.0) {
        return 0.0;
    }
    float seed = uFault.y;
    float depth = (q.y - crack(q.x)) * side;
    if (depth <= 0.0) {
        return 0.0;
    }
    float home = floor(q.x / FORK_SPACING);
    float ink = 0.0;
    for (int i = -1; i <= 1; i++) {
        float cell = home + float(i);
        if (hash(cell + 3.7 + seed) >= uFault.w) {
            continue;
        }
        if ((hash(cell + 29.3 + seed) < 0.5 ? 1.0 : -1.0) != side) {
            continue;
        }
        float reach = (FORK_SHORTEST + FORK_RANGE * hash(cell + 61.1 + seed)) * (0.6 + 0.4 * uFault.w);
        float t = depth / reach;
        if (t >= 1.0) {
            continue;
        }
        float start = (cell + 0.5 + 0.7 * (hash(cell + 97.9 + seed) - 0.5)) * FORK_SPACING;
        float slant = (hash(cell + 131.3 + seed) - 0.5) * FORK_SLANT;
        float x = start + slant * depth + FORK_WANDER * reach * zig(depth / FORK_RUN, cell + seed);
        float half_ = max(width * (1.0 - t), px * 0.5);
        ink = max(ink, 1.0 - smoothstep(half_ - px, half_ + px, abs(q.x - x)));
    }
    return ink;
}

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 p = vUV.st * aspect;
    float px = uTD2DInfos[0].res.y;

    float gap = uMotion.x;
    float shear = uMotion.y;
    float rise = gap * uMotion.z;
    float drop = gap - rise;
    float ink = uMotion.w;
    float forkWidth = ink * FORK_WIDTH;
    float inked = min(1.0, ink / px);  // no hairline left behind when the crack is shut

    float topX = p.x - shear;
    float bottomX = p.x + shear;
    float topEdge = above(p, topX, crack(topX) + rise);
    float bottomEdge = -above(p, bottomX, crack(bottomX) - drop);

    vec4 color = vec4(uGap.rgb, 1.0);

    // The top half throws a hard, jagged shadow down into the gap.
    float shadowX = topX - gap * SHADOW_SLIDE;
    float shadow = above(p, shadowX, crack(shadowX) + rise - gap * SHADOW_DROP);
    color.rgb *= 1.0 - smoothstep(-px, px, shadow);

    vec2 bottomHome = vec2(bottomX, p.y + drop);
    vec4 lower = picture(bottomHome, aspect);
    float lowerInk = 1.0 - smoothstep(ink - px, ink + px, bottomEdge);
    lowerInk = max(lowerInk, forks(bottomHome, -1.0, forkWidth, px));
    lower.rgb *= 1.0 - lowerInk * inked;
    color = mix(color, lower, smoothstep(-px, px, bottomEdge));

    vec2 topHome = vec2(topX, p.y - rise);
    vec4 upper = picture(topHome, aspect);
    float upperInk = 1.0 - smoothstep(ink - px, ink + px, topEdge);
    upperInk = max(upperInk, forks(topHome, 1.0, forkWidth, px));
    upper.rgb *= 1.0 - upperInk * inked;
    color = mix(color, upper, smoothstep(-px, px, topEdge));

    fragColor = TDOutputSwizzle(mix(source, color, uGap.a));
}
'''

EFFECT_SOURCE = r'''
import math
import random

SLIP_SCALE = 0.3
SOFT_SLIP = 0.25
SHEAR = 0.35          # the halves slide this much of the gap sideways
INK_SCALE = 0.028
SOFT_INK = 0.6
FULL_INK_GAP = 0.02   # the ink reaches full width once the gap is this wide
HEIGHT_MIN = 0.2
HEIGHT_RANGE = 0.6
DRIFT = 0.06
TOP_SHARE_LOW = 0.25  # the top half takes this much of the gap on one hit, the rest on the next
SEED_RANGE = 100.0
FEWEST_SHOCKS = 0.25  # a quarter cycle closes the crack with no aftershock
SHOCK_RANGE = 3.75
HOT_ORDER = ("pink", "red", "yellow")
COLORS = @@SWATCHES@@
PALETTES = @@PALETTES@@

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


def _write_rest(node):
    _write(node, 0, 0.5, 0.0, 0.0, 0.0)
    _write(node, 1, 0.0, 0.0, 0.5, 0.0)
    _write(node, 2, 0.0, 0.0, 0.0, 0.0)


def _roll(block, velocity):
    # The path is fixed for the life of a hit, because changing it mid-air would pop.
    global _hits
    choice = block.par.Gapcolor.eval()
    if choice in PALETTES:
        stops = PALETTES[choice]
        color = stops[_hits % len(stops)]
    else:
        name = HOT_ORDER[_hits % len(HOT_ORDER)] if choice == "hot" else choice
        color = COLORS[name]
    hit = {
        "seed": _rng.random() * SEED_RANGE,
        "drift": _rng.uniform(-DRIFT, DRIFT),
        "heading": _rng.choice((-1.0, 1.0)),
        "share": TOP_SHARE_LOW if _hits % 2 == 0 else 1.0 - TOP_SHARE_LOW,
        "color": color,
        "velocity": velocity,
    }
    _hits += 1
    return hit


def apply(block, env):
    global _hit
    switch = op("switch")
    fault = op("fault")
    if env.stage == "idle":
        switch.par.index = 0
        _hit = None
        _write_rest(fault)
        return

    switch.par.index = 1
    if env.started or _hit is None:
        _hit = _roll(block, env.velocity)

    slip = env.level
    if env.stage == "release":
        shocks = FEWEST_SHOCKS + _clamped(block.par.Aftershock) * SHOCK_RANGE
        slip *= math.cos(env.progress * math.pi * 2.0 * shocks)

    velocity = _hit["velocity"]
    amount = _clamped(block.par.Slip) * SLIP_SCALE * (SOFT_SLIP + (1.0 - SOFT_SLIP) * velocity)
    gap = amount * abs(slip)
    shear = amount * SHEAR * slip * _hit["heading"]
    ink = _clamped(block.par.Ink) * INK_SCALE * (SOFT_INK + (1.0 - SOFT_INK) * velocity)
    ink *= min(1.0, gap / FULL_INK_GAP)
    height = HEIGHT_MIN + HEIGHT_RANGE * _clamped(block.par.Height) + _hit["drift"]
    red, green, blue = _hit["color"]

    _write(fault, 0, height, _hit["seed"], _clamped(block.par.Jag), _clamped(block.par.Forks) * velocity)
    _write(fault, 1, gap, shear, _hit["share"], ink)
    _write(fault, 2, red, green, blue, _clamped(block.par.Mix))
'''

UNIFORMS = ["uFault", "uMotion", "uGap"]


def build(library):
    block = new_block(library, "fault_line")
    # At 116 BPM a beat is 0.52 s, so this release settles before the next stab.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.0, "release": 0.45, "curve": 1.2, "velocitydepth": 1.0})
    page = block.appendCustomPage("Fault Line")
    add_float(page, "Slip", "Slip", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Jag", "Jag", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Forks", "Forks", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Height", "Height", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Aftershock", "Aftershock", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Ink", "Ink", 0.5, 0.0, 1.0, True, True)
    add_palette_menu(
        page, "Gapcolor", "Gap Color",
        presets={"hot": "Hot (cycles)", **{name: name.title() for name in ("pink", "red", "yellow", "blue", "cyan", "white", "black")}},
        default="hot",
    )
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "fault_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    fault = add_top(block, glslTOP, "fault", -300)
    fault.par.pixeldat = "fault_shader"
    fault.par.outputresolution = "useinput"
    fault.par.format = "useinput"
    fault.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    fault.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(fault.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(fault)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(fault)
    switch.outputConnectors[0].connect(output)

    effect = palette_effect(EFFECT_SOURCE, SWATCHES=SWATCH_RGB, PALETTES=PALETTE_RGB)
    add_text(block, "effect", effect, -500, -300)
    add_callbacks(block)
    return block
