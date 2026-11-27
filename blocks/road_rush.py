# Road Rush: hold, the picture tips over into a road you steer, then crashes back up

SHADER = r'''
uniform vec4 uView;    // tilt (1 / horizon height), depth exponent, lean in radians, drop (0 flat, 1 landed)
uniform vec4 uRoad;    // curve at the horizon, road half width, distance driven, video scroll
uniform vec4 uDash;    // dash duty, sun radius, sun spin, ink (lengths in wall heights)
uniform vec4 uSky;     // sky color
uniform vec4 uGround;  // ground color
uniform vec4 uMisc;    // mix, shake x, shake y, palette (0 is brand)

layout(location = 0) out vec4 fragColor;

const float PI = 3.14159265359;
const vec3 BLACK = vec3(0.0);
const vec3 WHITE = vec3(1.0);
const vec3 YELLOW = @@YELLOW@@;
const vec3 RED = @@RED@@;
@@PALETTE_GLSL@@
@@PALETTE_HIGHLIGHTS@@

vec3 roadAccent()
{
    int palette = int(uMisc.w) - 1;
    return palette < 0 ? YELLOW : paletteHighlight(palette);
}

const float FAR = 10.0;         // past this depth everything is flat color, so the LEDs can't crawl
const float DASH_PERIOD = 1.2;  // the three periods divide evenly, so the distance can wrap at 2.4
const float RUMBLE_PERIOD = 0.6;
const float GROUND_PERIOD = 2.4;
const float LANE = 0.3333;      // dash lines at a third of the road's half width, each side of center
const float DASH_WIDTH = 0.022; // in road half widths
const float DASH_MIN_DROP = 0.25;
const float RUMBLE_WIDTH = 0.13;
const float SUN_POINTS = 14.0;
const float SUN_SPIKE = 0.24;

float cover(float signedDistance, float footprint)
{
    return clamp(signedDistance / max(footprint, 0.000001) + 0.5, 0.0, 1.0);
}

float mirrored(float value)
{
    return 1.0 - abs(1.0 - mod(value, 2.0));
}

vec3 roadVideo(float u, float v, float spread)
{
    // A few taps along the road's depth stop far video detail from sparkling.
    vec2 st = vec2(mirrored(u), mirrored(v));
    vec3 sum = texture(sTD2DInputs[0], st).rgb;
    sum += texture(sTD2DInputs[0], vec2(st.x, mirrored(v - spread))).rgb;
    sum += texture(sTD2DInputs[0], vec2(st.x, mirrored(v + spread))).rgb;
    return sum / 3.0;
}

vec3 drawSky(vec2 point, float center, float horizon, float pixel)
{
    vec3 color = uSky.rgb;
    if (uDash.y <= 0.0) {
        return color;
    }
    vec2 fromSun = point - vec2(center, horizon);
    float angle = atan(fromSun.y, fromSun.x) + uDash.z;
    float spike = abs(fract(angle * SUN_POINTS / (2.0 * PI)) - 0.5) * 2.0;
    float edge = uDash.y * (1.0 + SUN_SPIKE * spike) - length(fromSun);
    color = mix(color, BLACK, cover(edge, pixel));
    return mix(color, roadAccent(), cover(edge - uDash.w, pixel));
}

void main()
{
    vec4 info = uTD2DInfos[0].res;
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    float aspect = info.z / info.w;
    float pixel = 1.0 / info.w;

    float tilt = uView.x;
    float depthPower = uView.y;
    float drop = uView.w;
    float landed = clamp(drop, 0.0, 1.0);

    // Wall heights: x from the center of the wall, y from the bottom edge.
    vec2 point = (vUV.st - vec2(0.5, 0.0)) * vec2(aspect, 1.0) + uMisc.yz;
    vec2 pivot = point - vec2(0.0, 0.5);
    float leanSin = sin(uView.z);
    float leanCos = cos(uView.z);
    point = vec2(pivot.x * leanCos - pivot.y * leanSin, pivot.x * leanSin + pivot.y * leanCos + 0.5);

    float horizon = 1.0 / tilt;
    float belowHorizon = horizon - point.y;
    float ahead = max(1.0 - point.y * tilt, 0.0001);
    float z = pow(ahead, -depthPower);
    float zFootprint = depthPower * tilt * pow(ahead, -depthPower - 1.0) * pixel;

    // Flat, the road is exactly as wide as the wall and one picture deep.
    float halfWidth = mix(aspect * 0.5, uRoad.y, landed);
    float bend = 1.0 - min(ahead, 1.0);
    float across = (point.x - uRoad.x * bend * bend) / (ahead * halfWidth);
    float side = abs(across);
    float xFootprint = pixel / (ahead * halfWidth);
    float ink = uDash.w / halfWidth;

    float roadDepth = 2.0 * uRoad.y / aspect;
    if (tilt < 1.0) {
        roadDepth = min(roadDepth, tilt / (1.0 - tilt));
    }
    float travelled = z + uRoad.z;
    bool distant = z > FAR;

    vec3 road = BLACK;
    if (!distant) {
        road = roadVideo(
            across * 0.5 + 0.5,
            (z - 1.0) / roadDepth + uRoad.w * landed,
            zFootprint / roadDepth * 0.33
        );

        // The lines thin out as the road tips up and are gone before it's flat.
        float dashAcross = landed > DASH_MIN_DROP ? DASH_WIDTH * landed - abs(side - LANE) : -1.0;
        float duty = uDash.x;
        float along = duty > 0.97 ? 1.0 : (duty * 0.5 - abs(fract(travelled / DASH_PERIOD) - 0.5)) * DASH_PERIOD / halfWidth;
        float dash = min(dashAcross, along);
        float footprint = max(xFootprint, zFootprint / halfWidth);
        road = mix(road, BLACK, cover(dash, footprint));
        road = mix(road, roadAccent(), cover(dash - ink, footprint));
    }

    vec3 ground = uGround.rgb;
    vec3 rumble = WHITE;
    if (!distant) {
        float band = (0.25 - abs(fract(travelled / GROUND_PERIOD) - 0.5)) * GROUND_PERIOD;
        ground = mix(BLACK, ground, cover(band, zFootprint));
        float stripe = (0.25 - abs(fract(travelled / RUMBLE_PERIOD) - 0.5)) * RUMBLE_PERIOD;
        int palette = int(uMisc.w) - 1;
        vec3 stripeColor = palette < 0 ? RED : paletteStop(palette, 1);
        rumble = mix(WHITE, stripeColor, cover(stripe, zFootprint));
    }

    // Only a landed road has edges. Fully flat, the picture mirrors past the
    // wall while it rattles, so no color flickers in at the sides.
    float roadEdge = drop > 0.0 ? 1.0 : 1000.0;
    vec3 color = mix(ground, rumble, cover(roadEdge + RUMBLE_WIDTH - side, xFootprint));
    color = mix(color, BLACK, cover(roadEdge + ink - side, xFootprint));
    color = mix(color, road, cover(roadEdge - side, xFootprint));

    if (belowHorizon < uDash.w + pixel) {
        color = mix(color, BLACK, cover(uDash.w - belowHorizon, pixel));
        color = mix(color, drawSky(point, uRoad.x, horizon, pixel), cover(-belowHorizon, pixel));
    }

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uMisc.x), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

FLAT_TILT = 0.004       # effectively no perspective, and safe to divide by
FIRST_DROP = 0.35       # share of the drop already done on the firing frame
DROP_SECONDS = 0.14
DROP_BOUNCE = 2.2       # how far the horizon overshoots as the road lands
HORIZON_LOW = 0.45      # wall heights: range of the Horizon knob
HORIZON_HIGH = 0.78
HILL_RANGE = 0.16       # wall heights the horizon moves at either end of the Hill knob
HILL_POWER = 0.5        # how strongly a hill bends the road's depth
ROAD_NARROW = 0.75      # wall heights: range of the Road Width knob
ROAD_WIDE = 1.6
STEER_RANGE = 1.1       # wall heights the vanishing point moves at either end of Steer
LEAN = 0.075            # radians the wall banks into a full curve
WANDER_STEER = 0.7      # wall heights of drift at the top of the Wander knob
WANDER_HILL = 0.05
WANDER_RATE = 0.13      # radians per unit of road
SPEED_LOW = 3.0         # road units per second, at the bottom of the Speed knob
SPEED_HIGH = 12.0
SOFT_SPEED = 0.35       # share of the speed the softest press gets
BOOST_SPEED = 0.5       # extra speed at the peak of a boost
BOOST_SECONDS = 0.4     # how fast a boost dies away
BOOST_DIP = 0.08        # share the horizon sinks at the peak of a boost
DASH_DUTY = 0.45        # how much of each period is painted line
SUN_RANGE = 0.5         # wall heights at the top of the Sun knob
SUN_RATE = 0.04         # radians per unit of road
INK_RANGE = 0.016       # wall heights
FLIP_SHARE = 0.6        # share of the release spent rearing back up
RATTLE_RANGE = 0.05     # wall heights of shake at the top of the Crash knob
DISTANCE_WRAP = 2.4     # every painted period divides this
SCROLL_WRAP = 2.0       # one there-and-back of the mirrored video
MAX_DT = 0.1
TWO_PI = math.pi * 2.0

THEMES = @@THEMES@@
PALETTE_INDEX = @@PALETTE_INDEX@@

_fired = None
_last_seconds = None
_drop = 0.0
_drop_from = 0.0
_crash_from = None
_distance = 0.0
_scroll = 0.0
_wander = 0.0
_boost = 0.0
_boost_prev = 0.0


def _clear():
    global _fired, _last_seconds, _drop, _drop_from, _crash_from
    global _distance, _scroll, _wander, _boost, _boost_prev
    _fired = None
    _last_seconds = None
    _drop = 0.0
    _drop_from = 0.0
    _crash_from = None
    _distance = 0.0
    _scroll = 0.0
    _wander = 0.0
    _boost = 0.0
    _boost_prev = 0.0


def reset(block):
    _clear()


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y=0.0, z=0.0, w=0.0):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest(node):
    _write(node, 0, FLAT_TILT, 1.0, 0.0, 0.0)
    for index in range(1, 6):
        _write(node, index, 0.0)


def _landing(amount):
    # Fast, with one bounce past the landing point.
    amount = min(1.0, amount) - 1.0
    return 1.0 + (DROP_BOUNCE + 1.0) * amount ** 3 + DROP_BOUNCE * amount ** 2


def _advance_drop(block, env, now):
    """Return the drop for this frame, 0 flat and 1 landed, and whether it's fully flat."""
    global _drop, _drop_from, _crash_from
    if env.stage == "release":
        if _crash_from is None:
            _crash_from = _drop
        flip = min(1.0, env.progress / FLIP_SHARE)
        _drop = _crash_from * (1.0 - flip ** max(0.01, block.par.Curve.eval()))
        return _drop, flip >= 1.0

    _crash_from = None
    seconds = max(DROP_SECONDS, block.par.Attack.eval(), 0.001)
    landing = FIRST_DROP + (1.0 - FIRST_DROP) * _landing((now - _fired) / seconds)
    _drop = _drop_from + (1.0 - _drop_from) * landing
    return _drop, False


def _rattle(block, env, now):
    if env.stage != "release" or env.progress <= FLIP_SHARE:
        return 0.0, 0.0
    fading = (env.progress - FLIP_SHARE) / (1.0 - FLIP_SHARE)
    amount = _clamped(block.par.Crash) * RATTLE_RANGE * env.velocity * (1.0 - fading) ** 2
    return amount * math.sin(now * 91.0), amount * math.cos(now * 73.0)


def apply(block, env):
    global _fired, _last_seconds, _drop_from, _distance, _scroll, _wander, _boost, _boost_prev
    switch = op("switch")
    road = op("road")
    if env.stage == "idle":
        switch.par.index = 0
        _clear()
        _write_rest(road)
        return

    switch.par.index = 1
    now = absTime.seconds
    if env.started or _fired is None:
        _drop_from = max(0.0, min(1.0, _drop))
        _fired = now
    dt = 0.0 if _last_seconds is None else max(0.0, min(MAX_DT, now - _last_seconds))
    _last_seconds = now

    # Boost fires on its own key's press, so it can be played in rhythm.
    boost_input = block.par.Boost.eval()
    if boost_input > 0.0 and _boost_prev <= 0.0:
        _boost = max(_boost, min(1.0, boost_input))
    else:
        _boost *= math.exp(-dt / BOOST_SECONDS)
    _boost_prev = boost_input

    cruise = SPEED_LOW + (SPEED_HIGH - SPEED_LOW) * _clamped(block.par.Speed)
    speed = cruise * (SOFT_SPEED + (1.0 - SOFT_SPEED) * env.velocity) * (1.0 + BOOST_SPEED * _boost)
    _distance = (_distance + speed * dt) % DISTANCE_WRAP
    _scroll = (_scroll + speed * dt * _clamped(block.par.Videoscroll)) % SCROLL_WRAP
    _wander = (_wander + speed * dt * WANDER_RATE) % TWO_PI

    drop, flat = _advance_drop(block, env, now)
    landed = max(0.0, min(1.0, drop))
    wander = _clamped(block.par.Wander)

    hill = (_clamped(block.par.Hill) - 0.5) * 2.0
    horizon = HORIZON_LOW + (HORIZON_HIGH - HORIZON_LOW) * _clamped(block.par.Horizon)
    horizon += hill * HILL_RANGE + wander * WANDER_HILL * math.sin(_wander * 2.0)
    horizon *= 1.0 - BOOST_DIP * _boost
    tilt = FLAT_TILT + (1.0 / horizon - FLAT_TILT) * drop
    depth_power = 1.0 + hill * HILL_POWER * landed

    steer = (_clamped(block.par.Steer) - 0.5) * 2.0
    curve = steer * STEER_RANGE + wander * WANDER_STEER * math.sin(_wander)
    lean = -curve / (STEER_RANGE + WANDER_STEER) * LEAN * landed

    width = ROAD_NARROW + (ROAD_WIDE - ROAD_NARROW) * _clamped(block.par.Roadwidth)
    duty = DASH_DUTY + (1.0 - DASH_DUTY) * _boost
    sun = _clamped(block.par.Sun) * SUN_RANGE * landed
    ink = _clamped(block.par.Ink) * INK_RANGE
    theme = block.par.Sky.eval()
    sky, ground = THEMES.get(theme, THEMES["blue"])
    shake_x, shake_y = _rattle(block, env, now)

    _write(road, 0, tilt, depth_power, lean, 0.0 if flat else max(drop, 0.0001))
    _write(road, 1, curve * landed, width, _distance, _scroll)
    _write(road, 2, duty, sun, _distance * SUN_RATE * TWO_PI, ink)
    _write(road, 3, sky[0], sky[1], sky[2], 1.0)
    _write(road, 4, ground[0], ground[1], ground[2], 1.0)
    _write(road, 5, _clamped(block.par.Mix), shake_x, shake_y, PALETTE_INDEX.get(theme, -1) + 1)
'''

