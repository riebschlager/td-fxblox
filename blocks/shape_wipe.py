# Shape Wipe: a hit, a brand-color shape grows until it covers the wall, holds, then clears
#
# The press grows the shape over Attack, eased, so it can overshoot and settle. The color holds
# for Hold (or while held in Gate mode), then the Exit clears it over Release: a fade, the same
# shape punched through as a hole, halftone dots that shrink away, the shape shrinking back, a bar
# wiping it off, or a hard cut. Every shape is a distance field, so the edge, its black ink
# outline and its hard shadow come from one number per pixel. A press during the exit starts a
# new shape on top while the old one keeps clearing underneath, so repeated hits never jump.

SHADER = r'''
uniform vec4 uShapeA;  // newest: shape, cover, rotation in radians, active
uniform vec4 uPlaceA;  // newest: origin x, origin y, exit, exit progress
uniform vec4 uColorA;  // newest: red, green, blue, star points
uniform vec4 uShapeB;  // the one underneath, still clearing
uniform vec4 uPlaceB;
uniform vec4 uColorB;
uniform vec4 uLook;    // softness, ink, shadow (all in wall heights), mix

layout(location = 0) out vec4 fragColor;

const float PI = 3.14159265;
const int BAR = 8;
const int EXIT_FADE = 0;
const int EXIT_PUNCH = 1;
const int EXIT_HALFTONE = 2;
const int EXIT_RETREAT = 3;
const int EXIT_WIPEOFF = 4;
const float FAR_AWAY = 1e5;
const float DOT_PITCH = 0.04;     // halftone exit: dot spacing, in wall heights
const float DOT_STAGGER = 0.6;    // halftone exit: dots near the origin shrink first
const float INK_LEAVE = 0.15;     // halftone exit: share of it spent losing the ink and shadow

float aspect;
float px;

float sdBox(vec2 p, vec2 b)
{
    vec2 d = abs(p) - b;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}

float sdTriangle(vec2 p, float r)
{
    const float k = 1.7320508;
    p.x = abs(p.x) - r;
    p.y = p.y + r / k;
    if (p.x + k * p.y > 0.0) {
        p = vec2(p.x - k * p.y, -k * p.x - p.y) / 2.0;
    }
    p.x -= clamp(p.x, -2.0 * r, 0.0);
    return -length(p) * sign(p.y);
}

float sdHexagon(vec2 p, float r)
{
    const vec3 k = vec3(-0.866025404, 0.5, 0.577350269);
    p = abs(p);
    p -= 2.0 * min(dot(k.xy, p), 0.0) * k.xy;
    p -= vec2(clamp(p.x, -k.z * r, k.z * r), r);
    return length(p) * sign(p.y);
}

float sdStar(vec2 p, float r, float n, float m)
{
    float an = PI / n;
    float en = PI / m;
    vec2 acs = vec2(cos(an), sin(an));
    vec2 ecs = vec2(cos(en), sin(en));
    float bn = mod(atan(p.x, p.y), 2.0 * an) - an;
    p = length(p) * vec2(cos(bn), abs(sin(bn)));
    p -= r * acs;
    p += ecs * clamp(-dot(p, ecs), 0.0, r * acs.y / ecs.y);
    return length(p) * sign(p.x);
}

float dot2(vec2 v)
{
    return dot(v, v);
}

float sdHeart(vec2 p)
{
    p = p * 0.5 + vec2(0.0, 0.5);  // about the size of the unit circle, centered on its middle
    p.x = abs(p.x);
    float d;
    if (p.y + p.x > 1.0) {
        d = sqrt(dot2(p - vec2(0.25, 0.75))) - sqrt(2.0) / 4.0;
    } else {
        d = sqrt(min(dot2(p - vec2(0.0, 1.0)), dot2(p - 0.5 * max(p.x + p.y, 0.0)))) * sign(p.x - p.y);
    }
    return d * 2.0;
}

float sdCross(vec2 p, vec2 b)
{
    p = abs(p);
    p = (p.y > p.x) ? p.yx : p.xy;
    vec2 q = p - b;
    float k = max(q.y, q.x);
    vec2 w = (k > 0.0) ? q : vec2(b.y - p.x, -k);
    return sign(k) * length(max(w, 0.0));
}

// Every shape at roughly the size of the unit circle, centered on the origin, point up.
float shapeAt(int shape, vec2 p, float points)
{
    if (shape == 0) return length(p) - 1.0;
    if (shape == 1) return sdBox(p, vec2(1.0));
    if (shape == 2) return sdBox(mat2(0.7071, 0.7071, -0.7071, 0.7071) * p, vec2(0.75));
    if (shape == 3) return sdTriangle(p, 1.0);
    if (shape == 4) return sdHexagon(p, 0.9);
    if (shape == 5) return sdStar(p, 1.0, points, 2.0 + 0.5 * (points - 2.0));
    if (shape == 6) return sdHeart(p);
    return sdCross(p, vec2(1.0, 0.4));
}

vec2 turned(vec2 p, float a)
{
    return mat2(cos(a), -sin(a), sin(a), cos(a)) * p;
}

float scaled(int shape, vec2 p, float rot, float s, float points)
{
    if (s < 1e-5) {
        return FAR_AWAY;
    }
    return shapeAt(shape, turned(p, rot) / s, points) * s;
}

// A straight edge running across the wall along n: below 0 behind it, once it has travelled g.
float barEdge(vec2 p, vec2 n, float g, float margin)
{
    float halfSpan = 0.5 * (abs(n.x) * aspect + abs(n.y));
    float front = -halfSpan - margin + g * (2.0 * halfSpan + 2.0 * margin);
    return dot(p, n) - front;
}

// Signed distance to the edge of a layer's color, at wall point p (in wall heights, centered).
float region(vec4 shapeU, vec4 placeU, float points, vec2 p, float margin)
{
    int shape = int(shapeU.x + 0.5);
    float g = shapeU.y;
    float rot = shapeU.z;
    int exitStyle = int(placeU.z + 0.5);
    float leave = placeU.w;
    vec2 n = vec2(cos(rot), sin(rot));

    if (exitStyle == EXIT_RETREAT) {
        g *= 1.0 - leave;
    }

    float d;
    vec2 o = (placeU.xy - 0.5) * vec2(aspect, 1.0);
    float full = 1.0;
    if (shape == BAR) {
        d = barEdge(p, n, g, margin);
    } else {
        // Grown to g 1, the shape's inscribed circle reaches past the farthest corner.
        vec2 corner = vec2(0.5 * aspect, 0.5) + abs(o);
        float inner = max(-shapeAt(shape, vec2(0.0), points), 0.05);
        full = (length(corner) + margin) / inner;
        d = scaled(shape, p - o, rot, g * full, points);
    }

    float cutter = -FAR_AWAY;
    if (exitStyle == EXIT_PUNCH) {
        if (shape == BAR) {
            cutter = -barEdge(p, n, leave, margin);
        } else {
            cutter = -scaled(shape, p - o, rot, leave * full, points);
        }
    } else if (exitStyle == EXIT_WIPEOFF) {
        cutter = -barEdge(p, n, leave, margin);
    } else if (exitStyle == EXIT_HALFTONE) {
        mat2 grid = mat2(0.7071, -0.7071, 0.7071, 0.7071);
        vec2 q = grid * p / DOT_PITCH;
        vec2 cell = floor(q) + 0.5;
        vec2 at = transpose(grid) * (cell * DOT_PITCH);
        float far = length(vec2(0.5 * aspect, 0.5) + abs(o));
        float k = clamp(leave * (1.0 + DOT_STAGGER) - DOT_STAGGER * length(at - o) / far, 0.0, 1.0);
        float radius = (1.0 - k) * DOT_PITCH * 0.75;
        cutter = length(q - cell) * DOT_PITCH - radius;
    }
    return max(d, cutter);
}

vec4 drawLayer(vec4 under, vec4 shapeU, vec4 placeU, vec4 colorU, vec2 p)
{
    if (shapeU.w < 0.5) {
        return under;
    }
    int exitStyle = int(placeU.z + 0.5);
    float leave = placeU.w;
    float soft = max(uLook.x, px);
    float ink = uLook.y;
    float shadow = uLook.z;
    float margin = soft + ink + 1.5 * shadow + 2.0 * px;
    float alpha = exitStyle == EXIT_FADE ? 1.0 - leave : 1.0;
    float inkKeep = exitStyle == EXIT_HALFTONE ? 1.0 - smoothstep(0.0, INK_LEAVE, leave) : 1.0;

    float d = region(shapeU, placeU, colorU.w, p, margin);
    float fill = 1.0 - smoothstep(-soft, soft, d);
    float inked = 0.0;
    if (ink > 0.0) {
        inked = (1.0 - smoothstep(ink - soft, ink + soft, d)) * inkKeep;
    }
    float shaded = 0.0;
    if (shadow > 0.0) {
        float ds = region(shapeU, placeU, colorU.w, p - vec2(shadow, -shadow), margin);
        shaded = (1.0 - smoothstep(ink - soft, ink + soft, ds)) * inkKeep;
    }

    vec4 result = under;
    result = mix(result, vec4(0.0, 0.0, 0.0, 1.0), shaded * alpha);
    result = mix(result, vec4(0.0, 0.0, 0.0, 1.0), inked * alpha);
    result = mix(result, vec4(colorU.rgb, 1.0), fill * alpha);
    return result;
}

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 res = uTD2DInfos[0].res.zw;
    aspect = res.x / res.y;
    px = 1.0 / res.y;
    vec2 p = (vUV.st - 0.5) * vec2(aspect, 1.0);

    vec4 result = drawLayer(source, uShapeB, uPlaceB, uColorB, p);
    result = drawLayer(result, uShapeA, uPlaceA, uColorA, p);
    fragColor = TDOutputSwizzle(mix(source, result, uLook.w));
}
'''

