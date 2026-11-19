# Sunburst: hold, the picture is reprinted as hard brand-color rays on black around a sun disc

SHADER = r'''
uniform vec4 uSun;     // center x, center y, disc radius, ink width (lengths in wall heights)
uniform vec4 uRays;    // ray count (a multiple of the palette's size), phase in rays, width, screen
uniform vec4 uShape;   // reach (1 is the farthest corner), shutter level, stagger, floor
uniform vec4 uMisc;    // mix, ray palette, sun color, unused

layout(location = 0) out vec4 fragColor;

const float PI = 3.14159265359;
const vec3 LUMA = vec3(0.299, 0.587, 0.114);

// Exact brand values, as 8-bit channels.
const vec3 BLACK = vec3(0.0, 0.0, 0.0) / 255.0;
const vec3 BLUE = vec3(37.0, 115.0, 255.0) / 255.0;
const vec3 PINK = vec3(255.0, 141.0, 255.0) / 255.0;
const vec3 YELLOW = vec3(243.0, 221.0, 62.0) / 255.0;
const vec3 RED = vec3(255.0, 66.0, 77.0) / 255.0;
const vec3 CYAN = vec3(68.0, 248.0, 255.0) / 255.0;
const vec3 WHITE = vec3(255.0, 255.0, 255.0) / 255.0;

// One row per ray palette, padded with its last color. Black is left out
// because the rays print on black. Neighboring rays never share a color,
// because the ray count is always a multiple of the row's length.
const int MAX_STOPS = 5;
const int PALETTES = 7;
const int COUNTS[PALETTES] = int[PALETTES](3, 3, 4, 4, 5, 3, 2);
const vec3 STOPS[PALETTES * MAX_STOPS] = vec3[PALETTES * MAX_STOPS](
    YELLOW, BLUE, CYAN, CYAN, CYAN,     // sun
    PINK, RED, YELLOW, YELLOW, YELLOW,  // hot
    RED, YELLOW, PINK, BLUE, BLUE,      // hell
    RED, PINK, YELLOW, WHITE, WHITE,    // fire
    PINK, YELLOW, CYAN, BLUE, RED,      // disco
    BLUE, CYAN, WHITE, WHITE, WHITE,    // cover
    WHITE, YELLOW, YELLOW, YELLOW, YELLOW // ink
);
const int SUNS = 6;
const vec3 SUN_COLORS[SUNS] = vec3[SUNS](YELLOW, BLUE, PINK, RED, CYAN, WHITE);
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
    int palette = clamp(int(uMisc.y + 0.5), 0, PALETTES - 1);
    int stops = COUNTS[palette];
    vec3 sun = SUN_COLORS[clamp(int(uMisc.z + 0.5), 0, SUNS - 1)];
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

    vec3 color = mix(
        BLACK,
        STOPS[palette * MAX_STOPS + int(mod(ray, float(stops)) + 0.5) % stops],
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
# Ray palettes follow the Remotion brand palettes of the same names, without
# black (the rays print on black) or the light-blue tint. Order matches the shader.
PALETTES = {"sun": 3, "hot": 3, "hell": 4, "fire": 4, "disco": 5, "cover": 3, "ink": 2}
SUN_COLORS = ["yellow", "blue", "pink", "red", "cyan", "white"]
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
    stops = PALETTES[palette]
    target = MIN_RAYS + (MAX_RAYS - MIN_RAYS) * _clamped(block.par.Rays)
    count = stops * max(2, int(round(target / stops)))
    width = (SOFT_WIDTH + (HARD_WIDTH - SOFT_WIDTH) * env.velocity) * pop
    disc = (MIN_DISC + DISC_RANGE * _clamped(block.par.Disc)) * pop * math.sqrt(max(0.0, env.level))

    _write(burst, 0, _clamped(block.par.Centerx), _clamped(block.par.Centery), disc, _clamped(block.par.Ink) * INK_RANGE)
    _write(burst, 1, float(count), _phase(block.par.Spinmode.eval(), rate) % count, width, _clamped(block.par.Screen))
    _write(burst, 2, reach, env.level, STAGGER, max(MIN_FLOOR, _clamped(block.par.Floor)))
    sun = SUN_COLORS.index(block.par.Suncolor.eval())
    _write(burst, 3, _clamped(block.par.Mix), float(list(PALETTES).index(palette)), float(sun))
'''

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
    add_menu(
        page, "Palette", "Ray Colors",
        ["sun", "hot", "hell", "fire", "disco", "cover", "ink"],
        [
            "Sunburst (Yellow Blue Cyan)", "Hot (Pink Red Yellow)", "Hellfire (Red Yellow Pink Blue)",
            "Fire (Red Pink Yellow White)", "Disco (Pink Yellow Cyan Blue Red)", "Cover (Blue Cyan White)",
            "Ink (White Yellow)",
        ],
        "sun",
    )
    add_float(page, "Disc", "Sun Disc", 0.4, 0.0, 1.0, True, True)
    add_menu(
        page, "Suncolor", "Sun Color",
        ["yellow", "blue", "pink", "red", "cyan", "white"],
        ["Yellow", "Blue", "Pink", "Red", "Cyan", "White"],
        "yellow",
    )
    add_float(page, "Ink", "Ink Line", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "burst_shader", SHADER, -300, -150)

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

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
