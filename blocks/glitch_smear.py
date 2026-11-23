# Glitch Smear: datamosh hold. The hit freezes a frame, then the live video's motion drags its colors
#
# This follows a P-frame decoder that has lost its I-frame. The frozen frame is the reference picture.
# Every frame, block matching finds where each macroblock of the live video came from, the way an
# encoder would. Those vectors move the reference instead of the real picture, so the live footage
# itself is never shown, only its motion. Release heals the wall back to live a block at a time.

# Input 0 is the live frame, input 1 the previous live frame, input 2 last frame's motion field.
# One output texel per macroblock: rg is where the block came from in the previous frame, in pixels,
# and b is how badly the best match still differs.
MOTION_SHADER = r'''
uniform vec4 uBlock;  // block size in pixels, search on (0 on the frame a hit freezes), unused, unused

layout(location = 0) out vec4 fragColor;

const int GRID = 4;            // samples across a block, each the average of its patch
const int ROUNDS = 5;          // step search from 16 pixels down to 1
const float FIRST_STEP = 16.0;
const float PENALTY = 0.0008;  // cost per pixel of motion, so flat or noisy blocks stay put

vec2 res;
vec2 corner;
float spacing;
float lod;

float difference(vec2 offset)
{
    float total = 0.0;
    for (int y = 0; y < GRID; y++) {
        for (int x = 0; x < GRID; x++) {
            vec2 p = corner + (vec2(float(x), float(y)) + 0.5) * spacing;
            vec3 now = textureLod(sTD2DInputs[0], clamp(p / res, 0.0, 1.0), lod).rgb;
            vec3 before = textureLod(sTD2DInputs[1], clamp((p + offset) / res, 0.0, 1.0), lod).rgb;
            vec3 d = abs(now - before);
            total += d.r + d.g + d.b;
        }
    }
    return total / float(GRID * GRID * 3);
}

void main()
{
    res = uTD2DInfos[0].res.zw;
    float blockPx = max(1.0, uBlock.x);
    spacing = blockPx / float(GRID);
    // The mip level a sample reads is the average of its patch of the block.
    lod = max(0.0, log2(spacing));
    corner = floor(gl_FragCoord.xy) * blockPx;

    if (uBlock.y < 0.5) {
        fragColor = TDOutputSwizzle(vec4(0.0, 0.0, 0.0, 1.0));
        return;
    }

    vec2 best = vec2(0.0);
    float bestRaw = difference(best);
    float bestScore = bestRaw;

    // Last frame's vector for this block is the encoder's predictor. It catches motion past the search.
    vec2 guess = floor(texelFetch(sTD2DInputs[2], ivec2(gl_FragCoord.xy), 0).rg + 0.5);
    float raw = difference(guess);
    float score = raw + PENALTY * length(guess);
    if (score < bestScore) {
        best = guess;
        bestRaw = raw;
        bestScore = score;
    }

    float stride = FIRST_STEP;
    for (int round_ = 0; round_ < ROUNDS; round_++) {
        vec2 center = best;
        for (int y = -1; y <= 1; y++) {
            for (int x = -1; x <= 1; x++) {
                if (x == 0 && y == 0) {
                    continue;
                }
                vec2 candidate = center + vec2(float(x), float(y)) * stride;
                raw = difference(candidate);
                score = raw + PENALTY * length(candidate);
                if (score < bestScore) {
                    best = candidate;
                    bestRaw = raw;
                    bestScore = score;
                }
            }
        }
        stride *= 0.5;
    }

    fragColor = TDOutputSwizzle(vec4(best, bestRaw, 1.0));
}
'''

# Input 0 is this frame's motion, input 1 last frame's field. Bloom keeps the strongest recent vector
# alive and decays it, the way a repeated P-frame keeps pushing pixels after the motion has stopped.
FIELD_SHADER = r'''
uniform vec4 uField;  // decay per frame, clear (1 on the frame a hit freezes), unused, unused

layout(location = 0) out vec4 fragColor;

void main()
{
    ivec2 here = ivec2(gl_FragCoord.xy);
    vec4 found = texelFetch(sTD2DInputs[0], here, 0);
    vec2 held = uField.y > 0.5 ? vec2(0.0) : texelFetch(sTD2DInputs[1], here, 0).rg * uField.x;
    vec2 vector = dot(found.rg, found.rg) >= dot(held, held) ? found.rg : held;
    fragColor = TDOutputSwizzle(vec4(vector, found.b, 1.0));
}
'''

