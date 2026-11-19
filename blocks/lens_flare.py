# Lens Flare: tail hit, a light source sweeps across the wall through warm, dirty 70s glass
#
# Four passes. "extract" keeps the picture's highlights at an eighth of the size, and "blur_x" and
# "blur_y" soften them. "flare" draws all the flare light on black at reduced resolution (the
# Detail menu): the hot core, the anamorphic streak, the starburst, the rainbow halo, hexagonal
# ghosts thrown back through the wall's center, ghosts of the picture's own highlights, and lens
# dirt lit by the source. "composite" veils the picture with glare and adds the light so it burns
# smoothly to white instead of clipping flat.

EXTRACT_SHADER = r'''
// Keeps only the bright parts of the picture, shrunk to this pass's size.
uniform vec2 uExtract;  // cutoff, softness

layout(location = 0) out vec4 fragColor;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

void main()
{
    // 4x4 linear taps two texels apart cover the 8x8 source texels behind each output texel,
    // so small highlights don't flicker in and out as they move.
    vec2 texel = uTD2DInfos[0].res.xy;
    vec3 sum = vec3(0.0);
    for (int y = 0; y < 4; y++) {
        for (int x = 0; x < 4; x++) {
            vec2 offset = (vec2(float(x), float(y)) - 1.5) * 2.0 * texel;
            vec3 color = texture(sTD2DInputs[0], vUV.st + offset).rgb;
            float luma = dot(color, LUMA);
            sum += color * smoothstep(uExtract.x, uExtract.x + uExtract.y, luma);
        }
    }
    fragColor = TDOutputSwizzle(vec4(sum / 16.0, 1.0));
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

FLARE_SHADER = r'''
// All the flare light on black. Input 0 is the picture, used only for the wall's shape. Input 1
// is its blurred highlights, for the image ghosts. Distances are in wall heights.
uniform vec4 uSource;  // source position (UV, may sit off the wall), brightness, size
uniform vec4 uLook;    // streak length, ghosts, image ghosts, dirt
uniform vec4 uLens;    // warmth, starburst turn (radians), starburst, seed

layout(location = 0) out vec4 fragColor;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
const vec3 NEUTRAL = vec3(0.88, 0.94, 1.0);  // Warmth 0: clean modern coating

// Ghosts sit on the line from the source through the wall's center: 1 is the source, 0 the
// center, and negative values land on the far side. As the source sweeps one way they slide the
// other way at different speeds, which is what sells it as glass.
const float GHOST_AT[7] = float[7](0.55, 0.22, -0.3, -0.55, -0.85, -1.2, -1.6);
const float GHOST_SIZE[7] = float[7](0.03, 0.065, 0.045, 0.15, 0.085, 0.23, 0.11);
const float GHOST_RING[7] = float[7](0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0);  // 1 is a hollow ring
const float GHOST_GAIN[7] = float[7](0.8, 0.7, 0.9, 0.38, 0.7, 0.24, 0.5);
const vec3 GHOST_COLOR[7] = vec3[7](
    vec3(1.0, 0.62, 0.25),   // amber
    vec3(0.55, 0.85, 0.42),  // the green of an old coating
    vec3(1.0, 0.45, 0.18),   // orange
    vec3(0.95, 0.4, 0.58),   // dusty magenta
    vec3(1.0, 0.76, 0.34),   // gold
    vec3(0.42, 0.74, 0.62),  // teal
    vec3(1.0, 0.5, 0.3));    // salmon
const float APERTURE_TURN = 0.26;  // radians: the iris's fixed angle, shared by every ghost
const float ROUNDNESS = 0.3;       // ghosts sit between a hexagon (0) and a circle (1)
const float GHOST_SOFT = 0.1;      // of a ghost's size: old glass is never crisp
const float GHOST_CHROMA = 0.045;  // of a ghost's size: its edges split by color

