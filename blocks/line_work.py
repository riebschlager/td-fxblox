# Line Work: tail hit, the frame freezes as a cut-out on flat brand color, ink
# outlines pulse out from its silhouette, and the live video follows the last line.

BACKDROP_SHADER = r'''
// One pixel: rgb is the frame's background color, alpha is how much of the frame differs from it.
uniform vec4 uShape;    // threshold

layout(location = 0) out vec4 fragColor;

const int EDGE = 40;
const int GRID_X = 48;
const int GRID_Y = 16;
const float AGREE = 0.12;

void main()
{
    // The commonest color around the border, so a shape touching one edge can't skew it.
    vec3 edge[EDGE];
    for (int i = 0; i < EDGE; i++) {
        float along = (float(i) + 0.5) / float(EDGE) * 4.0;
        int side = int(along);
        float f = fract(along);
        vec2 place = side == 0 ? vec2(f, 0.01) : side == 1 ? vec2(0.99, f) : side == 2 ? vec2(1.0 - f, 0.99) : vec2(0.01, 1.0 - f);
        edge[i] = texture(sTD2DInputs[0], place).rgb;
    }
    int best = -1;
    vec3 backdrop = vec3(0.0);
    for (int i = 0; i < EDGE; i++) {
        int votes = 0;
        vec3 sum = vec3(0.0);
        for (int j = 0; j < EDGE; j++) {
            if (distance(edge[i], edge[j]) < AGREE) {
                votes++;
                sum += edge[j];
            }
        }
        if (votes > best) {
            best = votes;
            backdrop = sum / float(votes);
        }
    }

    int covered = 0;
    for (int y = 0; y < GRID_Y; y++) {
        for (int x = 0; x < GRID_X; x++) {
            vec2 place = (vec2(x, y) + 0.5) / vec2(GRID_X, GRID_Y);
            if (distance(texture(sTD2DInputs[0], place).rgb, backdrop) > uShape.x) {
                covered++;
            }
        }
    }
    fragColor = vec4(backdrop, float(covered) / float(GRID_X * GRID_Y));
}
'''

SHAPE_SHADER = r'''
// Jump flood seeds. rg is this pixel's uv inside the shape, b says it is a seed, a says the "!" stood in.
uniform vec4 uShape;    // threshold, simplify radius in pixels

layout(location = 0) out vec4 fragColor;

const int RING = 12;
const float MIN_COVER = 0.004;
const float MAX_COVER = 0.92;
float differs(vec2 place, vec3 backdrop)
{
    return distance(texture(sTD2DInputs[0], place).rgb, backdrop) > uShape.x ? 1.0 : 0.0;
}

bool inMark(vec2 place, float aspect)
{
    vec2 p = (place - vec2(0.5)) * vec2(aspect, 1.0);
    float halfWidth = mix(0.045, 0.09, clamp((p.y + 0.12) / 0.46, 0.0, 1.0));
    bool bar = p.y >= -0.12 && p.y <= 0.34 && abs(p.x) < halfWidth;
    bool dot = distance(p, vec2(0.0, -0.27)) < 0.07;
    return bar || dot;
}

void main()
{
    vec4 backdrop = texture(sTD2DInputs[1], vec2(0.5));
    bool useMark = backdrop.a < MIN_COVER || backdrop.a > MAX_COVER;
    bool seed;
    if (useMark) {
        seed = inMark(vUV.st, uTD2DInfos[0].res.z / uTD2DInfos[0].res.w);
    } else {
        // The majority of a small disc, so specks drop out and outlines follow the big shapes.
        float sum = differs(vUV.st, backdrop.rgb);
        vec2 pixel = uShape.y * 4.0 * uTD2DInfos[0].res.xy;  // the input is four times this pass
        for (int i = 0; i < RING; i++) {
            float turn = 6.2831853 * float(i) / float(RING);
            sum += differs(vUV.st + vec2(cos(turn), sin(turn)) * pixel, backdrop.rgb);
        }
        seed = sum / float(RING + 1) > 0.5;
    }
    fragColor = vec4(seed ? vUV.st : vec2(0.0), seed ? 1.0 : 0.0, useMark ? 1.0 : 0.0);
}
'''

