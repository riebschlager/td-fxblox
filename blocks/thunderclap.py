# Thunderclap: stateless hit, a printed lightning bolt tears across a black wall with
# the video inside it. The wall flips to yellow, the bolt strikes twice, then hard cut.
# Hits during a strike stack, up to three bolts, and they all cut out together.
#
# Built to stay cheap on the full wall. A small pass with no video input draws each
# bolt's distance field, once per strike, one bolt per color channel. The full-size
# pass only reads that field, so its cost doesn't depend on how many bolts are up.

SLOTS = 3
SLOT_POINTS = 15        # trunk 9 + two forks of 3
POINT_COUNT = SLOTS * SLOT_POINTS
BOLT_UNIFORMS = ["uP%d" % i for i in range(POINT_COUNT)] + ["uField"]
UNIFORMS = ["uLook", "uInk", "uSlip"]

BOLT_TEMPLATE = r'''
// Each point is x, y in wall heights, half width, and 1 if it joins the point before it.
// A point of all zeros is an empty entry. Output is signed distance to each bolt's edge.
@@DECLARATIONS@@
uniform vec4 uField;    // wall aspect

layout(location = 0) out vec4 fragColor;

const int SLOT_POINTS = @@SLOT_POINTS@@;
const float FAR = 9.0;

float bolt(vec2 p, vec4 points[SLOT_POINTS])
{
    float best = FAR;
    for (int i = 0; i + 1 < SLOT_POINTS; i++) {
        if (points[i + 1].w < 0.5) {
            continue;
        }
        vec2 a = points[i].xy;
        vec2 b = points[i + 1].xy;
        float len = max(length(b - a), 1e-5);
        vec2 dir = (b - a) / len;

        // Each segment owns the wedge between the bisectors of its corners. Measuring
        // straight across inside that wedge is what keeps every offset's corners mitered.
        vec2 startCut = dir;
        if (i > 0 && points[i].w > 0.5) {
            startCut = normalize(dir + normalize(a - points[i - 1].xy));
        }
        vec2 endCut = dir;
        if (i + 2 < SLOT_POINTS && points[i + 2].w > 0.5) {
            endCut = normalize(dir + normalize(points[i + 2].xy - b));
        }
        if (dot(p - a, startCut) < 0.0 || dot(p - b, endCut) > 0.0) {
            continue;
        }

        float along = clamp(dot(p - a, dir) / len, 0.0, 1.0);
        float across = abs(dot(p - a, vec2(-dir.y, dir.x)));
        best = min(best, across - mix(points[i].z, points[i + 1].z, along));
    }
    return best;
}

void main()
{
    vec2 p = vec2(vUV.s * uField.x, vUV.t);
    vec4 first[SLOT_POINTS] = vec4[SLOT_POINTS](@@SLOT0@@);
    vec4 second[SLOT_POINTS] = vec4[SLOT_POINTS](@@SLOT1@@);
    vec4 third[SLOT_POINTS] = vec4[SLOT_POINTS](@@SLOT2@@);
    fragColor = vec4(bolt(p, first), bolt(p, second), bolt(p, third), 1.0);
}
'''


def _slot_list(slot):
    return ", ".join("uP%d" % (slot * SLOT_POINTS + i) for i in range(SLOT_POINTS))


BOLT_SHADER = (
    BOLT_TEMPLATE
    .replace("@@DECLARATIONS@@", "\n".join("uniform vec4 uP%d;" % i for i in range(POINT_COUNT)))
    .replace("@@SLOT_POINTS@@", str(SLOT_POINTS))
    .replace("@@SLOT0@@", _slot_list(0))
    .replace("@@SLOT1@@", _slot_list(1))
    .replace("@@SLOT2@@", _slot_list(2))
)