const vec3 CORE_COLOR = vec3(1.0, 0.86, 0.66);
const vec3 STREAK_COLOR = vec3(1.0, 0.48, 0.16);
const vec3 GLOW_COLOR = vec3(1.0, 0.58, 0.28);
const float CORE_RADIUS = 0.02;
const float CORE_GAIN = 6.0;
const float GLOW_RADIUS = 0.09;    // the long 1/r^2 tail around the core
const float GLOW_GAIN = 1.3;
const float WASH_REACH = 0.4;      // the broad warm haze around the source
const float WASH_GAIN = 0.25;
const float STREAK_THIN = 0.005;
const float STREAK_WIDE = 0.022;
const float STREAK_GAIN = 1.8;
const float STREAK_WIDE_GAIN = 0.45;
const float STAR_REACH = 0.45;
const float STAR_GAIN = 1.5;
const float HALO_RADIUS = 0.3;
const float HALO_WIDTH = 0.014;
const float HALO_SPLIT = 0.06;     // of the halo's radius: red rides outside, blue inside
const float HALO_GAIN = 0.16;

const int IMAGE_GHOSTS = 5;
const float IMAGE_SPACING = 0.36;  // of the way to the center, between image ghosts
const float IMAGE_CHROMA = 0.025;
const float IMAGE_HALO = 0.42;
const float IMAGE_GAIN = 3.5;

const float DIRT_REACH = 0.8;      // how far the source's light reaches into the dirt
const float DIRT_FLOOR = 0.2;      // the whole lens catches a little light while the source is up
const float DIRT_GAIN = 0.9;
const float DIRT_SEED = 37.0;      // the dirt is on the lens, so it stays put from hit to hit

float hash12(vec2 p)
{
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float noise(vec2 p)
{
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash12(i), hash12(i + vec2(1.0, 0.0)), u.x),
               mix(hash12(i + vec2(0.0, 1.0)), hash12(i + vec2(1.0, 1.0)), u.x), u.y);
}

vec3 tint(vec3 warm, float warmth)
{
    return mix(vec3(dot(warm, LUMA)) * NEUTRAL * 1.3, warm, warmth);
}

// Signed distance to a hexagon of inradius r.
float hexagon(vec2 p, float r)
{
    const vec3 k = vec3(-0.866025404, 0.5, 0.577350269);
    p = abs(p);
    p -= 2.0 * min(dot(k.xy, p), 0.0) * k.xy;
    p -= vec2(clamp(p.x, -k.z * r, k.z * r), r);
    return length(p) * sign(p.y);
}

float ghostShape(vec2 q, float r, float ring)
{
    float d = mix(hexagon(q, r), length(q) - r, ROUNDNESS);
    float soft = GHOST_SOFT * r;
    // Filled ghosts brighten toward their rim, like light pooling at the edge of coated glass.
    float filled = (1.0 - smoothstep(-soft, soft, d)) * (0.55 + 0.45 * smoothstep(-r, 0.0, d));
    float hollow = exp(-abs(d) / (0.07 * r + 1e-4));
    return mix(filled, hollow, ring);
}

vec3 ghosts(vec2 p, vec2 source, vec2 center, float size, float warmth)
{
    float co = cos(APERTURE_TURN);
    float si = sin(APERTURE_TURN);
    mat2 iris = mat2(co, -si, si, co);
    vec2 axis = source - center;
    vec3 sum = vec3(0.0);
    for (int i = 0; i < 7; i++) {
        float r = GHOST_SIZE[i] * size;
        vec2 q = iris * (p - (center + axis * GHOST_AT[i]));
        if (dot(q, q) > 4.0 * r * r) {
            continue;
        }
        vec3 shape = vec3(
            ghostShape(q, r * (1.0 + GHOST_CHROMA), GHOST_RING[i]),
            ghostShape(q, r, GHOST_RING[i]),
            ghostShape(q, r * (1.0 - GHOST_CHROMA), GHOST_RING[i]));
        sum += shape * tint(GHOST_COLOR[i], warmth) * GHOST_GAIN[i];
    }
    return sum;
}

float onWall(vec2 uv)
{
    vec2 edge = smoothstep(vec2(0.0), vec2(0.02), uv) * smoothstep(vec2(1.0), vec2(0.98), uv);
    return edge.x * edge.y;
}

