# Gradient Map: the picture maps into palette-colored bands, then the bands recede through its tones or across the wall

# Measures the frame's tonal range into one pixel: red is the low end, green the high end.
# Mean and deviation rather than min and max, so a stray pixel can't make the bands jump.
# The range eases toward each new measurement, so a cut slides the bands instead of snapping them.
# Input 1 is last frame's result through the feedback loop.
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

# Blurs the brightness used for the color lookup, one direction per pass, at quarter size.
# The first pass reads the picture's luma, the second the first pass's result.
BLEED_SHADER = r'''
uniform vec4 uBlur;  // xy: step between taps in UV, z: 1 reads luma from color, 0 reads red

layout(location = 0) out vec4 fragColor;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
const int TAPS = 8;  // either side of center

void main()
{
    float sum = 0.0;
    float total = 0.0;
    for (int i = -TAPS; i <= TAPS; i++) {
        float weight = exp(-float(i * i) / 32.0);
        vec3 color = texture(sTD2DInputs[0], vUV.st + uBlur.xy * float(i)).rgb;
        sum += weight * mix(color.r, dot(color, LUMA), uBlur.z);
        total += weight;
    }
    float value = sum / total;
    fragColor = TDOutputSwizzle(vec4(value, value, value, 1.0));
}
'''

MAP_SHADER = r'''
uniform vec4 uMap;     // palette, softness, tone shift, reveal
uniform vec4 uLevels;  // auto levels, mix, bleed, reveal style

layout(location = 0) out vec4 fragColor;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
const float EDGE_MIN = 0.03;  // band edge width at Softness 0, just enough to stop stair-stepping
const float WIPE = 0.1;       // width of the wipe's soft edge
const int DITHER_CELL = 2;    // wall pixels per dissolve dot
const float BAYER[16] = float[16](0.0, 8.0, 2.0, 10.0, 12.0, 4.0, 14.0, 6.0, 3.0, 11.0, 1.0, 9.0, 15.0, 7.0, 13.0, 5.0);

@@PALETTE_GLSL@@

vec3 stopColor(int palette, int index)
{
    return paletteStop(palette, clamp(index, 0, STOP_COUNT - 1));
}

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 range = texture(sTD2DInputs[1], vec2(0.5)).rg;
    float low = range.r * uLevels.x;
    float high = mix(1.0, range.g, uLevels.x);
    // Bleed swaps in the blurred brightness, so colors run across hard edges while the image stays sharp.
    float luma = mix(dot(source.rgb, LUMA), texture(sTD2DInputs[2], vUV.st).r, uLevels.z);
    float tone = clamp((luma - low) / max(high - low, 0.001) + uMap.z, 0.0, 1.0);

    // Band centers land on whole numbers, so the fraction is the distance to the next band.
    int palette = clamp(int(uMap.x + 0.5), 0, PALETTES - 1);
    float position = tone * float(STOP_COUNT) - 0.5;
    float band = floor(position);
    float width = mix(EDGE_MIN, 1.0, uMap.y);
    float blend = smoothstep(0.5 - width * 0.5, 0.5 + width * 0.5, position - band);
    vec3 mapped = mix(stopColor(palette, int(band)), stopColor(palette, int(band) + 1), blend);

    // What gets mapped first. Tone: highlights first and let go last, so a soft press still shows on a
    // dark clip. Left, right and center: a soft wipe across the wall. Dissolve: a dot pattern fills in.
    int style = int(uLevels.w + 0.5);
    float aspect = uTD2DInfos[0].res.z / uTD2DInfos[0].res.w;
    float first = tone;
    if (style == 1) {
        first = 1.0 - vUV.s;
    } else if (style == 2) {
        first = vUV.s;
    } else if (style == 3) {
        first = 1.0 - length((vUV.st - 0.5) * vec2(aspect, 1.0)) / length(vec2(aspect, 1.0) * 0.5);
    }
    float threshold = (1.0 - uMap.w) * (1.0 + WIPE);
    float reveal = smoothstep(threshold - WIPE, threshold, first);
    if (style == 4) {
        ivec2 cell = (ivec2(gl_FragCoord.xy) / DITHER_CELL) % 4;
        reveal = step((BAYER[cell.y * 4 + cell.x] + 0.5) / 16.0, uMap.w);
    }

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, mapped, reveal * uLevels.y), source.a));
}
'''