FLOOD_SHADER = r'''
// One jump flood pass: every pixel keeps the closest seed its neighbors know about.
uniform vec4 uStep;     // divides the longer side to give this pass's step in pixels

layout(location = 0) out vec4 fragColor;

void main()
{
    vec2 size = uTD2DInfos[0].res.zw;
    vec2 aspect = vec2(size.x / size.y, 1.0);
    float jump = max(1.0, floor(max(size.x, size.y) / uStep.x));
    vec4 here = texture(sTD2DInputs[0], vUV.st);
    vec4 best = here;
    float bestDistance = here.b > 0.5 ? distance(here.xy * aspect, vUV.st * aspect) : 1000.0;
    for (int y = -1; y <= 1; y++) {
        for (int x = -1; x <= 1; x++) {
            vec4 other = texture(sTD2DInputs[0], vUV.st + vec2(x, y) * jump / size);
            if (other.b > 0.5) {
                float away = distance(other.xy * aspect, vUV.st * aspect);
                if (away < bestDistance) {
                    bestDistance = away;
                    best = other;
                }
            }
        }
    }
    best.a = here.a;
    fragColor = best;
}
'''

DISTANCE_SHADER = r'''
// r is the distance to the shape in wall heights, g says the "!" stood in.
layout(location = 0) out vec4 fragColor;

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec4 seed = texture(sTD2DInputs[0], vUV.st);
    float away = seed.b > 0.5 ? distance(seed.xy * aspect, vUV.st * aspect) : 4.0;
    fragColor = vec4(away, seed.a, 0.0, 1.0);
}
'''