// The lens's color shifts from amber at its center to magenta and then green toward its rim.
vec3 lensTint(float rim, float warmth)
{
    vec3 warm = mix(vec3(1.0, 0.62, 0.3), vec3(0.95, 0.42, 0.6), smoothstep(0.0, 0.5, rim));
    warm = mix(warm, vec3(0.5, 0.82, 0.5), smoothstep(0.45, 0.9, rim));
    return tint(warm, warmth);
}

vec3 highlights(vec2 uv, vec2 split)
{
    return vec3(
        texture(sTD2DInputs[1], uv + split).r,
        texture(sTD2DInputs[1], uv).g,
        texture(sTD2DInputs[1], uv - split).b) * onWall(uv);
}

// Ghosts of the picture's own highlights, flipped through the wall's center.
vec3 imageGhosts(vec2 uv, vec2 aspect, float warmth)
{
    vec2 flip = 1.0 - uv;
    vec2 toCenter = (vec2(0.5) - flip) * IMAGE_SPACING;
    float halfWidth = 0.5 * aspect.x;
    vec3 sum = vec3(0.0);
    for (int i = 0; i < IMAGE_GHOSTS; i++) {
        vec2 at = flip + toCenter * float(i);
        float rim = length((at - 0.5) * aspect) / halfWidth;
        float weight = pow(max(0.0, 1.0 - rim), 2.0);
        sum += highlights(at, (at - 0.5) * IMAGE_CHROMA) * weight * lensTint(rim, warmth);
    }
    vec2 toward = (vec2(0.5) - flip) * aspect;
    vec2 at = flip + normalize(toward + vec2(1e-5, 0.0)) * IMAGE_HALO / aspect;
    float rim = length((at - 0.5) * aspect) / halfWidth;
    float weight = pow(max(0.0, 1.0 - abs(rim - 0.45) * 2.0), 5.0);
    sum += highlights(at, (at - 0.5) * IMAGE_CHROMA * 2.0) * weight * lensTint(rim, warmth);
    return sum;
}

float dirt(vec2 p)
{
    // Greasy smudges, smeared sideways like a wiped thumbprint.
    vec2 q = p * vec2(1.6, 2.6) + DIRT_SEED;
    float n = 0.55 * noise(q) + 0.3 * noise(q * 2.3 + 7.1) + 0.15 * noise(q * 5.1 + 3.7);
    float smudge = smoothstep(0.5, 0.8, n) * 0.6;

    // Dust specks.
    vec2 g = p * 24.0;
    vec2 home = floor(g);
    float speck = 0.0;
    for (int v = -1; v <= 1; v++) {
        for (int u = -1; u <= 1; u++) {
            vec2 cell = home + vec2(float(u), float(v));
            if (hash12(cell + DIRT_SEED) > 0.12) {
                continue;
            }
            vec2 at = cell + 0.2 + 0.6 * vec2(hash12(cell + 1.7), hash12(cell + 9.2));
            float r = 0.08 + 0.3 * hash12(cell + 4.4);
            float d = length(g - at);
            speck = max(speck, (1.0 - smoothstep(0.4 * r, r, d)) * (0.35 + 0.65 * hash12(cell + 2.2)));
        }
    }
    return smudge + speck;
}

