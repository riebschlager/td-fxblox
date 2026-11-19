# Kaleido Burst: hold, a front tears the picture into mandalas, release implodes them

SHADER = r'''
uniform vec4 uFront;   // front radius, lip width, lip energy, core flash
uniform vec4 uFold;    // segments, rings, cells, spin
uniform vec4 uMotion;  // sweep, drift, prism, mix

layout(location = 0) out vec4 fragColor;

const float TAU = 6.2831853;
const float MAX_DOUBLINGS = 2.0;
const float GROOVE = 0.008;       // half width of the line between gear rings
const float REFRACT = 0.05;       // lip displacement inside the front, wall heights
const float PUSH = 0.04;          // lip displacement outside the front, wall heights
const float SEAM_BLEND = 0.15;    // half width of the source blend at a seam, in cells

vec2 fold(vec2 uv)
{
    return 1.0 - abs(1.0 - mod(uv, 2.0));
}

// Neighboring cells are mirror images, so the geometry meets exactly at a seam.
// The source center slides between cells across the seam for the same reason.
float sourceCenter(float k, float cells, float width)
{
    float center = 0.5;
    if (cells > 1.5) {
        center += smoothstep(1.0 - SEAM_BLEND, 1.0 + SEAM_BLEND, k);
        center += smoothstep(2.0 - SEAM_BLEND, 2.0 + SEAM_BLEND, k);
    }
    return center * width;
}

float seamNear(float k, float cells)
{
    if (cells < 1.5) {
        return 0.0;
    }
    float nearest = clamp(round(k), 1.0, cells - 1.0);
    return 1.0 - smoothstep(0.0, 0.12, abs(k - nearest));
}

float sampleChannel(int channel, float radius, float angle, float centerX, vec2 aspect)
{
    vec2 point = vec2(centerX + radius * cos(angle), 0.5 + radius * sin(angle));
    return texture(sTD2DInputs[0], fold(point / aspect))[channel];
}

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec2 place = vUV.st * aspect;
    float cells = uFold.z;
    float width = aspect.x / cells;
    float k = place.x / width;

    float tile = mod(place.x, 2.0 * width);
    float mirrored = tile > width ? -1.0 : 1.0;
    vec2 d = vec2((tile > width ? 2.0 * width - tile : tile) - width * 0.5, place.y - 0.5);
    float r = length(d);
    float rn = r / length(vec2(width * 0.5, 0.5));

    float front = uFront.x;
    float lipWidth = max(uFront.y, 0.001);
    float rimAt = (rn - front) / lipWidth;
    float rim = exp(-rimAt * rimAt);
    float inside = 1.0 - smoothstep(front - lipWidth, front + lipWidth * 0.25, rn);

    float rings = uFold.y;
    float band = min(floor(rn * rings), rings - 1.0);
    float segments = uFold.x * pow(2.0, min(band, MAX_DOUBLINGS));
    float direction = mod(band, 2.0) < 0.5 ? 1.0 : -1.0;
    float sector = TAU / segments;
    float wedge = mod(atan(d.y, d.x) + uFold.w * direction * (band + 1.0), sector);
    float angle = abs(wedge - sector * 0.5) + uMotion.x;

    float energy = uFront.z;
    float radius = r * (1.0 + 0.12 * band) + uMotion.y + rim * REFRACT * energy;
    float prism = uMotion.z * (1.0 + rim * 2.0);
    float centerX = sourceCenter(k, cells, width);
    vec3 mandala = vec3(
        sampleChannel(0, radius * (1.0 + prism), angle + prism * 0.5, centerX, aspect),
        sampleChannel(1, radius, angle, centerX, aspect),
        sampleChannel(2, radius * (1.0 - prism), angle - prism * 0.5, centerX, aspect)
    );

    float nearestRing = round(rn * rings);
    if (nearestRing >= 1.0 && nearestRing < rings) {
        float distanceToRing = abs(rn - nearestRing / rings);
        mandala *= mix(0.35, 1.0, smoothstep(0.0, GROOVE, distanceToRing));
    }

    vec2 outward = vec2(d.x * mirrored, d.y) / max(r, 0.0001);
    vec3 outside = texture(sTD2DInputs[0], fold(vUV.st - outward * rim * PUSH * energy / aspect)).rgb;

    float lip = rim * energy * (1.0 + 2.0 * seamNear(k, cells));
    vec3 color = mix(outside, mandala, inside);
    color *= 1.0 + lip * 1.6;
    color += vec3(lip * 0.25);
    float core = rn / 0.12;
    color += vec3(exp(-core * core) * uFront.w);

    vec4 source = texture(sTD2DInputs[0], vUV.st);
    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uMotion.w), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

TAU = math.pi * 2.0
FRONT_START = 0.08
LIP_MIN = 0.01
LIP_RANGE = 0.19
LIP_CLEAR = 2.5
OPEN_SLOW = 0.5
OPEN_FAST = 0.08
SPIN_SCALE = 3.8      # radians per second at full Spin
SWEEP_RATIO = 0.37
TUNNEL_SCALE = 0.5    # wall heights per second at full Tunnel
WINDUP = 7.0
SUCK = 6.0
PRISM_SCALE = 0.05

_front = 0.0
_open_from = FRONT_START
_opened = 0.0
_collapse_from = None
_spin = 0.0
_sweep = 0.0
_drift = 0.0
_last = None


def reset(block):
    global _front, _open_from, _opened, _collapse_from, _spin, _sweep, _drift, _last
    _front = 0.0
    _open_from = FRONT_START
    _opened = 0.0
    _collapse_from = None
    _spin = 0.0
    _sweep = 0.0
    _drift = 0.0
    _last = None


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y=0.0, z=0.0, w=0.0):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest(node):
    _write(node, 0, -1.0, 0.01, 0.0, 0.0)
    _write(node, 1, 6.0, 1.0, 1.0, 0.0)
    _write(node, 2, 0.0, 0.0, 0.0, 0.0)


def _smoothstep(low, high, value):
    t = max(0.0, min(1.0, (value - low) / (high - low)))
    return t * t * (3.0 - 2.0 * t)


def _opening(block, env, now, target):
    speed = _clamped(block.par.Burst)
    duration = OPEN_SLOW + (OPEN_FAST - OPEN_SLOW) * speed
    duration = max(duration / (0.5 + env.velocity), block.par.Attack.eval(), 0.001)
    t = min(1.0, (now - _opened) / duration)
    return _open_from + (target - _open_from) * (1.0 - (1.0 - t) ** 3)


def _collapsing(block, env):
    global _collapse_from
    if _collapse_from is None:
        _collapse_from = _front
    return _collapse_from * (1.0 - env.progress ** max(0.01, block.par.Curve.eval()))


def apply(block, env):
    global _front, _open_from, _opened, _collapse_from, _spin, _sweep, _drift, _last
    switch = op("switch")
    kaleido = op("kaleido")
    if env.stage == "idle":
        switch.par.index = 0
        reset(block)
        _write_rest(kaleido)
        return

    switch.par.index = 1
    now = absTime.seconds
    dt = 0.0 if _last is None else max(0.0, min(0.1, now - _last))
    _last = now
    if env.started:
        # A press during the implosion catches the front where it is.
        _open_from = max(FRONT_START, _front)
        _opened = now
        _collapse_from = None

    velocity = env.velocity
    lip_width = LIP_MIN + LIP_RANGE * _clamped(block.par.Frontwidth)
    releasing = env.stage == "release"
    if releasing:
        _front = _collapsing(block, env)
    else:
        _front = _opening(block, env, now, 1.0 + lip_width * LIP_CLEAR)

    windup = env.progress ** 2 if releasing else 0.0
    spin_rate = _clamped(block.par.Spin) * SPIN_SCALE * (0.5 + 0.5 * velocity)
    _spin = (_spin + spin_rate * (1.0 + WINDUP * windup) * dt) % TAU
    _sweep = (_sweep + spin_rate * SWEEP_RATIO * dt) % TAU
    _drift += _clamped(block.par.Tunnel) * TUNNEL_SCALE * (1.0 + SUCK * windup) * dt

    base = 3.0 + 9.0 * _clamped(block.par.Segments)
    segments = max(2, int(round(base * (1.0 + velocity))))
    rings = 1 + int(round(4.0 * _clamped(block.par.Rings)))
    cells = 3 if block.par.Centers.eval() == "three" else 1
    energy = 0.4 + 0.6 * velocity
    core = 0.0
    if releasing:
        core = _smoothstep(0.6, 1.0, env.progress) ** 2 * (0.5 + 0.5 * velocity)

    _write(kaleido, 0, _front, lip_width, energy, core)
    _write(kaleido, 1, segments, rings, cells, _spin)
    _write(kaleido, 2, _sweep, _drift, _clamped(block.par.Prism) * PRISM_SCALE * energy, _clamped(block.par.Mix))
'''

UNIFORMS = ["uFront", "uFold", "uMotion"]


def build(library):
    block = new_block(library, "kaleido_burst")
    # Release is the implosion time, and Curve is how late it accelerates.
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.25, "release": 0.4, "curve": 2.5, "velocitydepth": 1.0})
    page = block.appendCustomPage("Kaleido Burst")
    add_menu(page, "Centers", "Centers", ["three", "one"], ["Three", "One"], "three")
    add_float(page, "Segments", "Segments", 0.33, 0.0, 1.0, True, True)
    add_float(page, "Rings", "Gear Rings", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Spin", "Spin", 0.3, 0.0, 1.0, True, True)
    add_float(page, "Tunnel", "Tunnel", 0.3, 0.0, 1.0, True, True)
    add_float(page, "Prism", "Prism", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Burst", "Burst Speed", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Frontwidth", "Front Width", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "kaleido_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    kaleido = add_top(block, glslTOP, "kaleido", -300)
    kaleido.par.pixeldat = "kaleido_shader"
    kaleido.par.outputresolution = "useinput"
    kaleido.par.format = "useinput"
    kaleido.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    kaleido.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(kaleido.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(kaleido)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(kaleido)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