SKY_PRESETS = {"blue": "pink", "red": "yellow", "pink": "cyan", "cyan": "blue"}
UNIFORMS = ["uView", "uRoad", "uDash", "uSky", "uGround", "uMisc"]


def build(library):
    block = new_block(library, "road_rush")
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.25, "release": 0.42, "curve": 2.5, "velocitydepth": 1.0})
    page = block.appendCustomPage("Road Rush")
    add_float(page, "Speed", "Speed", 0.55, 0.0, 1.0, True, True)
    add_float(page, "Steer", "Steer", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Hill", "Hill", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Boost", "Boost", 0.0, 0.0, 1.0)
    add_float(page, "Wander", "Wander", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Horizon", "Horizon", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Roadwidth", "Road Width", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Videoscroll", "Video Scroll", 1.0, 0.0, 1.0, True, True)
    add_palette_menu(page, "Sky", "Sky", presets={name: name.title() for name in SKY_PRESETS}, default="blue")
    add_float(page, "Sun", "Sun", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Ink", "Ink Line", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Crash", "Crash", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    shader = palette_shader(SHADER, YELLOW=glsl_rgb(SWATCH_RGB8["yellow"]), RED=glsl_rgb(SWATCH_RGB8["red"]))
    add_text(block, "road_shader", shader, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    road = add_top(block, glslTOP, "road", -300)
    road.par.pixeldat = "road_shader"
    road.par.outputresolution = "useinput"
    road.par.format = "useinput"
    road.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    road.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(road.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(road)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(road)
    switch.outputConnectors[0].connect(output)

    themes = {name: (SWATCH_RGB[name], SWATCH_RGB[ground]) for name, ground in SKY_PRESETS.items()}
    themes.update({name: (row[3], row[0]) for name, row in PALETTE_RGB.items()})
    effect = palette_effect(EFFECT_SOURCE, PALETTE_INDEX=PALETTE_INDEX, THEMES=themes)
    add_text(block, "effect", effect, -500, -300)
    add_callbacks(block)
    return block
