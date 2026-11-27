# Sunburst: hold, the picture is reprinted as hard brand-color rays on black around a sun disc

SHADER = r'''
uniform vec4 uSun;     // center x, center y, disc radius, ink width (lengths in wall heights)
uniform vec4 uRays;    // ray count (a multiple of the palette's size), phase in rays, width, screen
uniform vec4 uShape;   // reach (1 is the farthest corner), shutter level, stagger, floor
uniform vec4 uMisc;    // mix, original ray preset, sun color, shared palette (0 is original)

layout(location = 0) out vec4 fragColor;

const float PI = 3.14159265359;
const vec3 LUMA = vec3(0.299, 0.587, 0.114);

const vec3 BLACK = @@BLACK@@;
// The original ray presets have different lengths. Pad their rows for lookup.
const int MAX_RAY_STOPS = @@MAX_STOPS@@;
@@RAY_COLORS@@
@@SUN_COLORS@@
@@PALETTE_GLSL@@
@@PALETTE_HIGHLIGHTS@@
const int TAPS = 2;             // softened brightness: (2 * TAPS + 1) squared samples
const float TAP_SPREAD = 0.002; // wall heights between samples

float softLuma(vec2 texel)
{
    // Wedge edges follow shapes in the picture without fizzing on video noise.
    float sum = 0.0;
    for (int y = -TAPS; y <= TAPS; y++) {
        for (int x = -TAPS; x <= TAPS; x++) {
            sum += dot(texture(sTD2DInputs[0], vUV.st + vec2(x, y) * texel).rgb, LUMA);
        }
    }
    float side = float(2 * TAPS + 1);
    return clamp(sum / (side * side), 0.0, 1.0);
}

void main()
{
    vec4 info = uTD2DInfos[0].res;
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 aspect = vec2(info.z / info.w, 1.0);
    float pixel = 1.0 / info.w;
    float ink = uSun.w;

    vec2 point = (vUV.st - uSun.xy) * aspect;
    float radius = max(length(point), pixel * 0.01);
    float reach = uShape.x * length(max(uSun.xy, 1.0 - uSun.xy) * aspect);

    // Each ray carries its own number, so it keeps its color and its place in
    // the shutter sweep as the burst turns.
    int preset = int(uMisc.y);
    int palette = int(uMisc.w) - 1;
    int stops = palette < 0 ? RAY_COUNTS[preset] : STOP_COUNT;
    int discColor = int(uMisc.z);
    vec3 sun = discColor < 0 ? paletteHighlight(palette) : SUN_COLORS[discColor];
    float count = max(2.0, uRays.x);
    float place = (atan(point.y, point.x) / (2.0 * PI) + 0.5) * count + uRays.y;
    float ray = floor(place);
    float across = abs(place - ray - 0.5) * 2.0;    // 0 on the ray's center line, 1 at its border
    float halfWidth = PI * radius / count;          // the same distance in wall heights

    float stagger = uShape.z;
    float open = clamp((uShape.y - stagger * mod(ray, count) / count) / max(1.0 - stagger, 0.001), 0.0, 1.0);

    float shade = uShape.w + (1.0 - uShape.w) * softLuma(info.xy * (TAP_SPREAD * info.w));
    float body = uRays.z * mix(1.0, shade, uRays.w);

    // Signed distances in wall heights, positive inside. One pixel of
    // antialiasing keeps the edges hard without crawling on the LEDs.
    float blade = (open - across) * halfWidth;
    float wedge = (body - across) * halfWidth;
    float tip = reach - radius;

    int stop = int(mod(ray, float(stops)) + 0.5) % stops;
    vec3 rayColor = palette < 0 ? RAY_COLORS[preset * MAX_RAY_STOPS + stop] : paletteStop(palette, stop);
    vec3 color = mix(
        BLACK,
        rayColor,
        clamp(min(min(wedge, blade - ink), tip - ink) / pixel + 0.5, 0.0, 1.0)
    );
    float cover = open > 0.0 ? clamp(min(blade, tip) / pixel + 0.5, 0.0, 1.0) : 0.0;

    if (uSun.z > 0.0) {
        float disc = uSun.z - radius;
        color = mix(color, BLACK, clamp(disc / pixel + 0.5, 0.0, 1.0));
        color = mix(color, sun, clamp((disc - ink) / pixel + 0.5, 0.0, 1.0));
        cover = max(cover, clamp(disc / pixel + 0.5, 0.0, 1.0));
    }

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, cover * uMisc.x), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

MIN_RAYS = 12           # the Rays knob runs from about 12 rays
MAX_RAYS = 48           # to about 48, rounded to a multiple of the palette's size
RAY_COUNTS = @@RAY_COUNTS@@
SUN_COLORS = @@SUN_COLORS@@
PALETTE_INDEX = @@PALETTE_INDEX@@
STOP_COUNT = @@STOP_COUNT@@
MAX_SPIN = 8.0          # rays per second at the top of the Spin knob
CLICK_SECONDS = 0.07    # how long one ratchet step takes to land
BACK = 1.70158          # how far a ratchet step overshoots
FIRST_REACH = 0.55      # share of the wall already covered on the firing frame
FULL_REACH = 1.05
OPEN_SECONDS = 0.08
POP = 0.3               # extra width and disc size on the press
POP_SECONDS = 0.06
SOFT_WIDTH = 0.6        # wedge width from the softest press
HARD_WIDTH = 1.25       # to the hardest, where bright areas fill the ray
STAGGER = 0.5           # share of the release spent sweeping around the circle
MIN_FLOOR = 0.08        # black footage still prints thin rays
MIN_DISC = 0.04         # wall heights: always covers the point where the rays converge
DISC_RANGE = 0.36
INK_RANGE = 0.012       # wall heights
MAX_DT = 0.1

_spin = 0.0
_fired = None
_last_seconds = None


def reset(block):
    global _spin, _fired, _last_seconds
    _spin = 0.0
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


def _write_rest(node):
    _write(node, 0, 0.5, 0.5, 0.0, 0.0)
    _write(node, 1, 3.0, 0.0, 0.0, 0.0)
    _write(node, 2, 0.0, 0.0, 0.0, 0.0)
    _write(node, 3, 0.0, 0.0, 0.0, 0.0)


def _phase(mode, rate):
    if mode == "smooth":
        return _spin
    whole = math.floor(_spin)
    if rate <= 0.0:
        return whole
    # Each step lands fast, overshoots a touch, then sits still until the next.
    amount = min(1.0, (_spin - whole) / rate / CLICK_SECONDS) - 1.0
    return whole + (BACK + 1.0) * amount ** 3 + BACK * amount ** 2


def apply(block, env):
    global _spin, _fired, _last_seconds
    switch = op("switch")
    burst = op("burst")
    if env.stage == "idle":
        switch.par.index = 0
        _fired = None
        _last_seconds = None
        _write_rest(burst)
        return

    switch.par.index = 1
    now = absTime.seconds
    if env.started or _fired is None:
        _fired = now
    dt = 0.0 if _last_seconds is None else max(0.0, min(MAX_DT, now - _last_seconds))
    _last_seconds = now

    # The burst keeps turning through the release, and the next press picks up
    # where this one stopped, so repeats don't look canned.
    rate = _clamped(block.par.Spin) * MAX_SPIN
    _spin += rate * dt

    age = now - _fired
    grown = min(1.0, age / OPEN_SECONDS)
    reach = FIRST_REACH + (FULL_REACH - FIRST_REACH) * (1.0 - (1.0 - grown) ** 2)
    pop = 1.0 + POP * math.exp(-age / POP_SECONDS)

    palette = block.par.Palette.eval()
    shared = PALETTE_INDEX.get(palette, -1)
    stops = STOP_COUNT if shared >= 0 else RAY_COUNTS[palette]
    target = MIN_RAYS + (MAX_RAYS - MIN_RAYS) * _clamped(block.par.Rays)
    count = stops * max(2, int(round(target / stops)))
    width = (SOFT_WIDTH + (HARD_WIDTH - SOFT_WIDTH) * env.velocity) * pop
    disc = (MIN_DISC + DISC_RANGE * _clamped(block.par.Disc)) * pop * math.sqrt(max(0.0, env.level))

    _write(burst, 0, _clamped(block.par.Centerx), _clamped(block.par.Centery), disc, _clamped(block.par.Ink) * INK_RANGE)
    _write(burst, 1, float(count), _phase(block.par.Spinmode.eval(), rate) % count, width, _clamped(block.par.Screen))
    _write(burst, 2, reach, env.level, STAGGER, max(MIN_FLOOR, _clamped(block.par.Floor)))
    disc_color = block.par.Suncolor.eval()
    if disc_color == "match":
        sun = -1 if shared >= 0 else 0
    else:
        sun = SUN_COLORS.index(disc_color)
    preset = list(RAY_COUNTS).index(palette) if shared < 0 else 0
    _write(burst, 3, _clamped(block.par.Mix), float(preset), float(sun), float(shared + 1))
'''