INK_SHADER = r'''
// Each wave is flight (0-1), pumped distance in wall heights, line count, unused. Zero lines is an empty slot.
uniform vec4 uWave0;
uniform vec4 uWave1;
uniform vec4 uWave2;
uniform vec4 uWave3;
// Each color is the flat background rgb, then ink: 0 black, 1 white.
uniform vec4 uColor0;
uniform vec4 uColor1;
uniform vec4 uColor2;
uniform vec4 uColor3;
uniform vec4 uLook;     // line spacing in wall heights, line weight (share of spacing), wobble, boil step
uniform vec4 uMisc;     // mix, halftone, newest slot

layout(location = 0) out vec4 fragColor;

const float MIN_REACH = 0.5;        // wall heights, also keeps a full frame from stalling
const float REACH_MARGIN = 1.02;
const float FAR_THIN = 0.45;        // line width left at the far end of its flight
const float WOBBLE_SHARE = 0.33;    // of the spacing, so neighboring lines never touch
const float OUTLINE = 0.009;        // wall heights
const float DOT_CELL = 0.011;       // wall heights

float aspectRatio()
{
    return uTD2DInfos[0].res.z / uTD2DInfos[0].res.w;
}

float hash(vec2 p)
{
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float valueNoise(vec2 p)
{
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

float wobble(vec2 place)
{
    vec2 shift = vec2(uLook.w * 3.17, uLook.w * 1.93);
    return valueNoise(place * 7.0 + shift) * 0.65 + valueNoise(place * 19.0 - shift) * 0.35 - 0.5;
}

vec3 cutout(vec2 place)
{
    float luma = dot(texture(sTD2DInputs[1], vUV.st).rgb, vec3(0.299, 0.587, 0.114));
    float light = smoothstep(0.2, 0.8, luma);
    float hard = luma > 0.5 ? 1.0 : 0.0;
    vec2 cell = mat2(0.7071, -0.7071, 0.7071, 0.7071) * place / DOT_CELL;
    float radius = sqrt(1.0 - light) * 0.75;
    float across = length(fract(cell) - 0.5);
    float width = max(fwidth(across), 0.001);
    float dots = smoothstep(radius - width, radius + width, across);
    return vec3(mix(hard, dots, uMisc.y));
}

vec3 layer(sampler2D field, vec4 wave, vec4 color, bool newest, vec3 below)
{
    if (wave.z <= 0.0) {
        return below;
    }
    vec2 place = vUV.st * vec2(aspectRatio(), 1.0);
    vec4 sample0 = texture(field, vUV.st);
    float raw = sample0.r;

    float reach = MIN_REACH;
    for (int y = 0; y <= 2; y++) {
        for (int x = 0; x <= 2; x++) {
            if (x != 1 || y != 1) {
                reach = max(reach, textureLod(field, vec2(x, y) * 0.5, 0.0).r);
            }
        }
    }
    reach *= REACH_MARGIN;

    float spacing = uLook.x;
    float travel = raw + uLook.z * WOBBLE_SHARE * spacing * wobble(place) * smoothstep(0.0, spacing, raw);
    float packet = max((wave.z - 1.0) * spacing, floor(wave.y / spacing) * spacing);
    float head = wave.x * (reach + packet) + wave.y;
    float tail = head - packet;

    float edge = max(fwidth(travel), 0.0002) * 0.75;
    float covered = smoothstep(tail - edge, tail + edge, travel);
    if (covered <= 0.0) {
        return below;
    }

    vec3 ink = vec3(color.a);
    vec3 result = color.rgb;

    float index = clamp(floor((head - travel) / spacing + 0.5), 0.0, packet / spacing);
    float place0 = head - index * spacing;
    if (place0 >= 0.0) {
        float half0 = 0.5 * uLook.y * spacing * mix(1.0, FAR_THIN, clamp(travel / reach, 0.0, 1.0));
        result = mix(result, ink, 1.0 - smoothstep(half0 - edge, half0 + edge, abs(travel - place0)));
    }

    float grain = 1.0 / float(textureSize(field, 0).y);
    float rim = smoothstep(grain + OUTLINE + edge, grain + OUTLINE - edge, raw);
    result = mix(result, ink, rim);
    float inside = smoothstep(grain + edge, grain - edge, raw);
    vec3 picture = (newest && sample0.g < 0.5) ? cutout(place) : ink;
    result = mix(result, picture, inside);

    return mix(below, result, covered);
}

vec3 layerFor(int slot, int newest, vec3 below)
{
    bool top = slot == newest;
    if (slot == 0) {
        return layer(sTD2DInputs[2], uWave0, uColor0, top, below);
    }
    if (slot == 1) {
        return layer(sTD2DInputs[3], uWave1, uColor1, top, below);
    }
    if (slot == 2) {
        return layer(sTD2DInputs[4], uWave2, uColor2, top, below);
    }
    return layer(sTD2DInputs[5], uWave3, uColor3, top, below);
}

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    int newest = int(uMisc.z + 0.5);
    vec3 color = source.rgb;
    // Oldest first: slots are reused in turn, so the one after the newest is the oldest.
    for (int turn = 1; turn <= 4; turn++) {
        color = layerFor((newest + turn) % 4, newest, color);
    }
    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uMisc.x), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

SLOTS = 4
MIN_LINES = 3
MAX_LINES_LOW = 4.0
MAX_LINES_RANGE = 28.0
VELOCITY_POWER = 1.5
SPACING_LOW = 0.03      # wall heights
SPACING_RANGE = 0.17
WEIGHT_LOW = 0.15       # share of the spacing
WEIGHT_RANGE = 0.55
SIMPLIFY_PIXELS = 12.0  # field pixels, a quarter of wall pixels
THRESHOLD_LOW = 0.05
THRESHOLD_RANGE = 0.6
BOIL_RATE = 16.0        # redraws per second at full
PUMP_LINES = 8.0        # lines per second while a Gate is held
MAX_DT = 0.1
SEQUENCE = ("blue", "pink", "yellow", "red", "cyan")
COLORS = {
    "blue": (0.1451, 0.4510, 1.0, 0.0),
    "pink": (1.0, 0.5529, 1.0, 0.0),
    "yellow": (0.9529, 0.8667, 0.2431, 0.0),
    "red": (1.0, 0.2588, 0.3020, 0.0),
    "cyan": (0.2667, 0.9725, 1.0, 0.0),
    "white": (1.0, 1.0, 1.0, 0.0),
    "black": (0.0, 0.0, 0.0, 1.0),
}
LOOK_ENTRY = SLOTS * 2
MISC_ENTRY = LOOK_ENTRY + 1

_waves = [None] * SLOTS
_newest = SLOTS - 1
_next_color = 0
_last_seconds = None


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y=0.0, z=0.0, w=0.0):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _clear_waves():
    global _last_seconds, _newest
    _last_seconds = None
    _newest = SLOTS - 1
    for slot in range(SLOTS):
        _waves[slot] = None


def reset(block):
    global _next_color
    _clear_waves()
    _next_color = 0
    op("still").par.resetpulse.pulse()
    for slot in range(SLOTS):
        op("wave%d" % slot).par.resetpulse.pulse()


def _write_rest(ink):
    for slot in range(SLOTS):
        _write(ink, slot, 0.0, 0.0, 0.0, 0.0)
        _write(ink, SLOTS + slot, 0.0, 0.0, 0.0, 0.0)
    _write(ink, LOOK_ENTRY, 0.1, 0.0, 0.0, 0.0)
    _write(ink, MISC_ENTRY, 0.0, 0.0, SLOTS - 1)


def _pick_color(block):
    global _next_color
    choice = block.par.Color.eval()
    if choice in COLORS:
        return COLORS[choice]
    name = SEQUENCE[_next_color % len(SEQUENCE)]
    _next_color = (_next_color + 1) % len(SEQUENCE)
    return COLORS[name]


def _launch(block, velocity):
    global _newest
    # A wave still waiting on the envelope leaves now, or it would never take flight.
    if _waves[_newest] is not None and _waves[_newest]["flight_start"] is None:
        _waves[_newest]["flight_start"] = absTime.seconds
    # Reusing slots in turn always replaces the oldest wave, and keeps the draw order simple.
    _newest = (_newest + 1) % SLOTS
    threshold = THRESHOLD_LOW + THRESHOLD_RANGE * _clamped(block.par.Threshold)
    simplify = SIMPLIFY_PIXELS * _clamped(block.par.Simplify)
    _write(op("backdrop"), 0, threshold)
    _write(op("shape"), 0, threshold, simplify)
    op("still").par.activepulse.pulse()
    op("wave%d" % _newest).par.activepulse.pulse()

    most = MAX_LINES_LOW + MAX_LINES_RANGE * _clamped(block.par.Lines)
    lines = int(round(MIN_LINES + max(0.0, most - MIN_LINES) * velocity ** VELOCITY_POWER))
    _waves[_newest] = {
        "lines": lines,
        "color": _pick_color(block),
        "pumped": 0.0,
        "pumping": block.par.Mode.eval() == "gate",
        "flight_start": None,
    }


def apply(block, env):
    global _last_seconds
    switch = op("switch")
    ink = op("ink")
    if env.stage == "idle":
        switch.par.index = 0
        _clear_waves()
        _write_rest(ink)
        return

    switch.par.index = 1
    if env.started:
        _launch(block, env.velocity)

    now = absTime.seconds
    dt = 0.0 if _last_seconds is None else max(0.0, min(MAX_DT, now - _last_seconds))
    _last_seconds = now

    spacing = SPACING_LOW + SPACING_RANGE * _clamped(block.par.Spacing)
    length = max(0.05, block.par.Release.eval())
    curve = max(0.01, block.par.Curve.eval())

    for slot in range(SLOTS):
        wave = _waves[slot]
        if wave is None:
            _write(ink, slot, 0.0, 0.0, 0.0, 0.0)
            continue
        if slot == _newest and env.stage == "release" and wave["flight_start"] is None:
            wave["flight_start"] = now
        if wave["flight_start"] is None:
            if wave["pumping"] and not env.started:
                wave["pumped"] += PUMP_LINES * spacing * dt
            flight = 0.0
        else:
            progress = (now - wave["flight_start"]) / length
            if progress >= 1.0:
                _waves[slot] = None
                _write(ink, slot, 0.0, 0.0, 0.0, 0.0)
                continue
            flight = 1.0 - (1.0 - progress) ** curve
        _write(ink, slot, flight, wave["pumped"], wave["lines"], 0.0)
        _write(ink, SLOTS + slot, *wave["color"])

    boil = _clamped(block.par.Boil) * BOIL_RATE
    _write(
        ink,
        LOOK_ENTRY,
        spacing,
        WEIGHT_LOW + WEIGHT_RANGE * _clamped(block.par.Weight),
        _clamped(block.par.Wobble),
        math.floor(now * boil) % 1024.0 if boil > 0.0 else 0.0,
    )
    _write(ink, MISC_ENTRY, _clamped(block.par.Mix), _clamped(block.par.Halftone), _newest)
'''