void main()
{
    vec2 aspect = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec2 p = vUV.st * aspect;
    vec2 center = 0.5 * aspect;
    vec2 source = uSource.xy * aspect;
    float bright = uSource.z;
    float size = uSource.w;
    float warmth = uLens.x;
    vec2 d = p - source;
    float dist = length(d);

    vec3 coreColor = tint(CORE_COLOR, warmth);
    vec3 glowColor = tint(GLOW_COLOR, warmth);

    float coreR = CORE_RADIUS * size;
    float glowR = GLOW_RADIUS * size;
    float core = CORE_GAIN * exp(-dist * dist / (coreR * coreR));
    float glow = GLOW_GAIN * glowR * glowR / (dist * dist + glowR * glowR);
    float wash = WASH_GAIN * exp(-dist / (WASH_REACH * size));

    // The anamorphic streak always runs level, whatever the sweep's tilt.
    float reach = max(uLook.x, 1e-4);
    float streak = STREAK_GAIN * exp(-abs(d.y) / STREAK_THIN) * exp(-abs(d.x) / reach)
                 + STREAK_WIDE_GAIN * exp(-abs(d.y) / STREAK_WIDE) * exp(-abs(d.x) / (0.4 * reach));
    streak *= step(1e-4, uLook.x);

    // Six iris blades throw six spikes, with finer uneven rays between them.
    float a = atan(d.y, d.x) + uLens.y;
    float spikes = pow(abs(cos(a * 3.0)), 60.0);
    float rays = pow(abs(sin(a * 11.0 + uLens.w)) * abs(sin(a * 17.0 + 1.7 * uLens.w)), 6.0);
    float starR = STAR_REACH * size;
    float star = uLens.z * STAR_GAIN * (spikes + 0.5 * rays) * coreR * 4.0 / (dist + coreR * 4.0) * exp(-dist / starR);

    float haloR = HALO_RADIUS * size;
    vec3 ringAt = haloR * vec3(1.0 + HALO_SPLIT, 1.0, 1.0 - HALO_SPLIT);
    vec3 offRing = (vec3(dist) - ringAt) / (HALO_WIDTH * size);
    vec3 halo = HALO_GAIN * exp(-offRing * offRing);

    vec3 light = (core + glow + star) * coreColor + wash * glowColor + streak * tint(STREAK_COLOR, warmth) + halo;
    light += uLook.y * ghosts(p, source, center, size, warmth);
    light += uLook.z * IMAGE_GAIN * imageGhosts(vUV.st, aspect, warmth);
    float lit = DIRT_FLOOR + exp(-dist / DIRT_REACH);
    light += uLook.w * DIRT_GAIN * lit * dirt(p) * glowColor;

    fragColor = TDOutputSwizzle(vec4(light * bright, 1.0));
}
'''

COMPOSITE_SHADER = r'''
// Input 0 is the picture, input 1 the flare light.
uniform vec4 uVeil;  // glare, warmth, mix, unused

layout(location = 0) out vec4 fragColor;

const vec3 VEIL_WARM = vec3(1.0, 0.78, 0.55);
const vec3 VEIL_NEUTRAL = vec3(0.85, 0.9, 1.0);
const float VEIL_LIFT = 0.35;  // at full glare the blacks rise this far toward the veil color

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec3 flare = max(texture(sTD2DInputs[1], vUV.st).rgb, vec3(0.0));

    // Glare washes the whole frame: blacks lift and contrast drops.
    vec3 base = mix(source.rgb, mix(VEIL_NEUTRAL, VEIL_WARM, uVeil.y), uVeil.x * VEIL_LIFT);

    // Light adds like light and burns smoothly to white instead of clipping flat. Each channel
    // saturates on its own, so a hot orange core still ends up white.
    vec3 lit = base + max(vec3(1.0) - base, vec3(0.0)) * (vec3(1.0) - exp(-flare));
    fragColor = TDOutputSwizzle(mix(source, vec4(lit, source.a), uVeil.z));
}
'''

EFFECT_SOURCE = r'''
import math
import random

MARGIN = 0.3            # wall heights past each edge where a sweep starts and ends
ANGLE_RANGE = 0.6       # radians the path tilts at Angle 0 or 1
HEIGHT_DRIFT = 0.04     # each sweep lands this close to Height, so repeats don't look canned
EASE = 0.65             # 0 sweeps at a constant speed, 1 eases fully in and out
FIRST_LIGHT = 0.6       # share of full brightness on the firing frame, so a hit lands at once
FADE_IN = 0.04          # share of the sweep spent reaching full brightness
FADE_OUT = 0.15         # share of the sweep spent fading out
SIDE_LIGHT = 0.45       # brightness at the wall's edges, against 1 crossing its center
INTENSITY_SCALE = 1.6
SOFT_HIT = 0.3          # a soft hit keeps this share of a hard hit's brightness
STREAK_RANGE = 3.5      # wall heights the streak reaches at full Streak
SOFT_STREAK = 0.35
SIZE_MIN = 0.5
SIZE_RANGE = 1.1
STAR_TURN = 0.5         # radians the starburst turns over a sweep
GLARE_SCALE = 0.8
GLARE_PEAK = 2.0        # glare gathers toward the center: higher is a tighter peak
CUTOFF_MIN = 0.35       # highlight cutoff at Threshold 0
CUTOFF_RANGE = 0.6
CUTOFF_SOFT = 0.15
SEED_RANGE = 100.0

