# Shockwave: stateless hit, up to four blast fronts refract the picture as they cross the wall

SHADER = r'''
// Each ring is origin x, origin y, front radius, amplitude. Amplitude 0 is an empty slot.
uniform vec4 uRing0;
uniform vec4 uRing1;
uniform vec4 uRing2;
uniform vec4 uRing3;
uniform vec4 uWidths;  // one wave width per ring
uniform vec4 uLook;    // roundness, aftershock, chroma, lensing
uniform vec2 uMisc;    // time, mix

layout(location = 0) out vec4 fragColor;

const float TAU = 6.2831853;
const float PUSH = 0.14;     // peak displacement, in wall heights
const float HAZE = 0.008;    // heat shimmer displacement, in wall heights

vec2 fold(vec2 uv)
{
    return 1.0 - abs(1.0 - mod(uv, 2.0));
}

// t is distance behind the front, in wave widths. A steep leading edge, then a
// long fall through zero into the suck-back, then the aftershock.
float blast(float t)
{
    if (t < 0.0) {
        return exp(t * 14.0);
    }
    float wake = smoothstep(0.7, 1.5, t) * sin((t - 1.0) * TAU * 0.7) * exp(-(t - 1.0) * 0.9);
    return (1.0 - t) * exp(-2.2 * t) + uLook.y * 0.55 * wake;
}

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    float roundness = uLook.x;
    vec4 rings[4] = vec4[4](uRing0, uRing1, uRing2, uRing3);

    vec2 push = vec2(0.0);
    float press = 0.0;
    float haze = 0.0;
    for (int i = 0; i < 4; i++) {
        vec4 ring = rings[i];
        if (ring.w <= 0.0) {
            continue;
        }
        // Roundness 0 ignores height, which turns the circle into flat fronts.
        vec2 point = (vUV.st - ring.xy) * aspect * vec2(1.0, roundness);
        float dist = length(point);
        vec2 corner = max(ring.xy, 1.0 - ring.xy) * aspect * vec2(1.0, roundness);
        float radius = dist / max(length(corner), 0.0001);
        float t = (ring.z - radius) / max(uWidths[i], 0.001);

        float wave = blast(t) * ring.w * smoothstep(0.0, 0.02, radius);
        push += vec2(point.x, point.y * roundness) / max(dist, 0.0001) * wave;
        press += wave;
        if (t > 0.0) {
            haze += ring.w * smoothstep(0.0, 0.3, t) * exp(-t * 1.2);
        }
    }

    vec2 place = vUV.st * aspect;
    vec2 ripple = vec2(
        sin(place.y * 83.0 + uMisc.x * 31.0) + sin(place.x * 47.0 - uMisc.x * 23.0),
        sin(place.x * 71.0 - uMisc.x * 37.0) + sin(place.y * 53.0 + uMisc.x * 19.0)
    );
    vec2 shift = (push * PUSH + ripple * haze * uLook.y * HAZE) / aspect;

    vec3 color = vec3(
        texture(sTD2DInputs[0], fold(vUV.st - shift * (1.0 + uLook.z))).r,
        texture(sTD2DInputs[0], fold(vUV.st - shift)).g,
        texture(sTD2DInputs[0], fold(vUV.st - shift * (1.0 - uLook.z))).b
    );

    // Light comes from the image: compressed areas brighten, stretched ones
    // darken. The small crest term keeps the front visible on dark clips.
    float pressure = clamp(press, -1.5, 1.5);
    color *= max(0.25, 1.0 + uLook.w * pressure * 1.4);
    color += vec3(pow(max(pressure, 0.0), 3.0) * uLook.w * 0.12);

    vec4 source = texture(sTD2DInputs[0], vUV.st);
    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uMisc.y), source.a));
}
'''

