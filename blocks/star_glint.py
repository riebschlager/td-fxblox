# Star Glint: disco star filter, the picture's highlights throw sparkling stars and glow

EXTRACT_SHADER = r'''
// Keeps only the bright parts of the picture, for the glow.
uniform vec2 uExtract;  // cutoff, softness

layout(location = 0) out vec4 fragColor;

void main()
{
    vec4 color = texture(sTD2DInputs[0], vUV.st);
    float luma = dot(color.rgb, vec3(0.2126, 0.7152, 0.0722));
    float keep = smoothstep(uExtract.x, uExtract.x + uExtract.y, luma);
    fragColor = TDOutputSwizzle(vec4(color.rgb * keep, 1.0));
}
'''

BLUR_SHADER = r'''
// One axis of a Gaussian. Samples sit two texels apart and lean on linear filtering.
uniform vec2 uBlur;  // direction in texels

layout(location = 0) out vec4 fragColor;

void main()
{
    vec2 texel = uTD2DInfos[0].res.xy;
    vec3 sum = vec3(0.0);
    float total = 0.0;
    for (int i = -6; i <= 6; i++) {
        float t = float(i) / 3.0;
        float weight = exp(-0.5 * t * t);
        sum += texture(sTD2DInputs[0], vUV.st + uBlur * texel * float(i) * 2.0).rgb * weight;
        total += weight;
    }
    fragColor = TDOutputSwizzle(vec4(sum / total, 1.0));
}
'''

