# Slice Shift: stateless hit, a blade cuts the wall into slabs that zip back into place

SHADER = r'''
uniform vec4 uCut;     // angle in radians, coarse slab count, seed, sliver chance
uniform vec4 uMotion;  // progress of the zip, strike point, stagger, throw
uniform vec4 uLook;    // seam, split, spring, mix

layout(location = 0) out vec4 fragColor;

const float TAU = 6.2831853;
const float WARP = 0.42;          // uneven slab widths; WARP * WARP_RATE must stay under 1
const float WARP_RATE = 1.7;
const float JITTER = 0.1;         // share of the stagger that is random per slab
const float SEAM_WIDTH = 0.006;   // in wall heights
const float SPREAD = 0.1;         // color tear at full split, in wall heights
const int SAMPLES = 8;

float hash(float n)
{
    n = fract(n * 0.1031);
    n *= n + 33.33;
    n *= n + n;
    return fract(n);
}

// 1 when thrown, 0 when home. Spring swings the slab past home before it lands.
float closing(float t)
{
    return pow(1.0 - t, 3.0) * cos(t * TAU * 1.5 * uLook.z);
}

float local(float progress, float delay)
{
    return clamp((progress - delay) / max(1.0 - delay, 0.001), 0.0, 1.0);
}

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    float count = uCut.y;
    float seed = uCut.z;

    // Angle 0 stacks long bands that slide sideways. A quarter turn makes columns.
    vec2 across = vec2(sin(uCut.x), cos(uCut.x));
    vec2 along = vec2(across.y, -across.x);
    float span = aspect.x * abs(across.x) + aspect.y * abs(across.y);
    float place = dot((vUV.st - 0.5) * aspect, across) / span + 0.5;

    float even = place * count;
    float bend = even * WARP_RATE + seed;
    float warped = even + WARP * sin(bend);
    float cell = floor(warped);
    float inside = warped - cell;

    // Some slabs shatter into slivers; the rest stay whole.
    float slab = cell;
    if (hash(cell + 11.3 + seed) < uCut.w) {
        float parts = 2.0 + floor(hash(cell + 47.9 + seed) * 5.0);
        slab = cell + (floor(inside * parts) + 0.5) / parts;
    }

    // The zip spreads out from the strike point, and far slabs wait for it.
    float center = clamp((cell + 0.5) / count, 0.0, 1.0);
    float reach = abs(center - uMotion.y) / max(max(uMotion.y, 1.0 - uMotion.y), 0.001);
    float delay = uMotion.z * mix(reach, hash(slab + 83.1 + seed), JITTER);

    float t = local(uMotion.x, delay);
    float offset = closing(t);
    float speed = abs(offset - closing(local(uMotion.x + 0.01, delay))) * 100.0 / 3.0;
    float impact = pow(1.0 - uMotion.x, 6.0);
    float tear = clamp(max(speed, impact), 0.0, 1.0);

    float heading = hash(slab + 131.7 + seed) < 0.5 ? -1.0 : 1.0;
    float distance_ = 0.35 + 0.65 * hash(slab + 271.3 + seed);
    vec2 slide = along * heading * distance_ * offset * uMotion.w;
    vec2 uv = vUV.st - slide / aspect;

    vec3 color;
    float spread = uLook.y * tear * SPREAD;
    if (spread < 0.0005) {
        color = texture(sTD2DInputs[0], fract(uv)).rgb;
    } else {
        // One run of samples along the slide: red trails, blue leads.
        vec3 total = vec3(0.0);
        color = vec3(0.0);
        for (int i = 0; i < SAMPLES; i++) {
            float x = float(i) / float(SAMPLES - 1);
            vec3 weight = vec3(max(0.0, 1.0 - 2.0 * x), 1.0 - abs(2.0 * x - 1.0), max(0.0, 2.0 * x - 1.0));
            vec2 tap = uv - along * heading * (x - 0.5) * spread / aspect;
            color += texture(sTD2DInputs[0], fract(tap)).rgb * weight;
            total += weight;
        }
        color /= total;
    }

    // Seams sit on the slab cuts, not the slivers, and burn out as each slab lands.
    float edge = min(inside, 1.0 - inside) * span / max(count * (1.0 + WARP * WARP_RATE * cos(bend)), 0.001);
    float heat = clamp(abs(offset) * 2.0, 0.0, 1.0);
    // Never thinner than a pixel, or the line breaks into dots. It fades instead.
    float width = max(SEAM_WIDTH / (1.0 + count / 12.0) * heat, uTD2DInfos[0].res.y * 1.5);
    float line_ = 1.0 - smoothstep(width * 0.5, width, edge);
    color += vec3(line_ * uLook.x * heat);

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uLook.w), source.a));
}
'''