SLOT_COUNT = 4
# Enough halvings to reach one pixel across an 8K-wide wall at quarter resolution.
FLOOD_PASSES = 11
FIELD_FORMAT = "rgba32float"
INK_UNIFORMS = [
    "uWave0", "uWave1", "uWave2", "uWave3",
    "uColor0", "uColor1", "uColor2", "uColor3",
    "uLook", "uMisc",
]


def _add_glsl(block, name, x, y, dat, uniforms, resolution, pixel_format, filter_type, op_type=glslTOP):
    node = add_top(block, op_type, name, x)
    node.nodeY = y
    node.par.pixeldat = dat
    node.par.outputresolution = resolution
    node.par.format = pixel_format
    node.par.inputfiltertype = filter_type
    node.seq.vec.numBlocks = max(1, len(uniforms))
    for index, uniform in enumerate(uniforms):
        setattr(node.par, "vec%dname" % index, uniform)
    return node


def _add_cache(block, name, x, y):
    cache = add_top(block, cacheTOP, name, x)
    cache.nodeY = y
    cache.par.active = False
    cache.par.cachesize = 1
    cache.par.outputindex = 0
    return cache


def build(library):
    block = new_block(library, "line_work")
    # Release is how long the last line takes to clear the wall.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.0, "release": 2.0, "curve": 1.3, "velocitydepth": 1.0})
    block.par.Release.normMax = 8.0
    block.par.Release.max = 8.0
    page = block.appendCustomPage("Line Work")
    add_menu(
        page, "Color", "Color",
        ["cycle", "blue", "pink", "yellow", "red", "cyan", "white", "black"],
        ["Cycle", "Blue", "Pink", "Yellow", "Red", "Cyan", "White", "Black"],
        "cycle",
    )
    add_float(page, "Lines", "Most Lines", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Spacing", "Line Spacing", 0.3, 0.0, 1.0, True, True)
    add_float(page, "Weight", "Line Weight", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Wobble", "Hand Wobble", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Boil", "Boil", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Threshold", "Shape Threshold", 0.25, 0.0, 1.0, True, True)
    add_float(page, "Simplify", "Simplify Shape", 0.25, 0.0, 1.0, True, True)
    add_float(page, "Halftone", "Halftone", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "backdrop_shader", BACKDROP_SHADER, -1500, 300)
    add_text(block, "shape_shader", SHAPE_SHADER, -1300, 300)
    add_text(block, "flood_shader", FLOOD_SHADER, -1100, 300)
    add_text(block, "distance_shader", DISTANCE_SHADER, -900, 300)
    add_text(block, "ink_shader", INK_SHADER, -300, 300)

    source = add_top(block, inTOP, "in1", -1700)

    backdrop = _add_glsl(block, "backdrop", -1500, 150, "backdrop_shader", ["uShape"], "custom", FIELD_FORMAT, "linear")
    backdrop.par.resolutionw = 1
    backdrop.par.resolutionh = 1
    shape = _add_glsl(block, "shape", -1300, 0, "shape_shader", ["uShape"], "quarter", FIELD_FORMAT, "nearest")
    source.outputConnectors[0].connect(backdrop)
    shape.inputConnectors[0].connect(source)
    shape.inputConnectors[1].connect(backdrop)

    previous = shape
    for index in range(FLOOD_PASSES):
        flood = _add_glsl(block, "flood%d" % index, -1100, -150 * index, "flood_shader", ["uStep"], "useinput", FIELD_FORMAT, "nearest")
        flood.par.vec0valuex = 2.0 ** (index + 1)
        previous.outputConnectors[0].connect(flood)
        previous = flood
    distance = _add_glsl(block, "distance", -900, 0, "distance_shader", [], "useinput", FIELD_FORMAT, "nearest")
    previous.outputConnectors[0].connect(distance)

    still = _add_cache(block, "still", -700, 150)
    source.outputConnectors[0].connect(still)

    # Six inputs, and a GLSL TOP stops at three. A Multi TOP's inputs are appended
    # in connection order, which is the order the shader reads them.
    ink = _add_glsl(block, "ink", -300, 0, "ink_shader", INK_UNIFORMS, "useinput", "useinput", "linear", glslmultiTOP)
    source.outputConnectors[0].connect(ink)
    still.outputConnectors[0].connect(ink)
    for slot in range(SLOT_COUNT):
        wave = _add_cache(block, "wave%d" % slot, -700, -150 * slot)
        distance.outputConnectors[0].connect(wave)
        wave.outputConnectors[0].connect(ink)

    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(ink)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