STARS_SHADER = r'''
// Input 0 is the sharp picture, input 1 is the blurred highlights.
uniform vec4 uClock;  // twinkle phase, pop, seed, level
uniform vec4 uStars;  // share of cells lit, radius in cells, spike lines, angle
uniform vec4 uLight;  // highlight cutoff, floor, rainbow, glow
uniform vec4 uHero;   // count, radius in wall heights, mix, palette (0 is spectrum)

layout(location = 0) out vec4 fragColor;

const float PI = 3.14159265;
const float ROWS = 14.0;         // grid cells per wall height
const float JITTER = 0.7;        // stars stay this share of a cell around its center
const float DRIFT = 0.1;         // cells a star rises over its life
// JITTER and DRIFT leave a star at least 0.05 cell inside its own cell, so the
// 3x3 search below can't clip a star whose radius is up to 1.05 cells.
const float SPIKE_WIDTH = 0.014; // share of the radius
const float HERO_RATE = 0.6;     // heroes twinkle slower than the small stars
const float HERO_FLOOR = 0.45;   // heroes still show this strongly on dark areas
const float CORE = 0.05;         // share of the radius
const float HALO = 0.3;          // share of the radius
const float HALO_GAIN = 0.4;
const vec3 SILVER = @@SILVER@@;
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

float hash(vec2 place, float salt)
{
    vec3 q = fract(vec3(place.xyx) * 0.1031 + salt);
    q += dot(q, q.yzx + 33.33);
    return fract((q.x + q.y) * q.z);
}

@@PALETTE_GLSL@@
@@PALETTE_CYCLE@@

vec3 spectrum(float t)
{
    int palette = int(uHero.w) - 1;
    if (palette >= 0) return paletteCycle(palette, t);
    return 0.5 + 0.5 * cos(2.0 * PI * (t + vec3(0.0, 0.33, 0.67)));
}

// Silver at the heart, breaking into color toward the spike tips.
void addStar(inout vec3 light, vec2 d, float radius, float brightness, float hue, float pixel)
{
    float dist = length(d);
    if (dist >= radius) {
        return;
    }
    float along = dist / radius;
    float width = max(pixel, radius * SPIKE_WIDTH);
    float spikes = 0.0;
    float lines = uStars.z;
    for (float k = 0.0; k < lines; k += 1.0) {
        float angle = uStars.w + PI * k / lines;
        vec2 axis = vec2(cos(angle), sin(angle));
        float taper = max(0.0, 1.0 - abs(dot(d, axis)) / radius);
        float across = dot(d, vec2(-axis.y, axis.x)) / (width * (0.25 + taper));
        spikes = max(spikes, taper * taper * exp(-across * across));
    }
    float core = dist / (radius * CORE);
    float halo = dist / (radius * HALO);
    float shape = spikes + exp(-core * core) + uLight.w * HALO_GAIN * exp(-halo * halo);
    vec3 tint = mix(SILVER, spectrum(along * 1.4 + hue), uLight.z * smoothstep(0.05, 0.4, along));
    light += tint * shape * brightness;
}

float highlight(vec2 uv)
{
    float luma = dot(texture(sTD2DInputs[0], uv).rgb, LUMA);
    return smoothstep(uLight.x, uLight.x + 0.2, luma);
}

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    float pixel = uTD2DInfos[0].res.y;
    vec2 point = vUV.st * aspect;
    float seed = uClock.z;
    float pop = uClock.y;
    float level = uClock.w;
    vec3 light = vec3(0.0);

    // Small stars: one possible star per cell, each on its own life cycle. A
    // finished star comes back somewhere else in its cell.
    vec2 home = floor(point * ROWS);
    for (int y = -1; y <= 1; y++) {
        for (int x = -1; x <= 1; x++) {
            vec2 cell = home + vec2(float(x), float(y));
            if (hash(cell, seed) >= uStars.x) {
                continue;
            }
            float life = uClock.x * (0.7 + 0.6 * hash(cell, seed + 0.13)) + hash(cell, seed + 0.29);
            float epoch = floor(life);
            float age = life - epoch;
            float salt = seed + epoch * 0.37;

            // Every star is lit by the opening pop, then falls into its twinkle.
            float flash = epoch < 0.5 ? pop : 0.0;
            float twinkle = pow(sin(PI * age), 3.0);
            float shine = max(twinkle, flash) * level;
            if (shine <= 0.003) {
                continue;
            }

            vec2 jitter = vec2(hash(cell, salt + 0.41), hash(cell, salt + 0.53)) - 0.5;
            vec2 center = (cell + 0.5 + jitter * JITTER + vec2(0.0, DRIFT * age)) / ROWS;
            float radius = uStars.y / ROWS * (0.55 + 0.45 * shine) * (1.0 + 0.3 * flash);
            if (distance(point, center) >= radius) {
                continue;
            }

            float allow = hash(cell, salt + 0.67) < uLight.y ? 1.0 : highlight(center / aspect);
            if (allow > 0.0) {
                addStar(light, point - center, radius, shine * allow, hash(cell, salt + 0.79), pixel);
            }
        }
    }

    // Hero stars: few and huge. They can't live in the grid, their spikes are too long.
    int heroes = int(uHero.x);
    for (int i = 0; i < heroes; i++) {
        vec2 id = vec2(float(i), 97.0);
        float life = uClock.x * HERO_RATE * (0.7 + 0.6 * hash(id, seed + 0.13)) + hash(id, seed + 0.29);
        float epoch = floor(life);
        float age = life - epoch;
        float salt = seed + epoch * 0.37;
        float flash = epoch < 0.5 ? pop : 0.0;
        float shine = max(pow(sin(PI * age), 3.0), flash) * level;
        if (shine <= 0.003) {
            continue;
        }
        vec2 center = vec2(hash(id, salt + 0.41), hash(id, salt + 0.53)) * aspect;
        float radius = uHero.y * (0.55 + 0.45 * shine) * (1.0 + 0.3 * flash);
        if (distance(point, center) >= radius) {
            continue;
        }
        float allow = max(HERO_FLOOR, highlight(center / aspect));
        addStar(light, point - center, radius, shine * allow, hash(id, salt + 0.79), pixel);
    }

    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec3 glow = texture(sTD2DInputs[1], vUV.st).rgb * uLight.w * (0.7 + 1.5 * pop) * level;
    vec3 color = source.rgb + min(light, vec3(2.0)) + glow;
    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uHero.z), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

PALETTE_INDEX = @@PALETTE_INDEX@@

GOLDEN = 0.618034        # steps the seed so each press gets a new pattern
MAX_DT = 0.1
POP_SECONDS = 0.18
MIN_RATE = 0.4           # twinkle cycles per second
RATE_RANGE = 2.2
MAX_HEROES = 12
ANGLE = math.pi / 4.0
ROTATE = 0.12            # radians per second, a slowly turned filter
LINES = {"four": 2.0, "six": 3.0, "eight": 4.0}
EXTRACT_SOFTNESS = 0.25

_phase = 0.0
_seed = 0.0
_fired = None
_last_seconds = None


def reset(block):
    global _phase, _seed, _fired, _last_seconds
    _phase = 0.0
    _seed = 0.0
    _fired = None
    _last_seconds = None


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y=0.0, z=0.0, w=0.0):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest(stars):
    for index in range(4):
        _write(stars, index, 0.0)
    _write(op("extract"), 0, 1.0, EXTRACT_SOFTNESS)


def apply(block, env):
    global _phase, _seed, _fired, _last_seconds
    switch = op("switch")
    stars = op("stars")
    if env.stage == "idle":
        switch.par.index = 0
        _phase = 0.0
        _fired = None
        _last_seconds = None
        _write_rest(stars)
        return

    switch.par.index = 1
    now = absTime.seconds
    if env.started or _fired is None:
        _fired = now
        _phase = 0.0
        _seed = (_seed + GOLDEN) % 1.0
        _last_seconds = now
    dt = 0.0 if _last_seconds is None else max(0.0, min(MAX_DT, now - _last_seconds))
    _last_seconds = now

    # Integrated, so moving the Twinkle knob changes the pace without a jump.
    _phase += dt * (MIN_RATE + RATE_RANGE * _clamped(block.par.Twinkle))
    age = now - _fired
    velocity = env.velocity
    size = _clamped(block.par.Starsize)
    scale = 0.6 + 0.4 * velocity

    cutoff = _clamped(block.par.Highlight)
    glow = _clamped(block.par.Glow) * (0.4 + 0.6 * velocity)
    heroes = round(_clamped(block.par.Heroes) * MAX_HEROES * (0.4 + 0.6 * velocity))

    _write(stars, 0, _phase, math.exp(-age / POP_SECONDS), _seed, env.level)
    _write(
        stars,
        1,
        _clamped(block.par.Density) * (0.3 + 0.7 * velocity),
        (0.25 + 0.55 * size) * scale,
        LINES[block.par.Points.eval()],
        ANGLE + ROTATE * age,
    )
    _write(stars, 2, cutoff, _clamped(block.par.Floor), _clamped(block.par.Rainbow), glow)
    palette = PALETTE_INDEX.get(block.par.Palette.eval(), -1) + 1
    _write(stars, 3, float(heroes), (0.25 + 0.55 * size) * scale, _clamped(block.par.Mix), palette)

    _write(op("extract"), 0, cutoff, EXTRACT_SOFTNESS)
'''