SHADER = r'''
uniform vec4 uLook;     // wall flip, dark, video zoom, mix
uniform vec4 uInk;      // accent band, key line, outer ink, palette (0 is brand)
uniform vec4 uSlip;     // yellow plate offset in uv

layout(location = 0) out vec4 fragColor;

const vec3 BLACK = vec3(0.0);
const vec3 YELLOW = @@YELLOW@@;
@@PALETTE_GLSL@@
@@PALETTE_HIGHLIGHTS@@
const int NEWEST = 0;
const int OLDEST = 2;

float cover(float edge, float soft)
{
    return clamp(0.5 - edge / soft, 0.0, 1.0);
}

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec3 color = BLACK;
    int palette = int(uInk.w) - 1;
    vec3 accent = palette < 0 ? YELLOW : paletteHighlight(palette);

    if (uLook.y < 0.5) {
        vec3 field = texture(sTD2DInputs[1], vUV.st).rgb;
        float soft = 1.5 / float(textureSize(sTD2DInputs[0], 0).y);

        if (uLook.x > 0.0) {
            // The flip: yellow wall, bolts printed solid black.
            float shape = min(field.r, min(field.g, field.b));
            color = mix(accent * uLook.x, BLACK, cover(shape, soft));
        } else {
            vec3 plate = field;
            if (uSlip.x != 0.0 || uSlip.y != 0.0) {
                plate = texture(sTD2DInputs[1], vUV.st + uSlip.xy).rgb;
            }
            vec2 place = vec2(0.5) + (vUV.st - vec2(0.5)) / uLook.z;
            for (int i = OLDEST; i >= NEWEST; i--) {
                float d = field[i];
                float yellow = plate[i];
                if (d >= uInk.z + soft && yellow >= soft) {
                    continue;
                }
                // Outer ink only shows where this bolt crosses another one.
                color = mix(color, BLACK, cover(d - uInk.z, soft));
                color = mix(color, accent, cover(yellow, soft));
                color = mix(color, BLACK, cover(d + uInk.x, soft));
                float core = cover(d + uInk.x + uInk.y, soft);
                if (core > 0.0) {
                    color = mix(color, texture(sTD2DInputs[0], place).rgb, core);
                }
            }
        }
    }

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uLook.w), source.a));
}
'''

