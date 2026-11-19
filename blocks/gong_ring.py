# Gong Ring: tail hit, the picture rings like struck metal, heave first, then a shimmer bloom

SHADER = r'''
// Each strike is origin x, origin y, age in seconds, reach. Reach 0 is an empty slot.
uniform vec4 uStrike0;
uniform vec4 uStrike1;
uniform vec4 uStrike2;
uniform vec4 uStrike3;
// Each band is low, mid, high, impact: how much of each set of modes is ringing.
uniform vec4 uBand0;
uniform vec4 uBand1;
uniform vec4 uBand2;
uniform vec4 uBand3;
uniform vec4 uLook;    // dents, chroma, glint amount, glint threshold
uniform vec4 uMisc;    // mix, node line width in pixels, sheen, node line opacity

layout(location = 0) out vec4 fragColor;

// Two modes per band, deliberately inharmonic. K is rings per wall height in
// radians, W is how fast the mode swings in radians per second.
const float K[6] = float[6](6.5, 10.9, 23.0, 37.3, 73.0, 118.0);
const float W[6] = float[6](11.9, 19.7, 33.3, 48.4, 71.0, 93.6);
const float BAND_MIX[6] = float[6](1.0, 0.6, 1.0, 0.7, 1.0, 0.8);
const float PUSH[6] = float[6](0.2, 0.2, 0.055, 0.055, 0.02, 0.02);  // wall heights
const float SHINE[6] = float[6](0.45, 0.45, 1.0, 1.0, 0.8, 0.8);

const float IMPACT = 0.45;       // first-frames bulge, in wall heights
const float DENT_WARP = 0.02;    // how far hammer marks bend the rings, in wall heights
const vec2 LIGHT = vec2(0.53, 0.848);
const vec3 BRAND_YELLOW = vec3(0.953, 0.867, 0.243);

vec2 fold(vec2 uv)
{
    return 1.0 - abs(1.0 - mod(uv, 2.0));
}

float hammer(vec2 place)
{
    return sin(place.x * 41.0 + 1.3 * sin(place.y * 27.0)) * sin(place.y * 37.0 + 1.7 * sin(place.x * 23.0));
}

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec4 strikes[4] = vec4[4](uStrike0, uStrike1, uStrike2, uStrike3);
    vec4 bands[4] = vec4[4](uBand0, uBand1, uBand2, uBand3);

    float dents = hammer(vUV.st * aspect);
    vec2 push = vec2(0.0);
    float shine = 0.0;
    float height = 0.0;
    float energy = 0.0;

    for (int i = 0; i < 4; i++) {
        vec4 strike = strikes[i];
        vec4 band = bands[i];
        if (strike.w <= 0.0) {
            continue;
        }
        vec2 point = (vUV.st - strike.xy) * aspect;
        float dist = length(point);
        vec2 corner = max(strike.xy, 1.0 - strike.xy) * aspect;
        float radius = dist / max(length(corner), 0.0001);
        float window = 1.0 - smoothstep(strike.w * 0.7, strike.w, radius);
        vec2 dir = point / max(dist, 0.0001);

        // The bulge covers the wall from the first frame, ahead of the rings.
        push += dir * band.w * IMPACT * dist * exp(-dist * 0.9);
        if (window <= 0.0) {
            continue;
        }

        float bent = dist + uLook.x * DENT_WARP * dents;
        float fall = window * smoothstep(0.0, 0.02, dist) / (1.0 + 0.3 * dist);
        float amounts[6] = float[6](band.x, band.x, band.y, band.y, band.z, band.z);
        float slope = 0.0;
        for (int m = 0; m < 6; m++) {
            float amp = amounts[m] * BAND_MIX[m] * fall * cos(W[m] * strike.z);
            float across = sin(K[m] * bent);
            push += dir * across * amp * PUSH[m];
            slope += across * amp * SHINE[m];
            height += cos(K[m] * bent) * amp;
            energy += abs(amp);
        }
        shine += slope * dot(dir, LIGHT);
    }

    vec2 shift = push / aspect;
    vec3 color = vec3(
        texture(sTD2DInputs[0], fold(vUV.st - shift * (1.0 + uLook.y))).r,
        texture(sTD2DInputs[0], fold(vUV.st - shift)).g,
        texture(sTD2DInputs[0], fold(vUV.st - shift * (1.0 - uLook.y))).b
    );

    // Light comes from the image, so the rings read on flat areas too.
    color *= max(0.25, 1.0 + uMisc.z * clamp(shine, -1.2, 1.2) * 0.9);

    // Glints and node lines are hard-edged: one pixel of antialiasing, no glow.
    if (uLook.z > 0.0) {
        float lit = shine + dents * uLook.x * 0.15;
        float edge = max(fwidth(lit), 0.001);
        color = mix(color, BRAND_YELLOW, smoothstep(uLook.w, uLook.w + edge, lit));
    }
    if (uMisc.y > 0.0 && uMisc.w > 0.0) {
        float across = abs(height) / max(fwidth(height), 0.00001);
        float line = 1.0 - smoothstep(uMisc.y, uMisc.y + 1.0, across);
        color = mix(color, vec3(0.0), line * smoothstep(0.02, 0.15, energy) * uMisc.w);
    }

    vec4 source = texture(sTD2DInputs[0], vUV.st);
    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uMisc.x), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

STRIKE_COUNT = 4
EMPTY_STRIKE = (0.5, 0.5, 0.0, 0.0)
EMPTY_BAND = (0.0, 0.0, 0.0, 0.0)
LOOK_ENTRY = STRIKE_COUNT * 2
MISC_ENTRY = LOOK_ENTRY + 1

SWEEP_SECONDS = 0.45    # how long the rings take to reach their full spread
FIRST_REACH = 0.3       # share of the spread already covered on the firing frame
IMPACT_SECONDS = 0.14
CHOKE_RATE = 40.0       # a full Damp is down to about 2% in 100 ms
SILENT = 0.002
MAX_DT = 0.1
CHROMA_SCALE = 0.3
LINE_PIXELS = 14.0
LINE_FADE_IN = 0.35     # seconds after a strike
LINE_FADE_OUT = 0.4     # final share of the ring
LINE_KNOB_FADE = 0.1    # the Node Lines knob fades them up over its first tenth

_strikes = [None] * STRIKE_COUNT
_last_seconds = None


def reset(block):
    global _last_seconds
    _last_seconds = None
    for index in range(STRIKE_COUNT):
        _strikes[index] = None


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y=0.0, z=0.0, w=0.0):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _strike(block, velocity):
    # Take a free slot, or the oldest strike when all four are ringing.
    slot = 0
    oldest = None
    for index, strike in enumerate(_strikes):
        if strike is None:
            slot = index
            break
        if oldest is None or strike["fired"] < oldest:
            oldest = strike["fired"]
            slot = index
    _strikes[slot] = {
        "fired": absTime.seconds,
        "x": _clamped(block.par.Strikex),
        "y": _clamped(block.par.Strikey),
        "velocity": velocity,
        "gain": 1.0,
    }


def _write_rest(node):
    for index in range(STRIKE_COUNT):
        _write(node, index, *EMPTY_STRIKE)
        _write(node, STRIKE_COUNT + index, *EMPTY_BAND)
    _write(node, LOOK_ENTRY, 0.0, 0.0, 0.0, 0.0)
    _write(node, MISC_ENTRY, 0.0, 0.0, 0.0)


def _bands(age, progress, velocity, bloom):
    # The heave goes first. Energy climbs into the mid and high modes after the
    # hit, and soft hits never excite the top of the instrument.
    low = velocity * math.exp(-3.2 * progress)
    mid = velocity ** 1.5 * (0.55 + 0.45 * min(1.0, progress / 0.1)) * math.exp(-3.5 * max(0.0, progress - 0.1))
    peak = 0.15 + 0.2 * bloom
    high = velocity ** 2.5 * bloom * 1.5 * min(1.0, progress / peak) ** 1.5 * math.exp(-1.5 * max(0.0, progress - peak))
    impact = velocity * math.exp(-age / IMPACT_SECONDS)
    return low, mid, high, impact


def apply(block, env):
    global _last_seconds
    switch = op("switch")
    ring = op("ring")
    if env.stage == "idle":
        switch.par.index = 0
        reset(block)
        _write_rest(ring)
        return

    switch.par.index = 1
    if env.started:
        _strike(block, env.velocity)

    now = absTime.seconds
    dt = 0.0 if _last_seconds is None else max(0.0, min(MAX_DT, now - _last_seconds))
    _last_seconds = now

    # Strikes keep their own clocks. Each press restarts the envelope for at
    # least one full ring, so every strike has landed before the block goes idle.
    length = max(0.05, block.par.Release.eval())
    curve = max(0.01, block.par.Curve.eval())
    strength = _clamped(block.par.Strength)
    size = _clamped(block.par.Size)
    bloom = _clamped(block.par.Bloom)
    damp = _clamped(block.par.Damp)

    line_opacity = 0.0
    for index in range(STRIKE_COUNT):
        strike = _strikes[index]
        if strike is None:
            continue
        strike["gain"] *= math.exp(-CHOKE_RATE * damp * dt)
        age = now - strike["fired"]
        progress = age / length
        if progress >= 1.0 or strike["gain"] < SILENT:
            _strikes[index] = None
            _write(ring, index, *EMPTY_STRIKE)
            _write(ring, STRIKE_COUNT + index, *EMPTY_BAND)
            continue
        # The loudest strike sets the opacity, so a new hit can't dip the lines.
        line_opacity = max(
            line_opacity,
            min(1.0, age / LINE_FADE_IN) * min(1.0, (1.0 - progress) / LINE_FADE_OUT) * strike["gain"],
        )
        fade = strength * strike["gain"] * (1.0 - progress) ** curve
        bands = [value * fade for value in _bands(age, progress, strike["velocity"], bloom)]
        spread = 1.0 - (1.0 - min(1.0, age / SWEEP_SECONDS)) ** 2
        reach = 1.3 * size * (0.4 + 0.6 * strike["velocity"]) * (FIRST_REACH + (1.0 - FIRST_REACH) * spread)
        _write(ring, index, strike["x"], strike["y"], age, max(0.001, reach))
        _write(ring, STRIKE_COUNT + index, *bands)

    glint = _clamped(block.par.Glint)
    _write(
        ring,
        LOOK_ENTRY,
        _clamped(block.par.Dents),
        _clamped(block.par.Chroma) * CHROMA_SCALE,
        glint,
        1.25 - 0.8 * glint,
    )
    nodelines = _clamped(block.par.Nodelines)
    _write(
        ring,
        MISC_ENTRY,
        _clamped(block.par.Mix),
        nodelines * LINE_PIXELS,
        _clamped(block.par.Sheen),
        line_opacity * min(1.0, nodelines / LINE_KNOB_FADE),
    )
'''

