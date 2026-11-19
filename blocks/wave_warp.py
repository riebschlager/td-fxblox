# Wave Warp: a sine displacement across the wall, played as a hit that settles like jelly

SHADER = r'''
uniform vec4 uWave;    // angle in radians, wavelength in wall heights, travel phase, direction
uniform vec4 uMotion;  // amplitude in wall heights, standing, ring phase, shape
uniform vec4 uLook;    // spread, mix, unused, unused

layout(location = 0) out vec4 fragColor;

const float TAU = 6.2831853;
const float PI = 3.1415927;
const float SQUARE_EDGE = 12.0;   // how hard the square shape snaps; higher is harsher

// Sine, then triangle, square and saw as Shape runs 0 to 1. Every shape peaks at
// the same place as the sine, so turning the knob morphs without the wave jumping.
float shaped(float p, float shape)
{
    float s = sin(p);
    float tri = asin(clamp(s, -1.0, 1.0)) * 2.0 / PI;
    float square = clamp(s * SQUARE_EDGE, -1.0, 1.0);
    float saw = 2.0 * fract(p / TAU + 0.25) - 1.0;
    float stage = shape * 3.0;
    if (stage < 1.0) return mix(s, tri, stage);
    if (stage < 2.0) return mix(tri, square, stage - 1.0);
    return mix(square, saw, stage - 2.0);
}

// Traveling at Standing 0. At 1 the crests stay put and flap in place, like a plucked string.
float wave(float place, float offset)
{
    float k = TAU / max(uWave.y, 0.0001);
    float traveling = shaped(place * k - uWave.z + offset, uMotion.w);
    float standing = shaped(place * k + offset, uMotion.w) * cos(uMotion.z);
    return mix(traveling, standing, uMotion.y);
}

// Mirror past the edges, so a big push never pulls in black or a hard repeat seam.
vec2 mirrored(vec2 uv)
{
    return 1.0 - abs(1.0 - mod(uv, 2.0));
}

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec4 source = texture(sTD2DInputs[0], vUV.st);

    // Angle 0 runs the wave down the wall, so rows slide sideways. A half turn comes back round.
    vec2 runs = vec2(sin(uWave.x), cos(uWave.x));
    vec2 across = vec2(runs.y, -runs.x);
    vec2 push = normalize(mix(across, runs, uWave.w));
    // Measured from the middle of the wall, so a stretching wavelength opens out from the center.
    float place = dot((vUV.st - 0.5) * aspect, runs);

    vec2 shift = push * uMotion.x / aspect;
    vec3 color;
    if (uLook.x < 0.0005) {
        color = texture(sTD2DInputs[0], mirrored(vUV.st - shift * wave(place, 0.0))).rgb;
    } else {
        // Each channel rides the wave a little ahead or behind the others.
        float lead = uLook.x * PI * 0.5;
        color.r = texture(sTD2DInputs[0], mirrored(vUV.st - shift * wave(place, -lead))).r;
        color.g = texture(sTD2DInputs[0], mirrored(vUV.st - shift * wave(place, 0.0))).g;
        color.b = texture(sTD2DInputs[0], mirrored(vUV.st - shift * wave(place, lead))).b;
    }

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uLook.y), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

AMPLITUDE_SCALE = 0.12   # wall heights of push with Amplitude at full
SHORTEST = 0.02          # wall heights per cycle with Wavelength at 0
LONGEST = 2.0            # wall heights per cycle with Wavelength at 1
RELAX_SCALE = 3.0        # how many extra wavelengths the wave stretches by as it settles
MAX_DT = 0.1
TAU = math.pi * 2.0

_travel = 0.0
_ring = 0.0
_last = None


def reset(block):
    global _travel, _ring, _last
    _travel = 0.0
    _ring = 0.0
    _last = None


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest(node):
    _write(node, 0, 0.0, 1.0, 0.0, 0.0)
    _write(node, 1, 0.0, 0.0, 0.0, 0.0)
    _write(node, 2, 0.0, 0.0, 0.0, 0.0)


def wavelength(block, level):
    base = SHORTEST * (LONGEST / SHORTEST) ** _clamped(block.par.Wavelength)
    return base * (1.0 + RELAX_SCALE * _clamped(block.par.Relax) * (1.0 - level))


def apply(block, env):
    global _travel, _ring, _last
    switch = op("switch")
    warp = op("warp")
    if env.stage == "idle":
        switch.par.index = 0
        _last = None
        _write_rest(warp)
        return

    now = absTime.seconds
    dt = 0.0 if _last is None else max(0.0, min(MAX_DT, now - _last))
    _last = now

    step = TAU * block.par.Speed.eval() * dt
    _travel = (_travel + step) % TAU
    # Every hit plucks the standing wave from its widest swing.
    _ring = 0.0 if env.started else (_ring + step) % TAU

    switch.par.index = 1
    amplitude = _clamped(block.par.Amplitude) * AMPLITUDE_SCALE * env.level * env.velocity
    _write(warp, 0, _clamped(block.par.Angle) * math.pi, wavelength(block, env.level), _travel, _clamped(block.par.Direction))
    _write(warp, 1, amplitude, _clamped(block.par.Standing), _ring, _clamped(block.par.Shape))
    _write(warp, 2, _clamped(block.par.Spread), _clamped(block.par.Mix), 0.0, 0.0)
'''

UNIFORMS = ["uWave", "uMotion", "uLook"]


def build(library):
    block = new_block(library, "wave_warp")
    # A wobble hit that settles over Release. Switch Mode to Gate to hold the warp while pressed.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.0, "release": 0.6, "curve": 1.5, "velocitydepth": 1.0})
    page = block.appendCustomPage("Wave Warp")
    add_float(page, "Amplitude", "Amplitude", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Wavelength", "Wavelength", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Angle", "Angle", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Direction", "Direction (Across to Along)", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Speed", "Speed (cycles/s)", 1.0, -4.0, 4.0)
    add_float(page, "Standing", "Standing", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Relax", "Relax", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Shape", "Shape (Sine Tri Square Saw)", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Spread", "Spread", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "warp_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    warp = add_top(block, glslTOP, "warp", -300)
    warp.par.pixeldat = "warp_shader"
    warp.par.outputresolution = "useinput"
    warp.par.format = "useinput"
    warp.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    warp.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(warp.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(warp)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(warp)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