EFFECT_SOURCE = r'''
import math
import random

SHAPES = ["circle", "square", "diamond", "triangle", "hexagon", "star", "heart", "cross", "bar"]
EXITS = ["fade", "punch", "halftone", "retreat", "wipeoff", "cut"]
BLUE = (0.1451, 0.4510, 1.0)
PINK = (1.0, 0.5529, 1.0)
YELLOW = (0.9529, 0.8667, 0.2431)
RED = (1.0, 0.2588, 0.3020)
CYAN = (0.2667, 0.9725, 1.0)
WHITE = (1.0, 1.0, 1.0)
BLACK = (0.0, 0.0, 0.0)
COLORS = {"blue": BLUE, "pink": PINK, "yellow": YELLOW, "red": RED, "cyan": CYAN, "white": WHITE, "black": BLACK}
CYCLE = ["blue", "pink", "yellow", "red", "cyan"]
POINTS_MIN = 4          # star points at Points 0
POINTS_RANGE = 8
SOFT_MAX = 0.5          # wall heights of edge softness at full Softness
INK_MAX = 0.03          # wall heights of black outline at full Ink
SHADOW_MAX = 0.06       # wall heights of hard shadow offset at full Shadow
SOFT_COVER = 0.35       # a soft hit grows this share of the way to covering the wall
RANDOM_MARGIN = 0.2     # random origins stay this far in from the wall's edges
BACK_MAX = 3.4
BOUNCE_MAX = 0.7
BOUNCES = 4
ELASTIC_TURNS = 2.5
ELASTIC_DAMP = 6.0
MAX_DT = 0.1

_rng = random.Random()
_current = None
_under = None
_level_from = 0.0
_last = None
_last_color = None
_last_shape = None


def reset(block):
    global _current, _under, _level_from, _last
    _current = None
    _under = None
    _level_from = 0.0
    _last = None


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _bounce(t, e):
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


def _pick_color(block):
    # Cycle never repeats the last color, or the color still clearing underneath.
    global _last_color
    choice = block.par.Color.eval()
    if choice == "cycle":
        avoid = {_last_color}
        if _under is not None:
            avoid.add(_under["colorname"])
        choice = _rng.choice([name for name in CYCLE if name not in avoid])
    _last_color = choice
    return choice


def _pick_shape(block):
    global _last_shape
    choice = block.par.Shape.eval()
    if choice == "shuffle":
        choice = _rng.choice([name for name in SHAPES if name != _last_shape])
    _last_shape = choice
    return choice


def _origin(block):
    choice = block.par.Origin.eval()
    if choice == "pad":
        return (_clamped(block.par.Centerx), _clamped(block.par.Centery))
    if choice == "random":
        return (_rng.uniform(RANDOM_MARGIN, 1.0 - RANDOM_MARGIN), _rng.uniform(RANDOM_MARGIN, 1.0 - RANDOM_MARGIN))
    return (0.5, 0.5)


def points_for(block):
    return POINTS_MIN + int(round(POINTS_RANGE * _clamped(block.par.Points)))


def _roll(block, velocity):
    colorname = _pick_color(block)
    return {
        "shape": _pick_shape(block),
        "origin": _origin(block),
        "colorname": colorname,
        "color": COLORS[colorname],
        "points": points_for(block),
        "cover": SOFT_COVER + (1.0 - SOFT_COVER) * velocity,
        "g": 0.0,
        "rot": 0.0,
        "exit": None,
        "leave": 0.0,
    }


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_layer(node, first, layer):
    if layer is None:
        for index in range(first, first + 3):
            _write(node, index, 0.0, 0.0, 0.0, 0.0)
        return
    exit_index = EXITS.index(layer["exit"]) if layer["exit"] else 0
    leave = ease("easeinout", layer["leave"], 0.0) if layer["exit"] else 0.0
    _write(node, first, SHAPES.index(layer["shape"]), layer["g"], layer["rot"], 1.0)
    _write(node, first + 1, layer["origin"][0], layer["origin"][1], exit_index, leave)
    red, green, blue = layer["color"]
    _write(node, first + 2, red, green, blue, layer["points"])


def _write_rest(node):
    for index in range(7):
        _write(node, index, 0.0, 0.0, 0.0, 0.0)


def _retire(layer, exit_style):
    # The shape on screen keeps clearing underneath the new one, from wherever it had got to.
    if layer is None or exit_style == "cut":
        return None
    if layer["exit"] is None:
        layer["exit"] = exit_style
        layer["leave"] = 0.0
    return layer


def apply(block, env):
    global _current, _under, _level_from, _last
    switch = op("switch")
    node = op("wipe")
    if env.stage == "idle":
        switch.par.index = 0
        _write_rest(node)
        reset(block)
        return

    now = absTime.seconds
    dt = 0.0 if _last is None else max(0.0, min(MAX_DT, now - _last))
    _last = now
    release = max(1e-3, block.par.Release.eval())
    exit_style = block.par.Exit.eval()

    # Only a press starts a shape. After a Hard Cut the release frames stay empty.
    if env.started or (_current is None and _under is None and env.stage == "attack"):
        _under = _retire(_current, exit_style)
        _current = _roll(block, env.velocity)
        _level_from = env.level
    elif _under is not None:
        _under["leave"] += dt / release
        if _under["leave"] >= 1.0:
            _under = None

    angle = _clamped(block.par.Angle) * math.tau
    if _current is not None:
        if env.stage in ("attack", "hold"):
            if env.stage == "hold" or _level_from >= 1.0:
                t = 1.0
            else:
                t = (env.level - _level_from) / (1.0 - _level_from)
            grown = ease(block.par.Easing.eval(), t, _clamped(block.par.Overshoot))
            _current["g"] = _current["cover"] * grown
            # Spins into place and lands on Angle.
            _current["rot"] = angle + block.par.Spin.eval() * math.tau * (1.0 - grown)
        elif _current["exit"] is None:
            _current["exit"] = exit_style
            if exit_style == "cut":
                _current = None
        if _current is not None and env.stage == "release":
            _current["leave"] = env.progress

    _write_layer(node, 0, _current)
    _write_layer(node, 3, _under)
    _write(node, 6, SOFT_MAX * _clamped(block.par.Softness) ** 2, INK_MAX * _clamped(block.par.Ink),
           SHADOW_MAX * _clamped(block.par.Shadow), _clamped(block.par.Mix))
    switch.par.index = 1 if (_current is not None or _under is not None) else 0
'''

