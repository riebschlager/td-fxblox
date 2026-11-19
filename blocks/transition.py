# Transition: hold to hide the whole picture with a classic transition, let go to bring it back
#
# The press plays the hide over Attack and keeps the picture gone (transparent) while held. Letting
# go plays the same style back in over Release. Each half has its own easing, so the picture can
# leave smoothly and land with a bounce. One shader pass draws every style from a single "hidden"
# amount: 0 is the untouched picture, 1 is fully gone, and overshoot easings run past either end.

SHADER = r'''
uniform vec4 uMove;    // hidden amount, style, side x, side y (the side the picture comes in from)
uniform vec4 uShape;   // softness, count, zoom, spin turns
uniform vec4 uPlace;   // center x, center y, seed, mix
uniform vec4 uMore;    // exit side (1 back the way it came, -1 carrying on through), unused x3

layout(location = 0) out vec4 fragColor;

const float PI = 3.14159265;
const float TAU = 6.2831853;
const float LINE = 0.004;          // squash: how thin the line gets before it shrinks to a dot
const float SQUASH_SPLIT = 0.7;    // squash: share of the move spent flattening to the line
const float FLIP_DEPTH = 2.0;      // flip: camera distance, in half-widths of the turning card
const float SLICE_SPREAD = 0.6;    // slices: how far apart the slowest and fastest bands start
const float RGB_SPREAD = 0.35;     // rgb split: how far behind green and blue trail red
const float TILE_SWEEP = 0.25;     // tiles: how much the pop order leans toward the direction

float aspect;
float seed;

float hash(vec2 p)
{
    vec3 p3 = fract(vec3(p.xyx + seed) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float noise(vec2 p)
{
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
               mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

// Transparent outside the frame, so whatever moved away leaves nothing behind.
vec4 frameAt(vec2 uv)
{
    if (any(lessThan(uv, vec2(0.0))) || any(greaterThan(uv, vec2(1.0)))) {
        return vec4(0.0);
    }
    return texture(sTD2DInputs[0], uv);
}

// 0 to 1 across the frame, highest on the side the picture comes in from.
float along(vec2 uv, vec2 d)
{
    float span = 0.5 * (abs(d.x) + abs(d.y));
    return clamp(dot(uv - 0.5, d) / max(span, 1e-4) * 0.5 + 0.5, 0.0, 1.0);
}

// How hidden a pixel is, given its place c in the order (low hides first, high comes back first).
float covered(float c, float h, float soft)
{
    float w = fwidth(c);
    if (w > 0.25) {
        w = 0.0;  // a seam where the order jumps, like a tile border, not an edge to soften
    }
    float s = max(max(soft, 1.5 * w), 1e-4);
    float x = clamp((h * (1.0 + s) - c) / s, 0.0, 1.0);
    return x * x * (3.0 - 2.0 * x);
}

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 res = uTD2DInfos[0].res.zw;
    aspect = res.x / res.y;
    vec2 asp = vec2(aspect, 1.0);
    seed = uPlace.z;

    float h = uMove.x;
    int style = int(uMove.y + 0.5);
    vec2 d = uMove.zw;
    float exitSide = uMore.x;
    float soft = uShape.x;
    float count = max(1.0, uShape.y);
    vec2 center = uPlace.xy;
    vec2 uv = vUV.st;
    vec4 result = source;

    if (style == 0) {            // slide: one whole frame width (or height) off to the side
        result = frameAt(uv - d * h);
    } else if (style == 1 || style == 2) {  // zoom away (shrink) or zoom through (grow), fading
        float scale = style == 1 ? 1.0 - uShape.z * h : 1.0 + uShape.z * h;
        result = frameAt(center + (uv - center) / max(scale, 1e-3)) * clamp(1.0 - h, 0.0, 1.0);
    } else if (style == 3) {     // spin: turns while shrinking to nothing
        float scale = 1.0 - h;
        if (scale <= 1e-3) {
            result = vec4(0.0);
        } else {
            float a = h * uShape.w * TAU;
            vec2 p = (uv - center) * asp;
            p = mat2(cos(a), sin(a), -sin(a), cos(a)) * p / scale;
            result = frameAt(p / asp + center);
        }
    } else if (style == 4) {     // squash: flattens to a line, then the line shrinks to a dot
        float first = min(h / SQUASH_SPLIT, 1.0);
        float second = clamp((h - SQUASH_SPLIT) / (1.0 - SQUASH_SPLIT), 0.0, 1.0);
        float flat_ = 1.0 - first * (1.0 - LINE);
        float shrink = 1.0 - second;
        vec2 s = abs(d.x) > 0.5 ? vec2(flat_, shrink) : vec2(shrink, flat_);
        if (min(s.x, s.y) <= 1e-4) {
            result = vec4(0.0);
        } else {
            result = frameAt(center + (uv - center) / s);
        }
    } else if (style == 5) {     // flip: the frame turns edge-on like a card, in perspective
        bool aroundY = abs(d.x) > 0.5;
        float a = h * PI * 0.5 * (aroundY ? d.x : d.y);
        vec2 p = (uv - 0.5) * asp;
        float halfSpan = aroundY ? aspect * 0.5 : 0.5;
        float depth = FLIP_DEPTH * halfSpan;
        float xs = aroundY ? p.x : p.y;
        float ys = aroundY ? p.y : p.x;
        float den = depth * cos(a) - xs * sin(a);
        if (den <= 1e-5) {
            result = vec4(0.0);
        } else {
            float x = xs * depth / den;
            float y = ys * (depth + x * sin(a)) / depth;
            vec2 q = aroundY ? vec2(x, y) : vec2(y, x);
            result = frameAt(q / asp + 0.5);
        }
    } else if (style <= 12) {    // the wipes: every pixel has a place in the order
        float c;
        if (style == 6) {        // wipe
            c = along(uv, d);
        } else if (style == 7) { // barn doors: open from the middle
            c = 1.0 - abs(along(uv, d) * 2.0 - 1.0);
            c = exitSide < 0.0 ? 1.0 - c : c;
        } else if (style == 8) { // iris: a circle from the center point
            vec2 p = (uv - center) * asp;
            vec2 far2 = max(center, 1.0 - center) * asp;
            c = 1.0 - length(p) / max(length(far2), 1e-4);
            c = exitSide < 0.0 ? 1.0 - c : c;
        } else if (style == 9) { // clock: a sweep around the center point from twelve o'clock
            vec2 p = (uv - center) * asp;
            float turn = fract(atan(p.x, p.y) / TAU);
            float dir = abs(d.x) > 0.5 ? d.x : d.y;
            c = dir > 0.0 ? turn : 1.0 - turn;
        } else if (style == 10) {  // blinds: the wipe, repeated in stripes
            c = fract(along(uv, d) * count);
        } else if (style == 11) {  // tiles: square tiles pop in a shuffled order
            vec2 cells = vec2(count, max(1.0, floor(count / aspect + 0.5)));
            vec2 cell = min(floor(uv * cells), cells - 1.0);
            c = (1.0 - TILE_SWEEP) * hash(cell) + TILE_SWEEP * along((cell + 0.5) / cells, d);
        } else {                   // dissolve: a noise threshold that eats in from the dark spots
            vec2 p = uv * asp * count;
            float n = 0.65 * noise(p) + 0.35 * noise(p * 2.7 + 17.0);
            c = clamp((n - 0.2) / 0.6, 0.0, 1.0);
        }
        result = source * (1.0 - covered(c, h, soft));
    } else if (style == 13) {    // pixelate: blocks grow until only a few are left, then fade
        float biggest = res.y / count;
        float size = exp(mix(0.0, log(max(biggest, 1.0)), clamp(h, 0.0, 1.0)));
        vec2 p = (uv - 0.5) * res / size;
        vec2 q = clamp((floor(p) + 0.5) * size / res + 0.5, 0.0, 1.0);
        result = texture(sTD2DInputs[0], q) * (1.0 - smoothstep(0.55, 1.0, h));
    } else if (style == 14) {    // slices: bands shove off at their own pace
        float coord = abs(d.x) > 0.5 ? uv.y : uv.x;
        float band = floor(coord * count);
        float r = hash(vec2(band, 3.7));
        float p = h >= 0.0 ? clamp(h * (1.0 + SLICE_SPREAD) - r * SLICE_SPREAD, 0.0, 1.0) : h;
        result = frameAt(uv - d * p);
    } else {                     // rgb split: red leads, green and blue trail behind
        vec3 p;
        for (int k = 0; k < 3; k++) {
            float lag = float(k) * 0.5 * RGB_SPREAD;
            p[k] = h >= 0.0 ? clamp(h * (1.0 + RGB_SPREAD) - lag, 0.0, 1.0) : h;
        }
        vec4 r = frameAt(uv - d * p.x);
        vec4 g = frameAt(uv - d * p.y);
        vec4 b = frameAt(uv - d * p.z);
        result = vec4(r.r, g.g, b.b, max(r.a, max(g.a, b.a)));
    }

    fragColor = TDOutputSwizzle(mix(source, result, uPlace.w));
}
'''

