# Radial Burst: stateless hit, highlights blast outward from a point as rays

EXTRACT_SHADER = r'''
// RGB is the image. Alpha is how hard each pixel feeds the rays.
uniform vec2 uExtract;

layout(location = 0) out vec4 fragColor;

void main()
{
    vec4 color = texture(sTD2DInputs[0], vUV.st);
    float luma = dot(color.rgb, vec3(0.2126, 0.7152, 0.0722));
    float heat = max(uExtract.x, smoothstep(0.45, 0.9, luma));
    fragColor = TDOutputSwizzle(vec4(color.rgb, heat));
}
'''

BLUR_SHADER = r'''
// Ten samples along the line to the origin. Each pass covers a tenth of the
// span of the one before it, so three passes act like a thousand samples.
uniform vec3 uBlur;

layout(location = 0) out vec4 fragColor;

void main()
{
    vec2 offset = vUV.st - uBlur.xy;
    vec4 sum = vec4(0.0);
    for (int i = 0; i < 10; i++) {
        float scale = exp(-uBlur.z * float(i) / 10.0);
        sum += texture(sTD2DInputs[0], uBlur.xy + offset * scale);
    }
    fragColor = TDOutputSwizzle(sum / 10.0);
}
'''

COMPOSITE_SHADER = r'''
// Input 0 is the sharp image, input 1 is the streak buffer.
uniform vec4 uShape;  // origin x, origin y, front radius, punch
uniform vec4 uRay;    // glow, smear, chroma, spokes
uniform vec2 uMisc;   // seed, mix

layout(location = 0) out vec4 fragColor;

const float TAU = 6.2831853;
const float EDGE = 0.1;

vec2 fold(vec2 uv)
{
    return 1.0 - abs(1.0 - mod(uv, 2.0));
}

float hash(float n)
{
    return fract(sin(n * 91.3458 + uMisc.x) * 47453.5453);
}

float spoke(float turn, float count)
{
    float x = turn * count;
    float i = floor(x);
    float f = x - i;
    f = f * f * (3.0 - 2.0 * f);
    return mix(hash(mod(i, count)), hash(mod(i + 1.0, count)), f);
}

vec3 rays(vec2 uv)
{
    vec4 streak = texture(sTD2DInputs[1], fold(uv));
    float luma = dot(streak.rgb, vec3(0.2126, 0.7152, 0.0722));
    vec3 tint = min(streak.rgb / max(luma, 0.001), vec3(2.5));
    return mix(vec3(1.0), tint, smoothstep(0.0, 0.08, luma)) * streak.a;
}

void main()
{
    vec2 origin = uShape.xy;
    vec2 offset = vUV.st - origin;
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec2 point = offset * aspect;
    float radius = length(point) / length(max(origin, 1.0 - origin) * aspect);
    float turn = atan(point.y, point.x) / TAU + 0.5;

    float beams = spoke(turn, 47.0) * 0.65 + spoke(turn, 131.0) * 0.35;
    beams = mix(1.0, beams * beams * 2.6, uRay.w);

    float front = uShape.z;
    float inside = 1.0 - smoothstep(front - EDGE, front + EDGE, radius);
    float rim = (radius - front) / EDGE;
    rim = exp(-rim * rim);

    vec3 hot = vec3(
        rays(origin + offset * (1.0 - uRay.z)).r,
        rays(vUV.st).g,
        rays(origin + offset * (1.0 + uRay.z)).b
    );

    vec3 color = texture(sTD2DInputs[0], fold(origin + offset * (1.0 - uShape.w))).rgb;
    color = mix(color, texture(sTD2DInputs[1], vUV.st).rgb, uRay.y * inside);
    color += hot * beams * uRay.x * (inside + rim * 0.8);
    float core = radius / 0.12;
    color += vec3(exp(-core * core) * uRay.x * 0.8);

    vec4 source = texture(sTD2DInputs[0], vUV.st);
    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uMisc.y), source.a));
}
'''

