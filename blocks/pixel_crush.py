# Pixel Crush: a bitcrusher for video. The wall collapses into slabs, then rebuilds in hard steps

SHADER = r'''
uniform vec4 uGrid;   // top level (block = 2^level px), split threshold, split depth, unused
uniform vec4 uFocus;  // focus x, focus y, amount, mode (0 sharp at focus, 1 crushed at focus)
uniform vec4 uLook;   // color bits per channel, gap, mix, unused

layout(location = 0) out vec4 fragColor;

const float FOCUS_RADIUS = 1.4;  // reach of the focus, in wall heights
const float GAP_SCALE = 0.18;    // grid line width in blocks, at full Gap
const float GAP_MIN_BLOCK = 4.0; // blocks smaller than this in pixels get no grid line

vec2 res;

// The mip level a block's size maps to is the average of the picture under it,
// so a slab holds steady where sampling one pixel would flicker.
vec3 averaged(vec2 center, vec2 cell)
{
    return textureLod(sTD2DInputs[0], center / res, log2(max(cell.x, cell.y))).rgb;
}

float contrast(vec2 center, vec2 cell)
{
    vec3 whole = averaged(center, cell);
    vec2 child = cell * 0.5;
    float most = 0.0;
    for (int y = 0; y < 2; y++) {
        for (int x = 0; x < 2; x++) {
            vec2 offset = (vec2(float(x), float(y)) - 0.5) * child;
            vec3 difference = abs(averaged(center + offset, child) - whole);
            most = max(most, max(difference.r, max(difference.g, difference.b)));
        }
    }
    return most;
}

// The largest level the focus allows for a block at this center.
float allowedLevel(vec2 center)
{
    float reach = clamp(length((center - uFocus.xy * res) / res.y) / FOCUS_RADIUS, 0.0, 1.0);
    float grow = reach * reach * (3.0 - 2.0 * reach);
    float pull = uFocus.w > 0.5 ? grow : 1.0 - grow;
    return uGrid.x * (1.0 - uFocus.z * pull);
}

void main()
{
    res = uTD2DInfos[0].res.zw;
    vec2 px = vUV.st * res;
    vec4 source = textureLod(sTD2DInputs[0], vUV.st, 0.0);

    // Whole blocks fill the wall exactly. Halving from here keeps every level nested.
    float level = uGrid.x;
    vec2 across = max(vec2(1.0), floor(res / pow(2.0, level) + 0.5));
    vec2 cell = res / across;
    vec2 corner = floor(px / cell) * cell;
    float stopSplitting = max(1.0, level - uGrid.z);

    while (level > 0.0) {
        vec2 center = corner + cell * 0.5;
        bool forced = level > allowedLevel(center) + 0.5;
        bool detailed = level > stopSplitting && contrast(center, cell) > uGrid.y;
        if (!forced && !detailed) {
            break;
        }
        cell *= 0.5;
        level -= 1.0;
        corner += step(corner + cell, px) * cell;
    }

    vec3 color = averaged(corner + cell * 0.5, cell);
    if (uLook.x < 7.5) {
        float steps = pow(2.0, uLook.x) - 1.0;
        color = floor(clamp(color, 0.0, 1.0) * steps + 0.5) / steps;
    }

    float smallest = min(cell.x, cell.y);
    if (smallest >= GAP_MIN_BLOCK) {
        vec2 edge = min(px - corner, corner + cell - px);
        if (min(edge.x, edge.y) < uLook.y * GAP_SCALE * smallest * 0.5) {
            color = vec3(0.0);
        }
    }

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uLook.z), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

LEVEL_MIN = 3.0      # block level at Size 0: 8 pixels
SOFT_LEVEL = 0.35    # share of the block levels that a very soft hit still gets
SPLIT_LOOSE = 0.6    # contrast a block needs to split, at Detail just above 0
SPLIT_TIGHT = 0.04   # contrast a block needs to split, at Detail 1
SPLIT_DEPTH = 4.0    # extra levels a block may split down, at Detail 1
FULL_BITS = 8.0      # color bits per channel with no crush
CRUSHED_BITS = 2.0   # color bits per channel at full Colorcrush and the top step
MODES = {"sharp": 0.0, "hole": 1.0}


def reset(block):
    return


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest(node):
    _write(node, 0, 1.0, SPLIT_LOOSE, 0.0, 0.0)
    _write(node, 1, 0.5, 0.5, 0.0, 0.0)
    _write(node, 2, FULL_BITS, 0.0, 0.0, 0.0)


def _peak_level(block, velocity):
    # The biggest block is half the wall height, whatever the input's resolution.
    ceiling = max(LEVEL_MIN, math.log2(max(2.0, float(op("in1").height))) - 1.0)
    level = LEVEL_MIN + (ceiling - LEVEL_MIN) * _clamped(block.par.Size)
    return max(1, int(round(1.0 + (level - 1.0) * (SOFT_LEVEL + (1.0 - SOFT_LEVEL) * velocity))))


def _stepped_level(peak, value, steps):
    # Resolution falls in stairs. Whole levels keep each one a hard change of grid.
    stairs = max(1, int(round(1.0 + (peak - 1) * steps)))
    stair = max(1, math.ceil(max(0.0, min(1.0, value)) * stairs - 1e-9))
    return max(1, int(round(peak * stair / float(stairs))))


def apply(block, env):
    switch = op("switch")
    crush = op("crush")
    if env.stage == "idle":
        switch.par.index = 0
        _write_rest(crush)
        return

    switch.par.index = 1
    peak = _peak_level(block, env.velocity)
    level = _stepped_level(peak, env.level, _clamped(block.par.Steps))

    detail = _clamped(block.par.Detail)
    depth = 0.0 if detail <= 0.0 else float(math.ceil(detail * SPLIT_DEPTH))
    threshold = SPLIT_LOOSE + (SPLIT_TIGHT - SPLIT_LOOSE) * detail

    bits = FULL_BITS + (CRUSHED_BITS - FULL_BITS) * _clamped(block.par.Colorcrush) * level / float(peak)

    _write(crush, 0, float(level), threshold, depth, 0.0)
    _write(crush, 1, _clamped(block.par.Focusx), _clamped(block.par.Focusy), _clamped(block.par.Focusamount), MODES[block.par.Focusmode.eval()])
    _write(crush, 2, float(round(bits)), _clamped(block.par.Gap), _clamped(block.par.Mix), 0.0)
'''

UNIFORMS = ["uGrid", "uFocus", "uLook"]


def build(library):
    block = new_block(library, "pixel_crush")
    # Curve 1 gives every stair of the rebuild the same length. Release is the whole climb.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.03, "release": 0.6, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Pixel Crush")
    add_float(page, "Size", "Size", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Steps", "Steps", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Detail", "Detail", 0.55, 0.0, 1.0, True, True)
    add_menu(page, "Focusmode", "Focus Mode", ["sharp", "hole"], ["Sharp at Focus", "Crushed at Focus"], "sharp")
    add_float(page, "Focusx", "Focus X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Focusy", "Focus Y", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Focusamount", "Focus Amount", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Colorcrush", "Color Crush", 0.8, 0.0, 1.0, True, True)
    add_float(page, "Gap", "Gap", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "crush_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    crush = add_top(block, glslTOP, "crush", -300)
    crush.par.pixeldat = "crush_shader"
    crush.par.outputresolution = "useinput"
    crush.par.format = "useinput"
    # The shader reads block averages out of the input's mip levels.
    crush.par.inputfiltertype = "mipmap"
    crush.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(crush.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(crush)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(crush)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