EFFECT_SOURCE = r'''
import math
import random

SLOTS = 3
SLOT_POINTS = 15
ENTRY = 0.25            # how far past the wall's ends a bolt starts and stops, wall heights
EDGE_MARGIN = 0.1       # corners stay this far inside the top and bottom
PAIRS = (3, 4)          # long stroke and kick-back pairs in one trunk
LONG_ANGLE = (8.0, 28.0)
KICK_ANGLE = (40.0, 78.0)
LONG_STROKE = (0.7, 1.15)
KICK_STROKE = (0.18, 0.34)
GOLDEN = 0.618034       # steps the bolt's height so neighboring hits never bunch up
FAR_TAPER = 0.55        # far end's width as a share of the near end's
WIDTH_LOW = 0.04
WIDTH_RANGE = 0.3
THIN_TAP = 0.1          # a soft tap keeps this share of the width
VELOCITY_POWER = 1.6
MOST_FORKS = 2
FORK_ROOT = 0.6
FORK_MID = 0.4
FORK_TIP = 0.15
FORK_SWING = (0.2, 0.45)
FORK_RETURN = (0.05, 0.2)
FORK_MIN_RUN = 0.45     # a fork always heads for an edge at least this far away
PAST_EDGE = 0.08
PALETTE_INDEX = @@PALETTE_INDEX@@
YELLOW_BAND = 0.03
KEY_LINE = 0.012
OUTER_INK = 0.016
FLIP_SECONDS = 0.04     # the frames right after the strike frame
FLIP_MIN_GAP = 0.35     # a full-wall flip can't repeat sooner: under 3 flashes a second
GAP_SECONDS = 0.025     # dark beat between the two strikes
ZOOM_RANGE = 0.3
SLIP = 0.03             # how far the yellow plate slides off on the second strike
EMPTY = (0.0, 0.0, 0.0, 0.0)

_rng = random.Random()
_bolts = []
_written = {}
_lean = 1.0
_place = 0.0
_flip_allowed = False
_last_flip = None
_started_at = 0.0
_restruck = False
_slip = (0.0, 0.0)


def reset(block):
    global _bolts, _written, _flip_allowed, _last_flip, _restruck, _slip
    _bolts = []
    _written = {}
    _flip_allowed = False
    _last_flip = None
    _restruck = False
    _slip = (0.0, 0.0)


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _lerp(pair, amount):
    return pair[0] + (pair[1] - pair[0]) * amount


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


def _aspect():
    node = op("in1")
    return max(0.01, float(node.width) / max(1.0, float(node.height)))


def _trunk(aspect, jag, width):
    global _lean, _place
    _lean = -_lean
    _place = (_place + GOLDEN) % 1.0
    long_angle = math.radians(_lerp(LONG_ANGLE, jag))
    kick_angle = math.radians(_lerp(KICK_ANGLE, jag))

    points = [(0.0, 0.0)]
    for _ in range(_rng.choice(PAIRS)):
        stroke = _rng.uniform(*LONG_STROKE)
        x, y = points[-1]
        points.append((x + stroke * math.cos(long_angle), y + _lean * stroke * math.sin(long_angle)))
        stroke = _rng.uniform(*KICK_STROKE)
        x, y = points[-1]
        points.append((x + stroke * math.cos(kick_angle), y - _lean * stroke * math.sin(kick_angle)))

    # Stretch across the whole wall, then fit the height. Both only ever soften corners.
    run = aspect + 2.0 * ENTRY
    x_scale = run / points[-1][0]
    ys = [y for _, y in points]
    low, high = min(ys), max(ys)
    room = 1.0 - 2.0 * EDGE_MARGIN
    y_scale = min(1.0, room / max(1e-6, high - low))
    free = room - (high - low) * y_scale
    base = EDGE_MARGIN + free * _place

    last = len(points) - 1
    shaped = []
    for index, (x, y) in enumerate(points):
        taper = 1.0 + (FAR_TAPER - 1.0) * index / last
        shaped.append((x * x_scale - ENTRY, base + (y - low) * y_scale, width * taper))
    if _rng.random() < 0.5:
        # Mirror it, so the wide end enters from either side.
        shaped = [(aspect - x, y, w) for x, y, w in shaped]
    return shaped


def _forks(trunk, count):
    corners = list(range(1, len(trunk) - 1))
    _rng.shuffle(corners)
    forks = []
    side = _rng.choice((0.0, 1.0))
    for corner in sorted(corners[:count]):
        x, y, width = trunk[corner]
        side = 1.0 - side
        target = side
        if abs(target - y) < FORK_MIN_RUN:
            target = 1.0 - target
        tip_y = -PAST_EDGE if target == 0.0 else 1.0 + PAST_EDGE
        swing = _rng.choice((-1.0, 1.0))
        mid_x = x + swing * _rng.uniform(*FORK_SWING)
        forks.append([
            (x, y, width * FORK_ROOT),
            (mid_x, (y + tip_y) * 0.5, width * FORK_MID),
            (mid_x - swing * _rng.uniform(*FORK_RETURN), tip_y, width * FORK_TIP),
        ])
    return forks


def _slot_entries(bolt):
    entries = []
    for line in [bolt["trunk"]] + bolt["forks"]:
        for index, point in enumerate(line):
            entries.append((point[0], point[1], point[2], 0.0 if index == 0 else 1.0))
    return entries + [EMPTY] * (SLOT_POINTS - len(entries))


def _write_bolts():
    entries = []
    for slot in range(SLOTS):
        entries += _slot_entries(_bolts[slot]) if slot < len(_bolts) else [EMPTY] * SLOT_POINTS
    _send(op("bolts"), entries + [(_aspect(), 0.0, 0.0, 0.0)])


def _fork_count(block):
    return int(round(_clamped(block.par.Forks) * MOST_FORKS))


def _strike(block, velocity, now):
    global _bolts, _flip_allowed, _last_flip, _started_at, _restruck, _slip
    width = (WIDTH_LOW + WIDTH_RANGE * _clamped(block.par.Width)) * (THIN_TAP + (1.0 - THIN_TAP) * velocity ** VELOCITY_POWER)
    trunk = _trunk(_aspect(), _clamped(block.par.Jag), width)
    newest = {"trunk": trunk, "forks": _forks(trunk, _fork_count(block))}
    keep = 1 + int(round(_clamped(block.par.Storm) * (SLOTS - 1)))
    _bolts = ([newest] + _bolts)[:keep]

    flip = block.par.Flip.eval() > 0.0
    _flip_allowed = flip and (_last_flip is None or now - _last_flip >= FLIP_MIN_GAP)
    if _flip_allowed:
        _last_flip = now
    _started_at = now
    _restruck = False
    _slip = (0.0, 0.0)


def _restrike(block):
    global _restruck, _slip
    _restruck = True
    for bolt in _bolts:
        bolt["forks"] = _forks(bolt["trunk"], _fork_count(block))
    angle = _rng.uniform(0.0, 2.0 * math.pi)
    _slip = (math.cos(angle) * SLIP, math.sin(angle) * SLIP)


def apply(block, env):
    switch = op("switch")
    clap = op("clap")
    if env.stage == "idle":
        switch.par.index = 0
        _bolts.clear()
        _send(op("bolts"), [EMPTY] * (SLOTS * SLOT_POINTS + 1))
        _send(clap, [EMPTY] * 3)
        return

    switch.par.index = 1
    now = absTime.seconds
    if env.started or not _bolts:
        _strike(block, env.velocity, now)
    if env.stage == "release" and not _restruck:
        _restrike(block)
    _write_bolts()

    elapsed = now - _started_at
    flip = 0.0
    if _flip_allowed and 0.0 < elapsed <= FLIP_SECONDS:
        flip = _clamped(block.par.Flip)

    dark = 0.0
    slip = (0.0, 0.0)
    if env.stage == "release":
        if env.progress * max(0.0, block.par.Release.eval()) < GAP_SECONDS:
            dark = 1.0
        settle = (1.0 - env.progress) ** max(0.01, block.par.Curve.eval())
        slip = (_slip[0] * settle / _aspect(), _slip[1] * settle)

    _send(clap, [
        (flip, dark, 1.0 + ZOOM_RANGE * _clamped(block.par.Punch), _clamped(block.par.Mix)),
        (YELLOW_BAND, KEY_LINE, OUTER_INK, PALETTE_INDEX.get(block.par.Palette.eval(), -1) + 1),
        (slip[0], slip[1], 0.0, 0.0),
    ])
'''