EFFECT_SOURCE = r'''
PALETTES = @@PALETTE_INDEX@@
REVEALS = {"tone": 0.0, "left": 1.0, "right": 2.0, "center": 3.0, "dissolve": 4.0}
MAX_BLEED = 0.06   # blur reach in wall heights at full Bleed and a full-strength press
FULL_BLEED = 0.004 # below this reach the blurred brightness eases back to the sharp one
EASE = 0.12        # share of the way the tonal range moves toward each new measurement per frame


def reset(block):
    op("smooth").par.resetpulse.pulse()


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def apply(block, env):
    switch = op("switch")
    node = op("map")
    if env.stage == "idle":
        switch.par.index = 0
        _write(node, 0, 0.0, 0.0, 0.0, 0.0)
        _write(node, 1, 0.0, 0.0, 0.0, 0.0)
        _write(op("bleed_h"), 0, 0.0, 0.0, 1.0, 0.0)
        _write(op("bleed_v"), 0, 0.0, 0.0, 0.0, 0.0)
        return

    switch.par.index = 1
    palette = PALETTES.get(block.par.Palette.eval(), 0)
    reveal = max(0.0, min(1.0, env.level * env.velocity))
    # Shift is centered: tones slide through the palette in its original stop order.
    _write(node, 0, palette, _clamped(block.par.Softness), _clamped(block.par.Shift) - 0.5, reveal)
    # A fresh press measures the frame outright, so it never eases in from an old scene.
    _write(op("levels"), 0, 1.0 if env.started else EASE, 0.0, 0.0, 0.0)

    # The blur swells with the press and tightens back to the edges on release.
    spread = _clamped(block.par.Bleed) * MAX_BLEED * env.level * env.velocity
    step = spread / 8.0
    source = op("in1")
    aspect = max(0.01, float(source.width) / max(1.0, float(source.height)))
    _write(op("bleed_h"), 0, step / aspect, 0.0, 1.0, 0.0)
    _write(op("bleed_v"), 0, 0.0, step, 0.0, 0.0)
    bleed = min(1.0, spread / FULL_BLEED)
    reveal_style = REVEALS[block.par.Reveal.eval()]
    _write(node, 1, _clamped(block.par.Autolevels), _clamped(block.par.Mix), bleed, reveal_style)
'''

UNIFORMS = ["uMap", "uLevels"]


def _glsl(block, name, shader, uniforms, x, y=0):
    node = add_top(block, glslTOP, name, x)
    node.nodeY = y
    node.par.pixeldat = shader
    node.par.inputfiltertype = "linear"
    node.seq.vec.numBlocks = len(uniforms)
    for index, uniform in enumerate(uniforms):
        setattr(node.par, "vec%dname" % index, uniform)
    return node


def build(library):
    block = new_block(library, "gradient_map")
    # Release is how long the bands take to recede through the tones.
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.25, "release": 0.15, "curve": 1.0, "velocitydepth": 0.6})
    page = block.appendCustomPage("Gradient Map")
    add_palette_menu(page)
    add_float(page, "Softness", "Softness", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Bleed", "Bleed", 0.35, 0.0, 1.0, True, True)
    add_menu(page, "Reveal", "Reveal", ["tone", "left", "right", "center", "dissolve"], ["Tone", "Sweep Left", "Sweep Right", "From Center", "Dissolve"], "tone")
    add_float(page, "Shift", "Tone Shift", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Autolevels", "Auto Levels", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "levels_shader", LEVELS_SHADER, -500, -150)
    add_text(block, "bleed_shader", BLEED_SHADER, -500, -450)
    shader = palette_shader(MAP_SHADER)
    add_text(block, "map_shader", shader, -300, -150)

    source = add_top(block, inTOP, "in1", -700)
    # Last frame's range comes back through this loop. It is 1x1, like the levels pass.
    smooth = add_top(block, feedbackTOP, "smooth", -700)
    smooth.nodeY = 150
    smooth.par.top = "levels"
    smooth.par.outputresolution = "custom"
    smooth.par.resolutionw = 1
    smooth.par.resolutionh = 1
    smooth.par.format = "rgba32float"
    levels = _glsl(block, "levels", "levels_shader", ["uLevels"], -500, 150)
    levels.par.outputresolution = "custom"
    levels.par.resolutionw = 1
    levels.par.resolutionh = 1
    # Float, so small easing steps aren't lost to 8-bit rounding.
    levels.par.format = "rgba32float"
    bleed_h = _glsl(block, "bleed_h", "bleed_shader", ["uBlur"], -500, -150)
    bleed_h.par.outputresolution = "quarter"
    bleed_h.par.format = "rgba16float"
    bleed_v = _glsl(block, "bleed_v", "bleed_shader", ["uBlur"], -400, -150)
    bleed_v.par.outputresolution = "useinput"
    bleed_v.par.format = "rgba16float"
    node = add_top(block, glslTOP, "map", -300)
    node.par.pixeldat = "map_shader"
    node.par.outputresolution = "useinput"
    node.par.format = "useinput"
    node.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    node.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(node.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(smooth)
    levels.inputConnectors[0].connect(source)
    levels.inputConnectors[1].connect(smooth)
    source.outputConnectors[0].connect(bleed_h)
    bleed_h.outputConnectors[0].connect(bleed_v)
    node.inputConnectors[0].connect(source)
    node.inputConnectors[1].connect(levels)
    node.inputConnectors[2].connect(bleed_v)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(node)
    switch.outputConnectors[0].connect(output)

    effect = palette_effect(EFFECT_SOURCE, PALETTE_INDEX=PALETTE_INDEX)
    add_text(block, "effect", effect, -500, -300)
    add_callbacks(block)
    return block