UNIFORMS = ["uShapeA", "uPlaceA", "uColorA", "uShapeB", "uPlaceB", "uColorB", "uLook"]


def build(library):
    block = new_block(library, "shape_wipe")
    # A hit: the shape grows over Attack, the color holds for Hold, the Exit plays over Release.
    # Gate mode holds the color while pressed. Curve isn't used: Easing shapes the growth.
    add_control_page(block, {"mode": "hit", "attack": 0.3, "hold": 0.25, "release": 0.5, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Shape Wipe")
    add_menu(
        page,
        "Shape",
        "Shape",
        ["circle", "square", "diamond", "triangle", "hexagon", "star", "heart", "cross", "bar", "shuffle"],
        ["Circle", "Square", "Diamond", "Triangle", "Hexagon", "Star", "Heart", "Cross", "Bar (line)", "Shuffle (new each press)"],
        "star",
    )
    add_float(page, "Points", "Star Points", 0.125, 0.0, 1.0, True, True)
    add_menu(
        page,
        "Color",
        "Color",
        ["cycle", "blue", "pink", "yellow", "red", "cyan", "white", "black"],
        ["Cycle (random each press)", "Blue", "Pink", "Yellow", "Red", "Cyan", "White", "Black"],
        "cycle",
    )
    add_menu(
        page,
        "Exit",
        "Exit",
        ["fade", "punch", "halftone", "retreat", "wipeoff", "cut"],
        ["Fade", "Punch Through", "Halftone Out", "Retreat", "Wipe Off", "Hard Cut"],
        "punch",
    )
    add_menu(page, "Origin", "Grows From", ["center", "pad", "random"], ["Center", "Center X/Y", "Random Each Press"], "center")
    add_float(page, "Centerx", "Center X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Centery", "Center Y", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Angle", "Angle (turns; Bar direction)", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Spin", "Spin In (turns)", 0.0, 0.0, 2.0, clamp_low=True)
    easings = ["linear", "easein", "easeout", "easeinout", "back", "bounce", "elastic"]
    easing_labels = ["Linear", "Ease In", "Ease Out", "Ease In-Out", "Back (overshoot)", "Bounce", "Elastic"]
    add_menu(page, "Easing", "Grow Easing", easings, easing_labels, "easeout")
    add_float(page, "Overshoot", "Overshoot / Bounce", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Softness", "Edge Softness", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Ink", "Ink Outline", 0.3, 0.0, 1.0, True, True)
    add_float(page, "Shadow", "Hard Shadow", 0.3, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "wipe_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    node = add_top(block, glslTOP, "wipe", -300)
    node.par.pixeldat = "wipe_shader"
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