EFFECT_SOURCE = r'''
import math
import random

STYLES = ["slide", "zoomaway", "zoomthrough", "spin", "squash", "flip", "wipe", "doors", "iris",
          "clock", "blinds", "tiles", "dissolve", "pixelate", "slices", "rgbsplit"]
DIRECTIONS = {
    "left": (-1.0, 0.0), "right": (1.0, 0.0), "up": (0.0, 1.0), "down": (0.0, -1.0),
    "upleft": (-1.0, 1.0), "upright": (1.0, 1.0), "downleft": (-1.0, -1.0), "downright": (1.0, -1.0),
}
SOFT_MIN = 0.0015       # of the frame: the hardest wipe edge, before antialiasing
SOFT_RANGE = 0.35       # of the frame: the softest wipe edge, at Softness 1
COUNT_MIN = 2           # stripes, tiles, bands, grain and pixel blocks, at Count 0
COUNT_RANGE = 46
COUNT_CURVE = 1.5       # more of the knob for the low counts
SHRINK_MAX = 0.95       # zoom away at full Zoom shrinks the picture by this much
GROW_MAX = 4.0          # zoom through at full Zoom grows the picture this many times over
BACK_MAX = 3.4          # Back easing's overshoot at full Overshoot (the textbook 1.7 is halfway)
BOUNCE_MAX = 0.7        # Bounce easing: how much speed survives each landing at full Overshoot
BOUNCES = 4
ELASTIC_TURNS = 2.5
ELASTIC_DAMP = 6.0
FULL = 0.999            # hidden at least this much counts as fully gone
REST = 1e-4             # hidden no more than this counts as untouched

_rng = random.Random()
_hidden = 0.0
_phase = None
_from = 0.0
_level_from = 0.0
_side = 1.0
_seed = 0.0


def reset(block):
    global _hidden, _phase, _from, _level_from, _side
    _hidden = 0.0
    _phase = None
    _from = 0.0
    _level_from = 0.0
    _side = 1.0


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _bounce(t, e):
    # A ball dropped onto the target: it lands at full speed and keeps e of its speed each landing.
    if e <= 1e-4:
        return t * t
    total = 1.0 + sum(2.0 * e ** k for k in range(1, BOUNCES + 1))
    s = t * total
    if s < 1.0:
        return s * s
    s -= 1.0
    for k in range(1, BOUNCES + 1):
        span = 2.0 * e ** k
        if s < span:
            x = s - span * 0.5
            return 1.0 - (e ** (2 * k) - x * x)
        s -= span
    return 1.0


def ease(name, t, overshoot):
    """0 at t 0 and exactly 1 at t 1. Back and Elastic run past 1, Bounce comes back short of it."""
    t = max(0.0, min(1.0, t))
    if t >= 1.0:
        return 1.0
    out = 1.0 - (1.0 - t) ** 3
    if name == "linear":
        return t
    if name == "easein":
        return t ** 3
    if name == "easeout":
        return out
    if name == "easeinout":
        return 4.0 * t ** 3 if t < 0.5 else 1.0 - (2.0 - 2.0 * t) ** 3 * 0.5
    if name == "back":
        s = BACK_MAX * overshoot
        u = t - 1.0
        return 1.0 + (s + 1.0) * u ** 3 + s * u * u
    if name == "bounce":
        return _bounce(t, BOUNCE_MAX * overshoot)
    if name == "elastic":
        wobble = 1.0 - (1.0 - t) * math.exp(-ELASTIC_DAMP * t) * math.cos(math.tau * ELASTIC_TURNS * t)
        return out + (wobble - out) * overshoot
    return t


def count_for(block):
    return COUNT_MIN + int(round(COUNT_RANGE * _clamped(block.par.Count) ** COUNT_CURVE))


def softness_for(block):
    return SOFT_MIN + SOFT_RANGE * _clamped(block.par.Softness) ** 2


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest(node):
    for index in range(4):
        _write(node, index, 0.0, 0.0, 0.0, 0.0)


def _start(block, phase, level):
    # A new half of the move starts from wherever the picture is now, so nothing jumps.
    global _phase, _from, _level_from, _side, _seed
    _phase = phase
    _from = _hidden
    _level_from = level
    # The side may only change while nothing of the move shows: untouched, or fully gone.
    if phase == "hide" and abs(_hidden) <= REST:
        _side = -1.0 if block.par.Exit.eval() == "through" else 1.0
        _seed = _rng.random() * 100.0
    elif phase == "reveal" and _hidden >= FULL:
        _side = 1.0


def apply(block, env):
    global _hidden
    switch = op("switch")
    node = op("transition")
    if env.stage == "idle":
        switch.par.index = 0
        _write_rest(node)
        reset(block)
        return

    phase = "reveal" if env.stage == "release" else "hide"
    if env.started or phase != _phase:
        _start(block, phase, env.level)

    overshoot = _clamped(block.par.Overshoot)
    if phase == "hide":
        # Attack ramps the level linearly from where it was; that ramp is this half's clock.
        if env.stage == "hold" or _level_from >= 1.0:
            t = 1.0
        else:
            t = (env.level - _level_from) / (1.0 - _level_from)
        _hidden = _from + (env.velocity - _from) * ease(block.par.Outeasing.eval(), t, overshoot)
    else:
        _hidden = _from * (1.0 - ease(block.par.Ineasing.eval(), env.progress, overshoot))

    style = block.par.Style.eval()
    dx, dy = DIRECTIONS[block.par.Direction.eval()]
    spin_sign = dx if dx != 0.0 else -dy
    zoom = 0.0
    if style == "zoomaway":
        zoom = _clamped(block.par.Zoom) * SHRINK_MAX
    elif style == "zoomthrough":
        zoom = _clamped(block.par.Zoom) * GROW_MAX

    switch.par.index = 1
    _write(node, 0, _hidden, STYLES.index(style), dx * _side, dy * _side)
    _write(node, 1, softness_for(block), count_for(block), zoom, block.par.Spin.eval() * spin_sign * _side)
    _write(node, 2, _clamped(block.par.Centerx), _clamped(block.par.Centery), _seed, _clamped(block.par.Mix))
    _write(node, 3, _side, 0.0, 0.0, 0.0)
'''

