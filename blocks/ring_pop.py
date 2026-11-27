# Ring Pop: hold, flat printed rings alternate with untouched video, then snap shut into a popping dot

SHADER = r'''
uniform vec4 uTarget;  // center x, center y, twin separation (in wall widths), target count
uniform vec4 uRings;   // ring count, phase in bands, cover (1 is the whole wall), palette size
uniform vec4 uStyle;   // ink width, shadow distance (both wall heights), dot radius in bands, color shift
uniform vec4 uMisc;    // mix, palette (0 is brand)

layout(location = 0) out vec4 fragColor;

const vec3 BLACK = vec3(0.0);
@@SWATCHES@@
@@PALETTE_GLSL@@
const float MAX_INK = 0.3;      // of a band, so a ring always keeps its color
const float MAX_SHADOW = 0.45;  // of a band, so a shadow never reaches the next ring

// Signed distance in wall heights, positive inside a printed band.
float ringField(vec2 uv, vec2 center, vec2 aspect, float width, out float band)
{
    float radius = length((uv - center) * aspect);
    float place = radius / width - uRings.y;
    band = floor(place);
    float across = place - band;
    // The innermost band is a solid disc: it has no inner edge to ink.
    float inner = band <= floor(-uRings.y) ? 1.0 : across;
    float edge = min(inner, 1.0 - across) * width;
    float inside = mod(band, 2.0) < 0.5 ? edge : -edge;
    if (uRings.z >= 1.0) {
        return inside;
    }

    // Rings are counted out from the center, so they appear and vanish whole.
    float farthest = length(max(center, 1.0 - center) * aspect) / width;
    float limit = floor(-uRings.y) + ceil(uRings.z * farthest);
    return min(inside, (limit + uRings.y) * width - radius);
}

float printField(vec2 uv, vec2 aspect, float width, out float tone)
{
    float count = uTarget.w > 1.5 ? 2.0 : 1.0;
    vec2 offset = vec2(uTarget.z * 0.5 * (count - 1.0), 0.0);
    vec2 first = uTarget.xy - offset;
    vec2 second = uTarget.xy + offset;

    float band;
    float inside = ringField(uv, first, aspect, width, band);
    if (count > 1.5) {
        // Twin: a band prints only where exactly one target has ink.
        float otherBand;
        float other = ringField(uv, second, aspect, width, otherBand);
        band = inside > other ? band : otherBand;
        inside = max(min(inside, -other), min(-inside, other));
    }
    tone = mod(floor(band * 0.5) + uStyle.w, uRings.w);

    if (uStyle.z > 0.0) {
        float near = min(length((uv - first) * aspect), length((uv - second) * aspect));
        float dot_ = uStyle.z * width - near;
        if (dot_ > inside) {
            inside = dot_;
            tone = 0.0;
        }
    }
    return inside;
}

void main()
{
    vec4 info = uTD2DInfos[0].res;
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 aspect = vec2(info.z / info.w, 1.0);
    float pixel = 1.0 / info.w;

    // Ring size is measured on the wall, so moving the target doesn't resize it.
    float width = 0.5 * length(aspect) / max(1.0, uRings.x);
    float ink = min(uStyle.x, MAX_INK * width);
    float drop = min(uStyle.y, MAX_SHADOW * width);

    float tone;
    float inside = printField(vUV.st, aspect, width, tone);
    float unused;
    float shadow = printField(vUV.st - vec2(drop, -drop) / aspect, aspect, width, unused);

    // One pixel of antialiasing keeps the edges hard without crawling on the LEDs.
    vec3 color = mix(source.rgb, BLACK, drop > 0.0 ? clamp(shadow / pixel + 0.5, 0.0, 1.0) : 0.0);
    color = mix(color, BLACK, clamp(inside / pixel + 0.5, 0.0, 1.0));
    int palette = int(uMisc.y) - 1;
    int stop = int(tone + 0.5);
    vec3 inkColor = palette < 0 ? INKS[stop] : paletteStop(palette, stop);
    color = mix(color, inkColor, clamp((inside - ink) / pixel + 0.5, 0.0, 1.0));

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uMisc.x), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

SOFT_RINGS = 4          # rings from center to corner on the softest press
MIN_TOP_RINGS = 6       # the Rings knob sets the hardest press, from here
MAX_TOP_RINGS = 24      # to here
MAX_CLICKS = 12.0       # ratchet steps per second at the top of the March knob
CLICKS_PER_BAND = 2.0   # a whole band would swap video and color on every step
CLICK_SECONDS = 0.05    # how long one ratchet step takes to land
BACK = 1.70158          # how far a ratchet step overshoots
PHASE_WRAP = 10.0       # bands: two per print and color pair, five colors
FIRST_COVER = 0.5       # share of the wall already printed on the firing frame
SLAM_SECONDS = 0.08
RINGS_SHARE = 0.7       # of the release, spent cutting rings; the dot gets the rest
SWELL_AT = 2.0 / 3.0    # of the dot's time, when it swells for its last frames
DOT_START = 0.9         # bands
DOT_END = 0.45
SWELL = 1.3
INK_RANGE = 0.02        # wall heights
SHADOW_RANGE = 0.06     # wall heights
MAX_DT = 0.1
PALETTES = {"cover": 1, "target": 2, "candy": @@STOP_COUNT@@}
PALETTE_INDEX = @@PALETTE_INDEX@@

_travel = 0.0
_presses = 0
_fired = None
_last_seconds = None
_held_cover = 0.0


def reset(block):
    global _travel, _presses, _fired, _last_seconds, _held_cover
    _travel = 0.0
    _presses = 0
    _fired = None
    _last_seconds = None
    _held_cover = 0.0


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y=0.0, z=0.0, w=0.0):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest(node):
    _write(node, 0, 0.5, 0.5, 0.0, 1.0)
    _write(node, 1, 4.0, 0.0, 0.0, 1.0)
    _write(node, 2, 0.0, 0.0, 0.0, 0.0)
    _write(node, 3, 0.0, 0.0, 0.0, 0.0)


def _clicks(mode, rate):
    if mode == "smooth":
        return _travel
    whole = math.floor(_travel)
    if rate <= 0.0 or _travel == whole:
        return whole    # no step is landing, as on the first press after a reset
    # Each step lands fast, overshoots a touch, then sits still until the next.
    amount = min(1.0, (_travel - whole) / rate / CLICK_SECONDS) - 1.0
    return whole + (BACK + 1.0) * amount ** 3 + BACK * amount ** 2


def _cover_and_dot(block, env, age):
    global _held_cover
    if env.stage != "release":
        grown = min(1.0, age / SLAM_SECONDS)
        cover = FIRST_COVER + (1.0 - FIRST_COVER) * grown
        if env.stage == "attack":
            cover *= env.level
        _held_cover = cover
        return cover, 0.0

    closed = 1.0 - (1.0 - env.progress) ** max(0.01, block.par.Curve.eval())
    if closed < RINGS_SHARE:
        return _held_cover * (1.0 - closed / RINGS_SHARE), 0.0

    closing = (closed - RINGS_SHARE) / (1.0 - RINGS_SHARE)
    if closing >= SWELL_AT:
        return 0.0, DOT_END * SWELL
    return 0.0, DOT_START + (DOT_END - DOT_START) * closing / SWELL_AT


def apply(block, env):
    global _travel, _presses, _fired, _last_seconds
    switch = op("switch")
    rings = op("rings")
    if env.stage == "idle":
        switch.par.index = 0
        _fired = None
        _last_seconds = None
        _write_rest(rings)
        return

    switch.par.index = 1
    now = absTime.seconds
    if env.started or _fired is None:
        _fired = now
    if env.started:
        # Moving one band swaps video and color, so hammering a key flips the wall.
        _presses += 1
    dt = 0.0 if _last_seconds is None else max(0.0, min(MAX_DT, now - _last_seconds))
    _last_seconds = now

    # The march carries through the release, and the next press picks up here.
    rate = _clamped(block.par.March) * MAX_CLICKS
    _travel = (_travel + rate * dt) % (PHASE_WRAP * CLICKS_PER_BAND)
    phase = _clicks(block.par.Marchmode.eval(), rate) / CLICKS_PER_BAND + _presses

    top = MIN_TOP_RINGS + (MAX_TOP_RINGS - MIN_TOP_RINGS) * _clamped(block.par.Rings)
    count = int(round(SOFT_RINGS + (top - SOFT_RINGS) * env.velocity))
    cover, dot = _cover_and_dot(block, env, now - _fired)
    palette = PALETTES[block.par.Palette.eval()]
    targets = 2.0 if block.par.Targets.eval() == "two" else 1.0

    _write(rings, 0, _clamped(block.par.Centerx), _clamped(block.par.Centery), _clamped(block.par.Spread), targets)
    _write(rings, 1, float(count), phase % PHASE_WRAP, cover, float(palette))
    _write(rings, 2, _clamped(block.par.Ink) * INK_RANGE, _clamped(block.par.Shadow) * SHADOW_RANGE, dot, float((_presses - 1) % palette))
    _write(rings, 3, _clamped(block.par.Mix), PALETTE_INDEX.get(block.par.Colorpalette.eval(), -1) + 1)
'''