EFFECT_SOURCE = r'''
RING_COUNT = 4
FRONT_START = 0.1
FRONT_TRAVEL = 1.2
WIDTH_SCALE = 0.45
CHROMA_SCALE = 0.35
EMPTY = (0.5, 0.5, -1.0, 0.0)

_rings = [None] * RING_COUNT


def reset(block):
    for index in range(RING_COUNT):
        _rings[index] = None


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y=0.0, z=0.0, w=0.0):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _launch(block, velocity):
    # Take a free slot, or the oldest ring when all four are flying.
    slot = 0
    oldest = None
    for index, ring in enumerate(_rings):
        if ring is None:
            slot = index
            break
        if oldest is None or ring["fired"] < oldest:
            oldest = ring["fired"]
            slot = index
    _rings[slot] = {
        "fired": absTime.seconds,
        "x": _clamped(block.par.Originx),
        "y": _clamped(block.par.Originy),
        "velocity": velocity,
    }


def _write_rest(node):
    for index in range(RING_COUNT):
        _write(node, index, *EMPTY)
    _write(node, RING_COUNT, 0.0, 0.0, 0.0, 0.0)
    _write(node, RING_COUNT + 1, 1.0, 0.0, 0.0, 0.0)
    _write(node, RING_COUNT + 2, 0.0, 0.0)


def apply(block, env):
    switch = op("switch")
    shock = op("shock")
    if env.stage == "idle":
        switch.par.index = 0
        reset(block)
        _write_rest(shock)
        return

    switch.par.index = 1
    if env.started:
        _launch(block, env.velocity)

    # Rings keep their own clocks. Each press restarts the envelope for at least
    # one flight, so every ring lands before the block goes idle.
    flight = max(0.05, block.par.Release.eval())
    curve = max(0.01, block.par.Curve.eval())
    strength = _clamped(block.par.Strength)
    width = _clamped(block.par.Width) * WIDTH_SCALE
    widths = [0.0] * RING_COUNT

    for index in range(RING_COUNT):
        ring = _rings[index]
        progress = 1.0
        if ring is not None:
            progress = (absTime.seconds - ring["fired"]) / flight
        if progress >= 1.0:
            _rings[index] = None
            _write(shock, index, *EMPTY)
            continue
        # The front is quickest off the origin and starts tight, then widens.
        ease = 1.0 - (1.0 - progress) ** 1.6
        amplitude = strength * ring["velocity"] * (1.0 - progress) ** curve
        _write(shock, index, ring["x"], ring["y"], FRONT_START + FRONT_TRAVEL * ease, amplitude)
        widths[index] = width * (0.5 + 0.5 * ring["velocity"]) * (0.35 + 0.65 * ease)

    _write(shock, RING_COUNT, *widths)
    _write(
        shock,
        RING_COUNT + 1,
        _clamped(block.par.Roundness),
        _clamped(block.par.Aftershock),
        _clamped(block.par.Chroma) * CHROMA_SCALE,
        _clamped(block.par.Lensing),
    )
    _write(shock, RING_COUNT + 2, absTime.seconds % 100.0, _clamped(block.par.Mix))
'''

UNIFORMS = ["uRing0", "uRing1", "uRing2", "uRing3", "uWidths", "uLook", "uMisc"]


def build(library):
    block = new_block(library, "shockwave")
    # Release is the flight time of a ring, and Curve is how it fades on the way.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.0, "release": 0.7, "curve": 0.5, "velocitydepth": 1.0})
    page = block.appendCustomPage("Shockwave")
    add_float(page, "Originx", "Origin X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Originy", "Origin Y", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Strength", "Strength", 0.75, 0.0, 1.0, True, True)
    add_float(page, "Width", "Width", 0.45, 0.0, 1.0, True, True)
    add_float(page, "Roundness", "Roundness", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Chroma", "Chroma", 0.45, 0.0, 1.0, True, True)
    add_float(page, "Aftershock", "Aftershock", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Lensing", "Lensing", 0.55, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "shock_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    shock = add_top(block, glslTOP, "shock", -300)
    shock.par.pixeldat = "shock_shader"
    shock.par.outputresolution = "useinput"
    shock.par.format = "useinput"
    shock.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    shock.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(shock.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(shock)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(shock)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
