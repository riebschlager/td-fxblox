# Gradient Flow: brand-color rings radiate from every shape's edge and flow through the palette

# Measures the frame's tonal range into one pixel, eased over time so cuts don't make the contours jump.
# Input 0 is the picture, input 1 is last frame's result through the feedback loop.
LEVELS_SHADER = r'''
uniform vec4 uLevels;  // x: how far toward this frame's measurement to move (1 jumps straight there)

layout(location = 0) out vec4 fragColor;

const int COLUMNS = 16;
const int ROWS = 9;
const float SPREAD = 1.5;     // deviations either side of the mean that count as the range
const float MIN_SPAN = 0.2;   // the floor: a flat or black clip is never stretched further than this
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

void main()
{
    float sum = 0.0;
    float squares = 0.0;
    for (int y = 0; y < ROWS; y++) {
        for (int x = 0; x < COLUMNS; x++) {
            vec2 uv = (vec2(float(x), float(y)) + 0.5) / vec2(float(COLUMNS), float(ROWS));
            float value = dot(texture(sTD2DInputs[0], uv).rgb, LUMA);
            sum += value;
            squares += value * value;
        }
    }
    float count = float(COLUMNS * ROWS);
    float mean = sum / count;
    float deviation = sqrt(max(0.0, squares / count - mean * mean));
    float reach = max(deviation * SPREAD, MIN_SPAN * 0.5);
    float low = max(0.0, mean - reach);
    float high = max(mean + reach, low + MIN_SPAN);
    vec2 previous = texture(sTD2DInputs[1], vec2(0.5)).rg;
    vec2 range = mix(previous, vec2(low, high), clamp(uLevels.x, 0.0, 1.0));
    fragColor = TDOutputSwizzle(vec4(range, 0.0, 1.0));
}
'''

# Which side of the edge each pixel of the reduced field is on.
MASK_SHADER = r'''
uniform vec4 uMask;  // x: threshold within the measured range

layout(location = 0) out vec4 fragColor;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

void main()
{
    vec2 range = texture(sTD2DInputs[1], vec2(0.5)).rg;
    float threshold = mix(range.r, range.g, uMask.x);
    float luma = dot(texture(sTD2DInputs[0], vUV.st).rgb, LUMA);
    fragColor = TDOutputSwizzle(vec4(step(threshold, luma), 0.0, 0.0, 1.0));
}
'''

# Seeds the distance field: every texel on a shape boundary records the boundary point, in texels.
EDGE_SHADER = r'''
layout(location = 0) out vec4 fragColor;

const ivec2 NEIGHBORS[4] = ivec2[4](ivec2(1, 0), ivec2(-1, 0), ivec2(0, 1), ivec2(0, -1));

void main()
{
    ivec2 size = textureSize(sTD2DInputs[0], 0);
    ivec2 here = ivec2(gl_FragCoord.xy);
    float side = texelFetch(sTD2DInputs[0], here, 0).r;
    vec4 seed = vec4(0.0);
    for (int i = 0; i < 4; i++) {
        ivec2 at = clamp(here + NEIGHBORS[i], ivec2(0), size - 1);
        if (texelFetch(sTD2DInputs[0], at, 0).r != side) {
            // Halfway to the neighbor across the boundary.
            seed = vec4(vec2(here) + 0.5 + vec2(NEIGHBORS[i]) * 0.5, 1.0, 1.0);
            break;
        }
    }
    fragColor = TDOutputSwizzle(seed);
}
'''

# One jump-flood pass: each texel keeps the nearest boundary point seen at this pass's jump length.
# Jumps halve from half the field's long side down to one texel, then stay at one.
JUMP_SHADER = r'''
uniform vec4 uJump;  // x: pass number, from 0

layout(location = 0) out vec4 fragColor;

void main()
{
    ivec2 size = textureSize(sTD2DInputs[0], 0);
    ivec2 here = ivec2(gl_FragCoord.xy);
    vec2 center = vec2(here) + 0.5;
    int jump = int(max(1.0, floor(float(max(size.x, size.y)) * exp2(-(uJump.x + 1.0)))));
    vec4 best = vec4(0.0);
    float nearest = 1e20;
    for (int y = -1; y <= 1; y++) {
        for (int x = -1; x <= 1; x++) {
            ivec2 at = here + ivec2(x, y) * jump;
            if (any(lessThan(at, ivec2(0))) || any(greaterThanEqual(at, size))) {
                continue;
            }
            vec4 candidate = texelFetch(sTD2DInputs[0], at, 0);
            if (candidate.b < 0.5) {
                continue;
            }
            vec2 offset = candidate.rg - center;
            float distance2 = dot(offset, offset);
            if (distance2 < nearest) {
                nearest = distance2;
                best = candidate;
            }
        }
    }
    fragColor = TDOutputSwizzle(best);
}
'''