_rng = random.Random()
_hit = None
_hits = 0


def reset(block):
    global _hit, _hits
    _hit = None
    _hits = 0


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_extract(block):
    cutoff = CUTOFF_MIN + CUTOFF_RANGE * _clamped(block.par.Threshold)
    _write(op("extract"), 0, cutoff, CUTOFF_SOFT, 0.0, 0.0)


def _write_rest(flare, composite):
    _write(flare, 0, 0.5, 0.5, 0.0, 1.0)
    _write(flare, 1, 0.0, 0.0, 0.0, 0.0)
    _write(flare, 2, 0.0, 0.0, 0.0, 0.0)
    _write(composite, 0, 0.0, 0.0, 0.0, 0.0)


def _roll(block, velocity):
    # The path is fixed for the life of a sweep, because changing it mid-air would jump.
    global _hits
    choice = block.par.Direction.eval()
    if choice == "alternate":
        heading = 1.0 if _hits % 2 == 0 else -1.0
    elif choice == "random":
        heading = _rng.choice((-1.0, 1.0))
    else:
        heading = 1.0 if choice == "right" else -1.0
    _hits += 1
    return {
        "heading": heading,
        "height": _clamped(block.par.Height) + _rng.uniform(-HEIGHT_DRIFT, HEIGHT_DRIFT),
        "angle": (_clamped(block.par.Angle) - 0.5) * 2.0 * ANGLE_RANGE,
        "turn": _rng.uniform(0.0, math.pi),
        "seed": _rng.random() * SEED_RANGE,
        "strength": SOFT_HIT + (1.0 - SOFT_HIT) * velocity,
        "velocity": velocity,
    }


def _aspect():
    source = op("in1")
    return max(0.01, float(source.width) / max(1.0, float(source.height)))


def source_at(sweep, hit, aspect):
    """Where the source sits, in UV, a given share of the way through its sweep."""
    eased = sweep + EASE * (_smooth(sweep) - sweep)
    cos_a = math.cos(hit["angle"])
    sin_a = math.sin(hit["angle"])
    half = (0.5 * aspect + MARGIN) / cos_a
    along = (2.0 * eased - 1.0) * half * hit["heading"]
    return (0.5 + along * cos_a / aspect, hit["height"] + along * sin_a)


def light_at(sweep, u):
    """Share of full brightness: quick to land, brightest crossing the center, fading at the end."""
    landing = FIRST_LIGHT + (1.0 - FIRST_LIGHT) * _smooth(sweep / FADE_IN)
    leaving = _smooth((1.0 - sweep) / FADE_OUT)
    closeness = _smooth(1.0 - abs(u - 0.5) * 2.0)
    return landing * leaving * (SIDE_LIGHT + (1.0 - SIDE_LIGHT) * closeness), closeness


def apply(block, env):
    global _hit
    switch = op("switch")
    flare = op("flare")
    composite = op("composite")
    _write_extract(block)
    if env.stage == "idle":
        switch.par.index = 0
        _hit = None
        _write_rest(flare, composite)
        return

    switch.par.index = 1
    if env.started or _hit is None:
        _hit = _roll(block, env.velocity)

    # Release is the sweep. A nonzero Attack fades the source in while it waits at the edge.
    sweep = env.progress if env.stage == "release" else 0.0
    lead = 1.0 if env.stage == "release" else env.level
    u, v = source_at(sweep, _hit, _aspect())
    share, closeness = light_at(sweep, u)
    share *= lead
    strength = _hit["strength"]

    bright = _clamped(block.par.Intensity) * INTENSITY_SCALE * strength * share
    size = SIZE_MIN + SIZE_RANGE * _clamped(block.par.Size)
    streak = _clamped(block.par.Streak) * STREAK_RANGE * (SOFT_STREAK + (1.0 - SOFT_STREAK) * _hit["velocity"])
    turn = _hit["turn"] + STAR_TURN * sweep * _hit["heading"]
    warmth = _clamped(block.par.Warmth)
    glare = _clamped(block.par.Glare) * GLARE_SCALE * strength * share * closeness ** GLARE_PEAK

    _write(flare, 0, u, v, bright, size)
    _write(flare, 1, streak, _clamped(block.par.Ghosts), _clamped(block.par.Imageghosts), _clamped(block.par.Dirt))
    _write(flare, 2, warmth, turn, _clamped(block.par.Starburst), _hit["seed"])
    _write(composite, 0, glare, warmth, _clamped(block.par.Mix), 0.0)