EFFECT_SOURCE = r'''
import math
import random

THROW_SCALE = 1.5
SOFT_THROW = 0.2
STAGGER_SCALE = 0.85
FEWEST = 3
MOST_MIN = 4
MOST_RANGE = 36
SLIVERS_MIN = 0.15
SLIVERS_RANGE = 0.5
SEED_RANGE = 100.0

_rng = random.Random()
_hit = None
_peak = 0.0


def reset(block):
    global _hit, _peak
    _hit = None
    _peak = 0.0


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
    _write(node, 1, 1.0, 0.5, 0.0, 0.0)
    _write(node, 2, 0.0, 0.0, 0.0, 0.0)


def _roll(block, velocity):
    # The cut is fixed for the life of a hit, because changing it mid-air would pop.
    most = MOST_MIN + int(round(_clamped(block.par.Slices) * MOST_RANGE))
    swing = _rng.uniform(-1.0, 1.0) * _clamped(block.par.Randomangle)
    return {
        "angle": (_clamped(block.par.Angle) + swing) * math.pi * 0.5,
        "count": float(FEWEST + int(round((most - FEWEST) * velocity))),
        "seed": _rng.random() * SEED_RANGE,
        "slivers": SLIVERS_MIN + SLIVERS_RANGE * velocity,
        "strike": _rng.random(),
        "velocity": velocity,
    }


def apply(block, env):
    global _hit, _peak
    switch = op("switch")
    slice_op = op("slice")
    if env.stage == "idle":
        switch.par.index = 0
        reset(block)
        _write_rest(slice_op)
        return

    switch.par.index = 1
    if env.started or _hit is None:
        _hit = _roll(block, env.velocity)

    zip_time = 0.0
    if env.stage == "release":
        zip_time = 1.0 - (1.0 - env.progress) ** max(0.01, block.par.Curve.eval())
    else:
        _peak = env.level

    velocity = _hit["velocity"]
    throw = _clamped(block.par.Throw) * THROW_SCALE * _peak * (SOFT_THROW + (1.0 - SOFT_THROW) * velocity)

    _write(slice_op, 0, _hit["angle"], _hit["count"], _hit["seed"], _hit["slivers"])
    _write(slice_op, 1, zip_time, _hit["strike"], _clamped(block.par.Stagger) * STAGGER_SCALE, throw)
    _write(
        slice_op,
        2,
        _clamped(block.par.Seam) * velocity * _peak,
        _clamped(block.par.Split) * velocity * _peak,
        _clamped(block.par.Spring),
        _clamped(block.par.Mix),
    )
'''

UNIFORMS = ["uCut", "uMotion", "uLook"]


def build(library):
    block = new_block(library, "slice_shift")
    # Release is how long the zip takes, and Curve above 1 makes it start faster.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.0, "release": 0.55, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Slice Shift")
    add_float(page, "Slices", "Slices", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Throw", "Throw", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Angle", "Angle", 0.333, 0.0, 1.0, True, True)
    add_float(page, "Randomangle", "Random Angle", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Stagger", "Stagger", 0.7, 0.0, 1.0, True, True)
    add_float(page, "Spring", "Spring", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Seam", "Seam", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Split", "Split", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "slice_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    slice_op = add_top(block, glslTOP, "slice", -300)
    slice_op.par.pixeldat = "slice_shader"
    slice_op.par.outputresolution = "useinput"
    slice_op.par.format = "useinput"
    slice_op.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    slice_op.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(slice_op.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(slice_op)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(slice_op)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