# Input 0 is the picture, input 1 the nearest-edge field, input 2 the measured range.
MAP_SHADER = r'''
uniform vec4 uFlow;   // palette, rings per wall height, phase, softness
uniform vec4 uReach;  // reach in wall heights, inside amount, strength, mix
uniform vec4 uTone;   // threshold within the measured range, unused x3

layout(location = 0) out vec4 fragColor;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
const float EDGE_MIN = 0.03;   // band edge width at Softness 0, before antialiasing
const float MAX_DIST = 4.0;    // stands in for "no edge anywhere"
const float REACH_EDGE = 0.3;  // the aura's outer edge fades over this share of its reach

// Exact brand values, as 8-bit channels.
const vec3 BLACK = vec3(0.0, 0.0, 0.0) / 255.0;
const vec3 BLUE = vec3(37.0, 115.0, 255.0) / 255.0;
const vec3 PINK = vec3(255.0, 141.0, 255.0) / 255.0;
const vec3 YELLOW = vec3(243.0, 221.0, 62.0) / 255.0;
const vec3 RED = vec3(255.0, 66.0, 77.0) / 255.0;
const vec3 CYAN = vec3(68.0, 248.0, 255.0) / 255.0;
const vec3 WHITE = vec3(255.0, 255.0, 255.0) / 255.0;

const int MAX_STOPS = 6;
const int PALETTES = 5;
const int COUNTS[PALETTES] = int[PALETTES](4, 4, 4, 3, 6);
// One row per palette. The rings cycle through a row and wrap back to its start.
const vec3 STOPS[PALETTES * MAX_STOPS] = vec3[PALETTES * MAX_STOPS](
    BLACK, BLUE, CYAN, WHITE, WHITE, WHITE,    // cover
    BLACK, RED, PINK, YELLOW, YELLOW, YELLOW,  // hot
    BLACK, BLUE, CYAN, YELLOW, YELLOW, YELLOW, // sunburst
    BLACK, YELLOW, WHITE, WHITE, WHITE, WHITE, // ink
    BLACK, BLUE, RED, PINK, CYAN, YELLOW       // disco
);

// Wraps around the row. Index runs from -1 to count, and % is undefined for negatives in GLSL.
vec3 stopColor(int palette, int index)
{
    int count = COUNTS[palette];
    return STOPS[palette * MAX_STOPS + (index + count) % count];
}

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 range = texture(sTD2DInputs[2], vec2(0.5)).rg;
    float threshold = mix(range.r, range.g, uTone.x);
    bool inside = dot(source.rgb, LUMA) >= threshold;

    // The field is coarse, but the distance is measured from this exact pixel, so rings stay smooth.
    ivec2 size = textureSize(sTD2DInputs[1], 0);
    vec2 here = vUV.st * vec2(size);
    vec4 seed = texelFetch(sTD2DInputs[1], clamp(ivec2(here), ivec2(0), size - 1), 0);
    float gap = seed.b > 0.5 ? length(here - seed.rg) / float(size.y) : MAX_DIST;
    float field = inside ? gap : -gap;  // wall heights, positive inside shapes

    int palette = clamp(int(uFlow.x + 0.5), 0, PALETTES - 1);
    float count = float(COUNTS[palette]);
    float ring = field * uFlow.y + uFlow.z;
    float position = fract(ring) * count - 0.5;
    float band = floor(position);
    float width = min(1.0, max(mix(EDGE_MIN, 1.0, uFlow.w), fwidth(ring) * count * 1.5));
    float blend = smoothstep(0.5 - width * 0.5, 0.5 + width * 0.5, position - band);
    vec3 mapped = mix(stopColor(palette, int(band)), stopColor(palette, int(band) + 1), blend);

    float reach = uReach.x;
    float edge = max(0.005, reach * REACH_EDGE);
    float aura = 1.0 - smoothstep(reach - edge, reach, -field);
    float amount = inside ? uReach.y : aura;

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, mapped, amount * uReach.z * uReach.w), source.a));
}
'''

EFFECT_SOURCE = r'''
PALETTES = {"cover": 0.0, "hot": 1.0, "sunburst": 2.0, "ink": 3.0, "disco": 4.0}
MAX_SPEED = 1.5   # palette cycles per second with Flow at either end
MAX_REACH = 0.5   # wall heights the aura spreads past an edge at full Reach
EASE = 0.12       # share of the way the tonal range moves toward each new measurement per frame

_phase = 0.0
_last = None


def reset(block):
    global _phase, _last
    _phase = 0.0
    _last = None
    op("smooth").par.resetpulse.pulse()


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y=0.0, z=0.0, w=0.0):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def apply(block, env):
    global _phase, _last
    switch = op("switch")
    node = op("map")
    if env.stage == "idle":
        switch.par.index = 0
        _write(node, 0, 0.0)
        _write(node, 1, 0.0)
        _write(node, 2, 0.0)
        _last = None
        return

    switch.par.index = 1
    now = absTime.seconds
    dt = 0.0 if _last is None else max(0.0, min(0.1, now - _last))
    _last = now
    # Flow is centered: above the middle rings travel outward, below it they sink inward.
    speed = (_clamped(block.par.Flow) - 0.5) * 2.0 * MAX_SPEED
    # The phase runs on across presses, so a retrigger never jumps the pattern.
    _phase = (_phase + speed * dt) % 1.0

    # A fresh press measures the frame outright, so it never eases in from an old scene.
    _write(op("levels"), 0, 1.0 if env.started else EASE)
    threshold = _clamped(block.par.Threshold)
    _write(op("mask"), 0, threshold)

    level = env.level
    reach = _clamped(block.par.Reach) * MAX_REACH * level * env.velocity
    # Fades the interior in and out over the envelope's ends, so nothing snaps.
    strength = min(1.0, level / 0.15)
    strength = strength * strength * (3.0 - 2.0 * strength)
    rings = 1.0 + 15.0 * _clamped(block.par.Rings)
    palette = PALETTES[block.par.Palette.eval()]
    _write(node, 0, palette, rings, _phase, _clamped(block.par.Softness))
    _write(node, 1, reach, _clamped(block.par.Inside), strength, _clamped(block.par.Mix))
    _write(node, 2, threshold)
'''