UNIFORMS = ["uMove", "uShape", "uPlace", "uMore"]


def build(library):
    block = new_block(library, "transition")
    # Held: the press hides the picture over Attack and keeps it gone, letting go brings it back
    # over Release. Hit mode hides, waits Hold, then brings it back. Curve isn't used: the two
    # easing menus shape the move instead.
    add_control_page(block, {"mode": "gate", "attack": 0.35, "hold": 0.5, "release": 0.6, "curve": 1.0, "velocitydepth": 0.0})
    page = block.appendCustomPage("Transition")
    add_menu(
        page,
        "Style",
        "Style",
        ["slide", "zoomaway", "zoomthrough", "spin", "squash", "flip", "wipe", "doors", "iris",
         "clock", "blinds", "tiles", "dissolve", "pixelate", "slices", "rgbsplit"],
        ["Slide", "Zoom Away (shrink + fade)", "Zoom Through (grow + fade)", "Spin", "Squash (TV off)",
         "Card Flip", "Wipe", "Barn Doors", "Iris", "Clock", "Blinds", "Tiles", "Dissolve", "Pixelate",
         "Slices", "RGB Split"],
        "slide",
    )
    add_menu(
        page,
        "Direction",
        "Comes In From",
        ["left", "right", "up", "down", "upleft", "upright", "downleft", "downright"],
        ["Left", "Right", "Top", "Bottom", "Top Left", "Top Right", "Bottom Left", "Bottom Right"],
        "left",
    )
    add_menu(page, "Exit", "Leaves", ["reverse", "through"], ["Back the Way It Came", "Carrying On Through"], "reverse")
    easings = ["linear", "easein", "easeout", "easeinout", "back", "bounce", "elastic"]
    easing_labels = ["Linear", "Ease In", "Ease Out", "Ease In-Out", "Back (overshoot)", "Bounce", "Elastic"]
    add_menu(page, "Outeasing", "Hide Easing", easings, easing_labels, "easein")
    add_menu(page, "Ineasing", "Reveal Easing", easings, easing_labels, "back")
    add_float(page, "Overshoot", "Overshoot / Bounce", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Softness", "Edge Softness", 0.15, 0.0, 1.0, True, True)
    add_float(page, "Count", "Count (blinds, tiles, slices, grain)", 0.3, 0.0, 1.0, True, True)
    add_float(page, "Zoom", "Zoom", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Spin", "Spin (turns)", 0.5, 0.0, 2.0, clamp_low=True)
    add_float(page, "Centerx", "Center X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Centery", "Center Y", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "transition_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    node = add_top(block, glslTOP, "transition", -300)
    node.par.pixeldat = "transition_shader"
    node.par.outputresolution = "useinput"
    node.par.format = "useinput"
    node.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    node.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(node.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(node)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(node)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