def _sequence(node, names):
    # Vectors is a sequence: entries past the first don't exist until it grows.
    node.seq.vec.numBlocks = len(names)
    for index, name in enumerate(names):
        setattr(node.par, "vec%dname" % index, name)


def build(library):
    block = new_block(library, "thunderclap")
    # Hold is the first strike. Release is the second strike, ending in the hard cut.
    # Curve sets how fast the yellow plate slides back into register.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.16, "release": 0.19, "curve": 2.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Thunderclap")
    add_float(page, "Width", "Bolt Width", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Jag", "Jag", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Forks", "Forks", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Flip", "Wall Flip", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Storm", "Storm", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Punch", "Video Punch-in", 0.5, 0.0, 1.0, True, True)
    add_palette_menu(page, presets={"brand": "Brand"}, default="brand")
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "bolt_shader", BOLT_SHADER, -500, -150)
    shader = palette_shader(SHADER, YELLOW=glsl_rgb(SWATCH_RGB8["yellow"]))
    add_text(block, "clap_shader", shader, -300, -150)

    source = add_top(block, inTOP, "in1", -700)

    # No inputs on purpose: with nothing feeding it, it only cooks when a strike changes it.
    # 16-bit float, not 32, because the full-size pass has to filter it to get clean edges.
    bolts = add_top(block, glslTOP, "bolts", -500)
    bolts.par.pixeldat = "bolt_shader"
    bolts.par.outputresolution = "custom"
    bolts.par.resolutionw = 1680
    bolts.par.resolutionh = 512
    bolts.par.format = "rgba16float"
    _sequence(bolts, BOLT_UNIFORMS)

    clap = add_top(block, glslTOP, "clap", -300)
    clap.par.pixeldat = "clap_shader"
    clap.par.outputresolution = "useinput"
    clap.par.format = "useinput"
    clap.par.inputfiltertype = "linear"
    _sequence(clap, UNIFORMS)

    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    clap.inputConnectors[0].connect(source)
    clap.inputConnectors[1].connect(bolts)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(clap)
    switch.outputConnectors[0].connect(output)

    effect = palette_effect(EFFECT_SOURCE, PALETTE_INDEX=PALETTE_INDEX)
    add_text(block, "effect", effect, -500, -300)
    add_callbacks(block)
    return block