'''

FLARE_UNIFORMS = ["uSource", "uLook", "uLens"]


def _add_glsl(block, name, x, shader, uniforms, pixel_format):
    node = add_top(block, glslTOP, name, x)
    node.par.pixeldat = shader
    node.par.format = pixel_format
    node.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    node.seq.vec.numBlocks = len(uniforms)
    for index, uniform in enumerate(uniforms):
        setattr(node.par, "vec%dname" % index, uniform)
    return node


def build(library):
    block = new_block(library, "lens_flare")
    # Release is how long a sweep takes to cross the wall. Curve isn't used.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.0, "release": 1.6, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Lens Flare")
    add_float(page, "Intensity", "Intensity", 0.7, 0.0, 1.0, True, True)
    add_float(page, "Size", "Size", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Streak", "Streak", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Starburst", "Starburst", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Ghosts", "Ghosts", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Imageghosts", "Image Ghosts", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Threshold", "Highlight Cutoff", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Glare", "Glare", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Dirt", "Lens Dirt", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Warmth", "Warmth", 0.8, 0.0, 1.0, True, True)
    add_float(page, "Angle", "Path Angle", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Height", "Path Height", 0.6, 0.0, 1.0, True, True)
    add_menu(
        page,
        "Direction",
        "Direction",
        ["alternate", "right", "left", "random"],
        ["Alternate", "Left to Right", "Right to Left", "Random"],
        "alternate",
    )
    # Resolution the flare light is drawn at. It's all soft, so Quarter loses little.
    add_menu(page, "Detail", "Detail", ["useinput", "half", "quarter", "eighth"], ["Full", "Half", "Quarter", "Eighth"], "quarter")
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "extract_shader", EXTRACT_SHADER, -1100, -150)
    add_text(block, "blur_shader", BLUR_SHADER, -900, -150)
    add_text(block, "flare_shader", FLARE_SHADER, -500, -150)
    add_text(block, "composite_shader", COMPOSITE_SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -1300)
    extract = _add_glsl(block, "extract", -1100, "extract_shader", ["uExtract"], "rgba16float")
    extract.par.outputresolution = "eighth"
    blur_x = _add_glsl(block, "blur_x", -900, "blur_shader", ["uBlur"], "rgba16float")
    blur_x.par.outputresolution = "useinput"
    blur_x.par.vec0valuex = 1.0
    blur_x.par.vec0valuey = 0.0
    blur_y = _add_glsl(block, "blur_y", -700, "blur_shader", ["uBlur"], "rgba16float")
    blur_y.par.outputresolution = "useinput"
    blur_y.par.vec0valuex = 0.0
    blur_y.par.vec0valuey = 1.0
    # Float, so the light can run past 1 and still burn to white smoothly in the composite.
    flare = _add_glsl(block, "flare", -500, "flare_shader", FLARE_UNIFORMS, "rgba16float")
    flare.par.outputresolution.expr = "parent().par.Detail.eval()"
    composite = _add_glsl(block, "composite", -300, "composite_shader", ["uVeil"], "useinput")
    composite.par.outputresolution = "useinput"
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(extract)
    extract.outputConnectors[0].connect(blur_x)
    blur_x.outputConnectors[0].connect(blur_y)
    flare.inputConnectors[0].connect(source)
    flare.inputConnectors[1].connect(blur_y)
    composite.inputConnectors[0].connect(source)
    composite.inputConnectors[1].connect(flare)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(composite)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