UNIFORMS = [
    "uStrike0", "uStrike1", "uStrike2", "uStrike3",
    "uBand0", "uBand1", "uBand2", "uBand3",
    "uLook", "uMisc",
]


def build(library):
    block = new_block(library, "gong_ring")
    # Release is how long a strike rings, and Curve is how it fades on the way.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.0, "release": 4.5, "curve": 1.2, "velocitydepth": 1.0})
    block.par.Release.normMax = 8.0
    block.par.Release.max = 8.0
    page = block.appendCustomPage("Gong Ring")
    add_float(page, "Strikex", "Strike X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Strikey", "Strike Y", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Strength", "Strength", 0.9, 0.0, 1.0, True, True)
    add_float(page, "Size", "Size", 0.85, 0.0, 1.0, True, True)
    add_float(page, "Bloom", "Bloom", 0.75, 0.0, 1.0, True, True)
    add_float(page, "Dents", "Hammer Dents", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Chroma", "Chroma", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Glint", "Glint", 0.7, 0.0, 1.0, True, True)
    add_float(page, "Sheen", "Sheen", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Nodelines", "Node Lines", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Damp", "Damp", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "ring_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    ring = add_top(block, glslTOP, "ring", -300)
    ring.par.pixeldat = "ring_shader"
    ring.par.outputresolution = "useinput"
    ring.par.format = "useinput"
    ring.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    ring.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(ring.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(ring)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(ring)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