UNIFORMS = ["uTarget", "uRings", "uStyle", "uMisc"]


def build(library):
    block = new_block(library, "ring_pop")
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.25, "release": 0.35, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Ring Pop")
    add_float(page, "Rings", "Rings (hardest press)", 0.55, 0.0, 1.0, True, True)
    add_float(page, "March", "March", 0.3, 0.0, 1.0, True, True)
    add_menu(page, "Marchmode", "March Mode", ["ratchet", "smooth"], ["Ratchet", "Smooth"], "ratchet")
    add_menu(page, "Palette", "Palette", ["cover", "target", "candy"], ["Cover", "Target", "Candy"], "cover")
    add_menu(page, "Targets", "Targets", ["one", "two"], ["1", "2 (Twin)"], "one")
    add_float(page, "Spread", "Twin Spread", 0.333, 0.0, 1.0, True, True)
    add_float(page, "Centerx", "Center X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Centery", "Center Y", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Ink", "Ink Line", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Shadow", "Hard Shadow", 0.4, 0.0, 1.0, True, True)
    add_palette_menu(page, "Colorpalette", "Color Palette", presets={"brand": "Brand"}, default="brand")
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    shader = palette_shader(SHADER, SWATCHES=glsl_swatches("INKS", ("blue", "yellow", "pink", "red", "cyan")))
    add_text(block, "rings_shader", shader, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    rings = add_top(block, glslTOP, "rings", -300)
    rings.par.pixeldat = "rings_shader"
    rings.par.outputresolution = "useinput"
    rings.par.format = "useinput"
    rings.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    rings.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(rings.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(rings)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(rings)
    switch.outputConnectors[0].connect(output)

    effect = palette_effect(EFFECT_SOURCE, PALETTE_INDEX=PALETTE_INDEX, STOP_COUNT=STOP_COUNT)
    add_text(block, "effect", effect, -500, -300)
    add_callbacks(block)
    return block