# Input 0 is the live frame, 1 the previous live frame, 2 the motion field, 3 last frame's smear.
MOSH_SHADER = r'''
uniform vec4 uMosh;  // vector gain, ghost, skip threshold, intra threshold
uniform vec4 uGrid;  // block size in pixels, liquid, freeze (1 on the frame a hit lands), unused

layout(location = 0) out vec4 fragColor;

const float STEPS = 16.0;  // residual quantizer: changes smaller than one step are dropped

vec2 res;

vec3 fetch(int index, vec2 px)
{
    vec2 half_ = 0.5 / res;
    return texture(sTD2DInputs[index], clamp(px / res, half_, 1.0 - half_)).rgb;
}

void main()
{
    res = uTD2DInfos[0].res.zw;
    vec2 px = vUV.st * res;
    vec4 live = texture(sTD2DInputs[0], vUV.st);

    // The hit freezes this frame as the reference picture.
    if (uGrid.z > 0.5) {
        fragColor = TDOutputSwizzle(live);
        return;
    }

    float blockPx = max(1.0, uGrid.x);
    ivec2 size = textureSize(sTD2DInputs[2], 0);
    ivec2 cell = clamp(ivec2(px / blockPx), ivec2(0), size - 1);
    vec4 block = texelFetch(sTD2DInputs[2], cell, 0);

    // A block nothing in the last frame resembles is coded fresh, so the real picture pops through.
    if (block.b > uMosh.w) {
        fragColor = TDOutputSwizzle(live);
        return;
    }

    // Hard macroblocks move by whole pixels. Liquid blends toward a smooth field between block centers.
    vec2 smooth_ = texture(sTD2DInputs[2], px / (vec2(size) * blockPx)).rg;
    vec2 vector = mix(block.rg, smooth_, uGrid.y);
    vec2 pushed = vector * uMosh.x;
    pushed = mix(floor(pushed + 0.5), pushed, uGrid.y);
    vec3 color = fetch(3, px + pushed);

    // Well-matched blocks are skipped, so they carry no correction and keep the frozen color.
    if (block.b > uMosh.z) {
        vec3 residual = live.rgb - fetch(1, px + vector);
        residual = sign(residual) * floor(abs(residual) * STEPS) / STEPS;
        color += residual * uMosh.y;
    }

    fragColor = TDOutputSwizzle(vec4(clamp(color, 0.0, 1.0), live.a));
}
'''

# Input 0 is the live frame, input 1 the smear. Healed blocks show live video in a random order per hit.
COMPOSITE_SHADER = r'''
uniform vec4 uHeal;  // share of blocks healed, seed, block size in pixels, mix

layout(location = 0) out vec4 fragColor;

float hash(vec2 p)
{
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main()
{
    vec4 live = texture(sTD2DInputs[0], vUV.st);
    vec4 smear = texture(sTD2DInputs[1], vUV.st);
    vec2 cell = floor(vUV.st * uTD2DInfos[0].res.zw / max(1.0, uHeal.z));
    bool healed = hash(cell + uHeal.y) < uHeal.x;
    vec3 color = healed ? live.rgb : smear.rgb;
    fragColor = TDOutputSwizzle(vec4(mix(live.rgb, color, uHeal.w), live.a));
}
'''

EFFECT_SOURCE = r'''
import random

GAIN_RANGE = 3.0        # extra vector gain at full Smear and full velocity, on top of true speed
BLOOM_HALF_LIFE = 2.0   # seconds a stopped motion keeps half its push, at full Bloom
SKIP_MAX = 0.12         # block difference below which the residual is skipped, at full Freeze
INTRA_LOOSE = 0.5       # block difference that refreshes from live, at Intra just above 0
INTRA_TIGHT = 0.06      # block difference that refreshes from live, at full Intra
NEVER = 1000.0          # a threshold no block reaches
SEED_RANGE = 1000.0

_rng = random.Random()
_seed = 0.0
_peak = 0.0


def reset(block):
    global _seed, _peak
    _seed = 0.0
    _peak = 0.0
    for name in ("previous", "field_last", "mosh_last"):
        op(name).par.resetpulse.pulse()


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y=0.0, z=0.0, w=0.0):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest():
    _write(op("motion"), 0, 0.0)
    _write(op("field"), 0, 0.0)
    _write(op("mosh"), 0, 0.0)
    _write(op("mosh"), 1, 0.0)
    _write(op("composite"), 0, 0.0)


def _decay(block):
    bloom = _clamped(block.par.Bloom)
    if bloom <= 0.0:
        return 0.0
    half_life = BLOOM_HALF_LIFE * bloom * bloom
    return 0.5 ** (1.0 / (max(1.0, float(project.cookRate)) * half_life))


def _intra(block):
    intra = _clamped(block.par.Intra)
    if intra <= 0.0:
        return NEVER
    return INTRA_LOOSE + (INTRA_TIGHT - INTRA_LOOSE) * intra


def apply(block, env):
    global _seed, _peak
    switch = op("switch")
    if env.stage == "idle":
        switch.par.index = 0
        _peak = 0.0
        _write_rest()
        return

    switch.par.index = 1
    fresh = env.started
    if fresh:
        # Each hit heals in its own block order.
        _seed = _rng.random() * SEED_RANGE

    heal = 0.0
    if env.stage == "release":
        heal = 1.0 - env.level / _peak if _peak > 0.0 else 1.0
    else:
        _peak = env.level

    size = float(block.par.Blocksize.eval())
    # The push holds at its peak through release, while the healing takes the wall back.
    gain = _peak * (1.0 + GAIN_RANGE * _clamped(block.par.Smear) * env.velocity)
    flag = 1.0 if fresh else 0.0

    # The previous frame is stale on the first frame of a hit, so the search waits a frame.
    _write(op("motion"), 0, size, 1.0 - flag)
    _write(op("field"), 0, _decay(block), flag)
    _write(op("mosh"), 0, gain, _clamped(block.par.Ghost), _clamped(block.par.Freeze) * SKIP_MAX, _intra(block))
    _write(op("mosh"), 1, size, _clamped(block.par.Liquid), flag)
    _write(op("composite"), 0, max(0.0, min(1.0, heal)), _seed, size, _clamped(block.par.Mix))
'''

