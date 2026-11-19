# Halftone Hit: the frame prints as glowing dots, then the dots swell back into the picture

SHADER = r'''
uniform vec4 uScreen;  // cell size, plate split (wall heights), roll, swell
uniform vec4 uOrigin;  // origin x, origin y, ink floor, mix
uniform vec4 uStyle;   // shape (0 dot, 1 ring, 2 bolt), ink (0 light, 1 paper), unused, unused

layout(location = 0) out vec4 fragColor;

const float TAU = 6.2831853;
const float DOT_MAX = 0.72;   // radius of a full dot, in cells: just past the cell corner
const float BOLT_MIN = 0.25;  // scale of a bolt in an empty cell
const float BOLT_MAX = 1.25;  // scale of a full bolt, in cells
const float BOLT_FAT = 0.04;  // thickens the bolts, in cells
const float INK_SETTLE = 0.45;  // swell by which the dots carry only the picture
const float RING_COVER = 0.75;
const vec3 ANGLES = vec3(0.2618, 1.3090, 0.7854);  // 15, 75 and 45 degrees

const int BOLT_POINTS = 7;
const vec2 BOLT[BOLT_POINTS] = vec2[BOLT_POINTS](
    vec2(0.10, 0.60), vec2(-0.28, -0.06), vec2(-0.04, -0.06), vec2(-0.12, -0.60),
    vec2(0.28, 0.10), vec2(0.04, 0.10), vec2(0.25, 0.60)
);

vec2 res;
float cell;
float swell;
bool paper;

float sdBolt(vec2 p)
{
    float d = dot(p - BOLT[0], p - BOLT[0]);
    float s = 1.0;
    for (int i = 0, j = BOLT_POINTS - 1; i < BOLT_POINTS; j = i, i++) {
        vec2 e = BOLT[j] - BOLT[i];
        vec2 w = p - BOLT[i];
        vec2 b = w - e * clamp(dot(w, e) / dot(e, e), 0.0, 1.0);
        d = min(d, dot(b, b));
        bvec3 c = bvec3(p.y >= BOLT[i].y, p.y < BOLT[j].y, e.x * w.y > e.y * w.x);
        if (all(c) || all(not(c))) {
            s = -s;
        }
    }
    return s * sqrt(d);
}

// Paper mode prints the inverse, so light and paper share all of the dot logic.
float tone(vec3 color, int channel)
{
    return paper ? 1.0 - color[channel] : color[channel];
}

float inkAt(vec2 px, int channel)
{
    vec3 color = texture(sTD2DInputs[0], clamp(px / res, 0.0, 1.0)).rgb;
    float inkFloor = uOrigin.z;
    return inkFloor + (1.0 - inkFloor) * clamp(tone(color, channel), 0.0, 1.0);
}

float cover(float d, float value)
{
    float edge = 0.75 / cell;
    // Lifts an empty cell clear of the edge so it prints nothing, until the swell.
    float lift = edge * (1.0 - swell);
    return (value > 0.0 || swell > 0.0) ? 1.0 - smoothstep(-edge, edge, d + lift) : 0.0;
}

float ringPlate(int channel, vec2 center, vec2 px)
{
    vec2 toPixel = px - center;
    float r = length(toPixel) / cell;
    vec2 direction = toPixel / max(length(toPixel), 0.0001);
    float nearest = floor(r - 0.5) + 0.5;
    float mask = 0.0;
    for (int i = 0; i < 2; i++) {
        float ring = abs(nearest + float(i));
        float value = inkAt(center + direction * ring * cell, channel);
        float halfWidth = mix(value * 0.5, RING_COVER, swell);
        mask = max(mask, cover(abs(r - ring) - halfWidth, value));
    }
    return mask;
}

// Checks the four nearest lattice points, because a swollen dot reaches past its own cell.
float gridPlate(int channel, vec2 center, vec2 px, float angle, bool bolt)
{
    float c = cos(angle);
    float s = sin(angle);
    mat2 toGrid = mat2(c, -s, s, c);
    mat2 fromGrid = mat2(c, s, -s, c);
    vec2 g = toGrid * (px - center) / cell;
    vec2 base = floor(g);
    float mask = 0.0;
    for (int y = 0; y < 2; y++) {
        for (int x = 0; x < 2; x++) {
            vec2 id = base + vec2(float(x), float(y));
            vec2 f = g - id;
            float value = inkAt(center + fromGrid * id * cell, channel);
            float d;
            if (bolt) {
                float scale = mix(BOLT_MIN, BOLT_MAX, sqrt(value));
                d = sdBolt(f / scale) * scale - BOLT_FAT - swell;
            } else {
                d = length(f) - mix(sqrt(value) * DOT_MAX, 1.0, swell);
            }
            mask = max(mask, cover(d, value));
        }
    }
    return mask;
}

void main()
{
    res = uTD2DInfos[0].res.zw;
    cell = max(uScreen.x * res.y, 2.0);
    swell = uScreen.w;
    paper = uStyle.y > 0.5;

    int shape = int(uStyle.x + 0.5);
    vec2 px = vUV.st * res;
    vec2 origin = uOrigin.xy * res;
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec3 printed;

    for (int channel = 0; channel < 3; channel++) {
        float heading = uScreen.z + TAU * (float(channel) / 3.0 + 0.25);
        vec2 center = origin + vec2(cos(heading), sin(heading)) * uScreen.y * res.y;
        float mask;
        if (shape == 1) {
            mask = ringPlate(channel, center, px);
        } else if (shape == 2) {
            mask = gridPlate(channel, center, px, uScreen.z, true);
        } else {
            mask = gridPlate(channel, center, px, uScreen.z + ANGLES[channel], false);
        }
        // Dots start as solid ink, and turn to the live picture early so the release can't fog.
        float ink = mask * mix(1.0, tone(source.rgb, channel), smoothstep(0.0, INK_SETTLE, swell));
        printed[channel] = paper ? 1.0 - ink : ink;
    }

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, printed, uOrigin.w), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

TAU = math.pi * 2.0
CELL_MIN = 0.015       # cell size in wall heights, at Dotsize 0
CELL_MAX = 0.13        # cell size in wall heights, at Dotsize 1
SOFT_CELL = 0.3        # share of the cell size that a very soft hit still gets
SPLIT_SCALE = 1.4      # plate travel in cells, at full Split
ROLL_STEP = 0.645      # radians each new press turns the screens
BOLT_TILT = 0.28       # bolts rock instead of spinning, so they stay readable
BOLT_CELL = 1.7        # bolts need bigger, fewer cells than dots to read as bolts
SHAPES = {"dot": 0.0, "ring": 1.0, "bolt": 2.0}
INKS = {"light": 0.0, "paper": 1.0}

_roll = 0.0
_current = 0.0


def reset(block):
    global _roll, _current
    _roll = 0.0
    _current = 0.0


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest(node):
    _write(node, 0, 0.05, 0.0, 0.0, 1.0)
    _write(node, 1, 0.5, 0.5, 0.0, 0.0)
    _write(node, 2, 0.0, 0.0, 0.0, 0.0)


def apply(block, env):
    global _roll, _current
    switch = op("switch")
    halftone = op("halftone")
    if env.stage == "idle":
        # The roll carries over, so a hit after a rest still looks different.
        switch.par.index = 0
        _write_rest(halftone)
        return

    switch.par.index = 1
    if env.started:
        _current = _roll
        _roll = (_roll + ROLL_STEP) % TAU

    shape = block.par.Shape.eval()
    velocity = env.velocity
    size = _clamped(block.par.Dotsize) ** 1.5
    cell = (CELL_MIN + (CELL_MAX - CELL_MIN) * size) * (SOFT_CELL + (1.0 - SOFT_CELL) * velocity)
    # Squaring the level snaps the plates into register before the dots have swollen.
    split = _clamped(block.par.Split) * SPLIT_SCALE * cell * velocity * env.level ** 2
    roll = _current
    if shape == "bolt":
        cell *= BOLT_CELL
        roll = math.sin(_current * 5.0) * BOLT_TILT

    _write(halftone, 0, cell, split, roll, 1.0 - env.level)
    _write(halftone, 1, _clamped(block.par.Originx), _clamped(block.par.Originy), _clamped(block.par.Inkfloor), _clamped(block.par.Mix))
    _write(halftone, 2, SHAPES[shape], INKS[block.par.Ink.eval()], 0.0, 0.0)
'''

UNIFORMS = ["uScreen", "uOrigin", "uStyle"]


def build(library):
    block = new_block(library, "halftone_hit")
    # Release is how long the dots take to swell back into the picture.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.04, "release": 0.45, "curve": 2.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Halftone Hit")
    add_menu(page, "Shape", "Shape", ["dot", "ring", "bolt"], ["Dot", "Ring", "Bolt"], "dot")
    add_menu(page, "Ink", "Ink", ["light", "paper"], ["Light on Black", "Ink on White"], "light")
    add_float(page, "Dotsize", "Dot Size", 0.55, 0.0, 1.0, True, True)
    add_float(page, "Split", "Plate Split", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Originx", "Origin X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Originy", "Origin Y", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Inkfloor", "Ink Floor", 0.12, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "halftone_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    halftone = add_top(block, glslTOP, "halftone", -300)
    halftone.par.pixeldat = "halftone_shader"
    halftone.par.outputresolution = "useinput"
    halftone.par.format = "useinput"
    halftone.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    halftone.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(halftone.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(halftone)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(halftone)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