JUMP_PASSES = 12  # enough to cross a Half-detail field; extra one-texel passes only tidy the result


def _glsl(block, name, shader, uniforms, x, y=0):
    node = add_top(block, glslTOP, name, x)
    node.nodeY = y
    node.par.pixeldat = shader
    node.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows. It never shrinks below one.
    node.seq.vec.numBlocks = max(1, len(uniforms))
    for index, uniform in enumerate(uniforms):
        setattr(node.par, "vec%dname" % index, uniform)
    return node


def build(library):
    block = new_block(library, "gradient_flow")
    # A short attack and a long release, so the aura swells and recedes instead of snapping.
    add_control_page(block, {"mode": "gate", "attack": 0.08, "hold": 0.25, "release": 0.6, "curve": 1.5, "velocitydepth": 0.7})
    page = block.appendCustomPage("Gradient Flow")
    add_menu(page, "Palette", "Palette", ["cover", "hot", "sunburst", "ink", "disco"], ["Cover", "Hot", "Sunburst", "Ink", "Disco"], "cover")
    add_float(page, "Rings", "Ring Density", 0.33, 0.0, 1.0, True, True)
    add_float(page, "Reach", "Reach", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Flow", "Flow (in / out)", 0.65, 0.0, 1.0, True, True)
    add_float(page, "Softness", "Softness", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Threshold", "Edge Threshold", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Inside", "Fill Inside", 1.0, 0.0, 1.0, True, True)
    # Resolution the distance field is worked out at. Rings are always drawn at full size.
    add_menu(page, "Detail", "Detail", ["half", "quarter", "eighth"], ["Half", "Quarter", "Eighth"], "quarter")
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "levels_shader", LEVELS_SHADER, -1100, -150)
    add_text(block, "mask_shader", MASK_SHADER, -900, -150)
    add_text(block, "edge_shader", EDGE_SHADER, -700, -150)
    add_text(block, "jump_shader", JUMP_SHADER, -500, -150)
    add_text(block, "map_shader", MAP_SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -1300)

    # Last frame's range comes back through this loop. It is 1x1, like the levels pass.
    smooth = add_top(block, feedbackTOP, "smooth", -1300)
    smooth.nodeY = 150
    smooth.par.top = "levels"
    smooth.par.outputresolution = "custom"
    smooth.par.resolutionw = 1
    smooth.par.resolutionh = 1
    smooth.par.format = "rgba32float"
    levels = _glsl(block, "levels", "levels_shader", ["uLevels"], -1100, 150)
    levels.par.outputresolution = "custom"
    levels.par.resolutionw = 1
    levels.par.resolutionh = 1
    levels.par.format = "rgba32float"

    mask = _glsl(block, "mask", "mask_shader", ["uMask"], -900)
    mask.par.outputresolution.expr = "parent().par.Detail.eval()"
    mask.par.format = "rgba16float"
    edge = _glsl(block, "edge", "edge_shader", [], -700)
    edge.par.outputresolution = "useinput"
    # Boundary points are stored in texels, so they need full float precision.
    edge.par.format = "rgba32float"

    previous = edge
    for index in range(JUMP_PASSES):
        jump = _glsl(block, "jump%d" % index, "jump_shader", ["uJump"], -700 + index * 120, -450)
        jump.par.outputresolution = "useinput"
        jump.par.format = "rgba32float"
        jump.par.vec0valuex = float(index)
        previous.outputConnectors[0].connect(jump)
        previous = jump

    node = _glsl(block, "map", "map_shader", ["uFlow", "uReach", "uTone"], -300)
    node.par.outputresolution = "useinput"
    node.par.format = "useinput"
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(smooth)
    levels.inputConnectors[0].connect(source)
    levels.inputConnectors[1].connect(smooth)
    mask.inputConnectors[0].connect(source)
    mask.inputConnectors[1].connect(levels)
    mask.outputConnectors[0].connect(edge)
    node.inputConnectors[0].connect(source)
    node.inputConnectors[1].connect(previous)
    node.inputConnectors[2].connect(levels)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(node)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