EFFECT_SOURCE = r'''
import math
import random

BLUR_PASSES = ("blur1", "blur2", "blur3")

_fired = None
_seed = 0.0
_scatter = (0.0, 0.0)


def reset(block):
    global _fired, _seed, _scatter
    _fired = None
    _seed = 0.0
    _scatter = (0.0, 0.0)


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y=0.0, z=0.0, w=0.0):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _fire(block):
    global _fired, _seed, _scatter
    _fired = absTime.seconds
    _seed = random.random() * 100.0
    spread = _clamped(block.par.Scatter)
    _scatter = ((random.random() - 0.5) * spread, (random.random() - 0.5) * spread)


def _front(block, velocity):
    length = _clamped(block.par.Front) * 0.4
    if length <= 0.0:
        return 2.0
    age = 0.0 if _fired is None else absTime.seconds - _fired
    ease = 1.0 - (1.0 - min(1.0, age / length)) ** 3
    return 0.25 + (0.1 + velocity) * ease


def apply(block, env):
    switch = op("switch")
    composite = op("composite")
    if env.stage == "idle":
        switch.par.index = 0
        _write(composite, 0, 0.5, 0.5, 2.0, 0.0)
        _write(composite, 1, 0.0, 0.0, 0.0, 0.0)
        _write(composite, 2, 0.0, 0.0)
        for name in BLUR_PASSES:
            op(name).par.vec0valuez = 0.0
        return

    switch.par.index = 1
    if env.started:
        _fire(block)

    level = env.level
    velocity = env.velocity
    energy = level * velocity
    x = max(0.0, min(1.0, _clamped(block.par.Centerx) + _scatter[0]))
    y = max(0.0, min(1.0, _clamped(block.par.Centery) + _scatter[1]))

    reach = min(0.97, _clamped(block.par.Amount) * energy)
    span = -math.log(1.0 - reach)
    _write(op("extract"), 0, _clamped(block.par.Floor))
    for index, name in enumerate(BLUR_PASSES):
        _write(op(name), 0, x, y, span / 10.0 ** index)

    punch = 0.06 * energy
    if env.stage == "release":
        punch -= 0.03 * velocity * math.sin(env.progress * math.pi) * (1.0 - env.progress)
    chroma = _clamped(block.par.Chroma) * 0.06 * (0.2 + 0.8 * env.progress) * energy
    _write(composite, 0, x, y, _front(block, velocity), punch)
    _write(
        composite,
        1,
        _clamped(block.par.Glow) * 3.0 * energy,
        (1.0 - _clamped(block.par.Highlight)) * energy,
        chroma,
        _clamped(block.par.Rays),
    )
    _write(composite, 2, _seed, _clamped(block.par.Mix))
'''


def _add_glsl(block, name, x, shader, uniforms, resolution, pixel_format):
    node = add_top(block, glslTOP, name, x)
    node.par.pixeldat = shader
    node.par.outputresolution = resolution
    node.par.format = pixel_format
    node.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    node.seq.vec.numBlocks = len(uniforms)
    for index, uniform in enumerate(uniforms):
        setattr(node.par, "vec%dname" % index, uniform)
    return node


def build(library):
    block = new_block(library, "radial_burst")
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.03, "release": 0.5, "curve": 2.5, "velocitydepth": 1.0})
    page = block.appendCustomPage("Radial Burst")
    add_float(page, "Amount", "Ray Length", 0.7, 0.0, 1.0, True, True)
    add_float(page, "Centerx", "Center X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Centery", "Center Y", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Scatter", "Scatter", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Rays", "Ray Breakup", 0.7, 0.0, 1.0, True, True)
    add_float(page, "Highlight", "Highlights vs Image", 0.55, 0.0, 1.0, True, True)
    add_float(page, "Floor", "Dark Clip Floor", 0.2, 0.0, 1.0, True, True)
    add_float(page, "Chroma", "Chroma", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Glow", "Glow", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Front", "Wavefront", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "extract_shader", EXTRACT_SHADER, -900, -150)
    add_text(block, "blur_shader", BLUR_SHADER, -700, -150)
    add_text(block, "composite_shader", COMPOSITE_SHADER, -100, -150)

    source = add_top(block, inTOP, "in1", -1100)
    # The extract pass halves the wall. The blurs follow its size, and float
    # keeps the long streaks from banding.
    extract = _add_glsl(block, "extract", -900, "extract_shader", ["uExtract"], "half", "rgba16float")
    blur1 = _add_glsl(block, "blur1", -700, "blur_shader", ["uBlur"], "useinput", "rgba16float")
    blur2 = _add_glsl(block, "blur2", -500, "blur_shader", ["uBlur"], "useinput", "rgba16float")
    blur3 = _add_glsl(block, "blur3", -300, "blur_shader", ["uBlur"], "useinput", "rgba16float")
    composite = _add_glsl(block, "composite", -100, "composite_shader", ["uShape", "uRay", "uMisc"], "useinput", "useinput")
    switch = add_top(block, switchTOP, "switch", 100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 300)

    source.outputConnectors[0].connect(extract)
    extract.outputConnectors[0].connect(blur1)
    blur1.outputConnectors[0].connect(blur2)
    blur2.outputConnectors[0].connect(blur3)
    composite.inputConnectors[0].connect(source)
    composite.inputConnectors[1].connect(blur3)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(composite)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