BLOCK_SIZES = ["8", "16", "32", "64"]


def _glsl(block, op_type, name, shader, uniforms, x, y=0):
    node = add_top(block, op_type, name, x)
    node.nodeY = y
    node.par.pixeldat = shader
    node.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    node.seq.vec.numBlocks = len(uniforms)
    for index, uniform in enumerate(uniforms):
        setattr(node.par, "vec%dname" % index, uniform)
    return node


def _feedback(block, name, target, fmt, x, y):
    node = add_top(block, feedbackTOP, name, x)
    node.nodeY = y
    node.par.top = target
    node.par.format = fmt
    return node


def build(library):
    block = new_block(library, "glitch_smear")
    # Hold the pad to keep moshing. Release is how long the wall takes to heal back to live.
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 1.5, "release": 0.8, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Glitch Smear")
    add_menu(page, "Blocksize", "Block Size", BLOCK_SIZES, [s + " px" for s in BLOCK_SIZES], "16")
    add_float(page, "Smear", "Smear", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Bloom", "Bloom", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Liquid", "Liquid", 0.0, 0.0, 1.0, True, True)
    add_float(page, "Ghost", "Ghost", 0.25, 0.0, 1.0, True, True)
    add_float(page, "Freeze", "Freeze", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Intra", "Intra Refresh", 0.1, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "motion_shader", MOTION_SHADER, -900, -150)
    add_text(block, "field_shader", FIELD_SHADER, -700, -150)
    add_text(block, "mosh_shader", MOSH_SHADER, -500, -150)
    add_text(block, "composite_shader", COMPOSITE_SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -1300)

    # The live frame from one cook ago.
    previous = add_top(block, cacheTOP, "previous", -1100)
    previous.nodeY = 150
    previous.par.active = True
    previous.par.interp = False
    previous.par.cachesize = 2
    previous.par.outputindex = -1

    # One texel per macroblock. Each sample reads a mip level, so the search costs the same at any size.
    motion = _glsl(block, glslTOP, "motion", "motion_shader", ["uBlock"], -900)
    motion.par.inputfiltertype = "mipmap"
    motion.par.outputresolution = "custom"
    motion.par.resolutionw.expr = "-(-op('in1').width // int(parent().par.Blocksize.eval()))"
    motion.par.resolutionh.expr = "-(-op('in1').height // int(parent().par.Blocksize.eval()))"
    motion.par.format = "rgba32float"
    field_last = _feedback(block, "field_last", "field", "rgba32float", -900, 300)
    field = _glsl(block, glslTOP, "field", "field_shader", ["uField"], -700)
    field.par.outputresolution = "useinput"
    field.par.format = "rgba32float"

    # The smear accumulates in float, so small residuals and repeated moves don't band.
    mosh_last = _feedback(block, "mosh_last", "mosh", "rgba16float", -500, 300)
    mosh = _glsl(block, glslmultiTOP, "mosh", "mosh_shader", ["uMosh", "uGrid"], -500)
    mosh.par.outputresolution = "useinput"
    mosh.par.format = "rgba16float"

    composite = _glsl(block, glslTOP, "composite", "composite_shader", ["uHeal"], -300)
    composite.par.outputresolution = "useinput"
    composite.par.format = "useinput"
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(previous)
    motion.inputConnectors[0].connect(source)
    motion.inputConnectors[1].connect(previous)
    motion.inputConnectors[2].connect(field_last)
    motion.outputConnectors[0].connect(field_last)
    field.inputConnectors[0].connect(motion)
    field.inputConnectors[1].connect(field_last)
    source.outputConnectors[0].connect(mosh_last)
    mosh.inputConnectors[0].connect(source)
    mosh.inputConnectors[1].connect(previous)
    mosh.inputConnectors[2].connect(field)
    mosh.inputConnectors[3].connect(mosh_last)
    composite.inputConnectors[0].connect(source)
    composite.inputConnectors[1].connect(mosh)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(composite)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