RAY_PRESETS = {
    "sun": ("Sunburst", ("yellow", "blue", "cyan")),
    "hot": ("Hot", ("pink", "red", "yellow")),
    "hell": ("Hellfire", ("red", "yellow", "pink", "blue")),
    "fire": ("Fire", ("red", "pink", "yellow", "white")),
    "disco": ("Disco", ("pink", "yellow", "cyan", "blue", "red")),
    "cover": ("Cover", ("blue", "cyan", "white")),
    "ink": ("Ink", ("white", "yellow")),
}
SUN_COLORS = ("yellow", "blue", "pink", "red", "cyan", "white")
UNIFORMS = ["uSun", "uRays", "uShape", "uMisc"]


def build(library):
    block = new_block(library, "sunburst")
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.25, "release": 0.2, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Sunburst")
    add_float(page, "Rays", "Ray Count", 0.33, 0.0, 1.0, True, True)
    add_float(page, "Spin", "Spin", 0.4, 0.0, 1.0, True, True)
    add_menu(page, "Spinmode", "Spin Mode", ["ratchet", "smooth"], ["Ratchet", "Smooth"], "ratchet")
    add_float(page, "Screen", "Picture Screen", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Floor", "Luma Floor", 0.3, 0.0, 1.0, True, True)
    add_float(page, "Centerx", "Center X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Centery", "Center Y", 0.5, 0.0, 1.0, True, True)
    add_palette_menu(
        page, "Palette", "Ray Colors",
        presets={
            name: f"{label} ({' '.join(color.title() for color in row)})"
            for name, (label, row) in RAY_PRESETS.items()
        }, default="sun",
    )
    add_float(page, "Disc", "Sun Disc", 0.4, 0.0, 1.0, True, True)
    add_menu(
        page, "Suncolor", "Sun Color", [*SUN_COLORS, "match"],
        [*(color.title() for color in SUN_COLORS), "Match Ray Palette"], "match",
    )
    add_float(page, "Ink", "Ink Line", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    ray_counts = {name: len(row) for name, (_, row) in RAY_PRESETS.items()}
    max_stops = max(ray_counts.values())
    rays = tuple(
        color
        for _, row in RAY_PRESETS.values()
        for color in row + (row[-1],) * (max_stops - len(row))
    )
    size = len(ray_counts)
    counts = ", ".join(map(str, ray_counts.values()))
    ray_colors = f"const int RAY_COUNTS[{size}] = int[{size}]({counts});"
    shader = palette_shader(
        SHADER, BLACK=glsl_rgb(SWATCH_RGB8["black"]), MAX_STOPS=str(max_stops),
        RAY_COLORS=ray_colors + "\n" + glsl_swatches("RAY_COLORS", rays),
        SUN_COLORS=glsl_swatches("SUN_COLORS", SUN_COLORS),
    )
    add_text(block, "burst_shader", shader, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    burst = add_top(block, glslTOP, "burst", -300)
    burst.par.pixeldat = "burst_shader"
    burst.par.outputresolution = "useinput"
    burst.par.format = "useinput"
    burst.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    burst.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(burst.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(burst)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(burst)
    switch.outputConnectors[0].connect(output)

    effect = palette_effect(
        EFFECT_SOURCE, PALETTE_INDEX=PALETTE_INDEX, STOP_COUNT=STOP_COUNT,
        RAY_COUNTS=ray_counts, SUN_COLORS=SUN_COLORS,
    )
    add_text(block, "effect", effect, -500, -300)
    add_callbacks(block)
    return block