STARS_UNIFORMS = ["uClock", "uStars", "uLight", "uHero"]


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
    block = new_block(library, "star_glint")
    # Gate keeps it sparkling while held. Hit plays once, with Release as the tail.
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.1, "release": 1.2, "curve": 1.6, "velocitydepth": 1.0})
    page = block.appendCustomPage("Star Glint")
    add_float(page, "Density", "Density", 0.45, 0.0, 1.0, True, True)
    add_float(page, "Starsize", "Star Size", 0.6, 0.0, 1.0, True, True)
    add_menu(page, "Points", "Points", ["four", "six", "eight"], ["4", "6", "8"], "four")
    add_float(page, "Twinkle", "Twinkle", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Heroes", "Hero Stars", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Highlight", "Highlight Cutoff", 0.55, 0.0, 1.0, True, True)
    add_float(page, "Floor", "Dark Clip Floor", 0.25, 0.0, 1.0, True, True)
    add_float(page, "Rainbow", "Rainbow Edge", 0.25, 0.0, 1.0, True, True)
    add_float(page, "Glow", "Glow", 0.5, 0.0, 1.0, True, True)
    add_palette_menu(page, "Palette", "Rainbow Palette", presets={"spectrum": "Spectrum"}, default="spectrum")
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "extract_shader", EXTRACT_SHADER, -900, -150)
    add_text(block, "blur_shader", BLUR_SHADER, -700, -150)
    shader = palette_shader(STARS_SHADER, SILVER=f"vec3{PRISM_SILVER}")
    add_text(block, "stars_shader", shader, -300, -150)

    source = add_top(block, inTOP, "in1", -1100)
    # Each halving makes the glow cheaper and wider. Float keeps it from banding.
    extract = _add_glsl(block, "extract", -900, "extract_shader", ["uExtract"], "half", "rgba16float")
    blur_x = _add_glsl(block, "blur_x", -700, "blur_shader", ["uBlur"], "half", "rgba16float")
    blur_y = _add_glsl(block, "blur_y", -500, "blur_shader", ["uBlur"], "useinput", "rgba16float")
    blur_x.par.vec0valuex = 1.0
    blur_x.par.vec0valuey = 0.0
    blur_y.par.vec0valuex = 0.0
    blur_y.par.vec0valuey = 1.0
    stars = _add_glsl(block, "stars", -300, "stars_shader", STARS_UNIFORMS, "useinput", "useinput")
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(extract)
    extract.outputConnectors[0].connect(blur_x)
    blur_x.outputConnectors[0].connect(blur_y)
    stars.inputConnectors[0].connect(source)
    stars.inputConnectors[1].connect(blur_y)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(stars)
    switch.outputConnectors[0].connect(output)

    effect = palette_effect(EFFECT_SOURCE, PALETTE_INDEX=PALETTE_INDEX)
    add_text(block, "effect", effect, -500, -300)
    add_callbacks(block)
    return block
