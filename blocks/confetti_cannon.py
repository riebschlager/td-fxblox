# Confetti Cannon: tail hit, flat brand-color paper rains in or bursts out over the video
#
# Under drag a particle's position is linear in its launch velocity, so a pixel can be
# mapped back into velocity space, where the particles sit on a fixed grid. Each pixel
# then checks only its nearest grid cells, and the cost doesn't grow with Count.

SHADER = r'''
uniform vec4 uLook;    // count, radius, ink, shadow (lengths in wall heights)
uniform vec4 uFlight;  // launch speed, half cone in radians, gravity, drag
uniform vec4 uAir;     // flutter, spin, wind, origin across the wall
uniform vec4 uRig;     // cannons, mix, band half width for Rain, palette (0 is brand)
uniform vec4 uKind;    // each shot's motion: 0 rain, 1 burst, 2 corners, 3 blast, -1 empty
uniform vec4 uPour;    // each shot's seconds of pouring, Rain only
uniform vec4 uShot0;   // age, seed, velocity, level
uniform vec4 uShot1;
uniform vec4 uShot2;
uniform vec4 uShot3;

layout(location = 0) out vec4 fragColor;

const int SHOTS = 4;
const float PI = 3.14159265;
const float TAU = 6.2831853;
@@SWATCHES@@
@@PALETTE_GLSL@@
const float JITTER = 0.5;         // of a cell, so a particle strays a quarter cell either way
const float SWAY = 0.2;           // of a cell, the flutter's reach
const float FALL_MIN = 0.65;      // columns fall at different speeds so nothing moves as a sheet
const float FALL_RANGE = 0.7;
const float SOFT_SPEED = 0.45;
const float SOFT_COUNT = 0.35;
const float CORNER_SPEED = 1.6;   // the wall is wide, so corner cannons throw further
const vec2 CORNER_AIM = vec2(0.75, 0.66);
const float INNER_SPEED = 0.05;   // of the launch speed: nothing dribbles out of the muzzle
const float RAGGED_EDGE = 0.35;   // of the launch speed: the fastest paper thins out at random, so no hard arc
const float MUZZLE_DROP = 0.02;   // cannons sit just below the wall
const float RAIN_LEAD = 0.14;     // Rain is already this far down the wall on the first frame
const float SLOWEST_RAIN = 0.05;
const float FLIP_RATE = 0.7;      // of the spin
const float THINNEST = 0.25;      // edge-on, a flipped rectangle keeps this much height
const float RECT_HEIGHT = 0.55;
const float CIRCLE_RADIUS = 0.62;
const float CIRCLE_SHARE = 0.72;  // ids above this are dots, the rest are strips
const float INK_SHARE = 0.3;      // of the radius
const float SHADOW_SHARE = 0.7;   // of the radius
const float POP = 0.12;           // of the level, how long one particle takes to pop out
const vec2 SHADOW_AIM = vec2(0.7071, -0.7071);

vec3 hash(vec3 p)
{
    p = fract(p * vec3(0.1031, 0.1030, 0.0973));
    p += dot(p, p.yxz + 33.33);
    return fract((p.xxy + p.yxx) * p.zyx);
}

float cover(float distance_, float px)
{
    return 1.0 - smoothstep(-px, px, distance_);
}

float shapeDistance(vec2 q, float radius, float flip, bool dot_)
{
    if (dot_) {
        return length(q) - radius * CIRCLE_RADIUS;
    }
    vec2 edge = abs(q) - radius * vec2(1.0, RECT_HEIGHT * flip);
    return length(max(edge, 0.0)) + min(max(edge.x, edge.y), 0.0);
}

// The largest a particle may reach, in radii, so the neighbor search always finds it.
float extent()
{
    return 1.16 + INK_SHARE * uLook.z + SHADOW_SHARE * uLook.w;
}

vec4 paint = vec4(0.0);
float paintDepth = -1.0;

void stamp(vec2 q, vec3 id, float age, float level, float radius, float px)
{
    if (id.x <= paintDepth) {
        return;
    }
    radius *= clamp((level - id.y * (1.0 - POP)) / POP, 0.0, 1.0);
    if (radius < px * 0.5) {
        return;
    }
    float direction = id.z < 0.5 ? -1.0 : 1.0;
    float angle = id.z * TAU + uAir.y * age * direction;
    float s = sin(angle);
    float c = cos(angle);
    bool dot_ = fract(id.x * 7.13) > CIRCLE_SHARE;
    float flip = THINNEST + (1.0 - THINNEST) * abs(cos(id.y * TAU + uAir.y * age * FLIP_RATE));
    float ink = radius * INK_SHARE * uLook.z;
    vec2 local = mat2(c, -s, s, c) * q;
    float body = shapeDistance(local, radius, flip, dot_);
    vec2 thrown = mat2(c, -s, s, c) * (q - SHADOW_AIM * radius * SHADOW_SHARE * uLook.w);
    float shadow = uLook.w > 0.0 ? shapeDistance(thrown, radius, flip, dot_) : 1.0;
    float alpha = max(cover(body - ink, px), cover(shadow - ink, px));
    if (alpha <= 0.0) {
        return;
    }
    float pick = fract(id.z * 11.7);
    int palette = int(uRig.w) - 1;
    vec3 color = palette < 0
        ? BRAND[int(pick * float(BRAND.length()))]
        : paletteStop(palette, int(pick * float(STOP_COUNT)));
    paint = vec4(color * cover(body, px), alpha);
    paintDepth = id.x;
}

void cannon(vec2 p, vec2 origin, vec2 aim, float cone, float speed, float count, vec4 shot, float salt, float px)
{
    float age = shot.x;
    float seed = shot.y + salt;
    float gravity = uFlight.z;
    float drag = uFlight.w;
    float reach = (1.0 - exp(-drag * age)) / drag;
    float drift = age - reach;
    float windShift = uAir.z * drift;
    float cell = speed * sqrt(cone / count);
    float kept = SOFT_COUNT + (1.0 - SOFT_COUNT) * shot.z;
    float cosine = cos(cone);
    float radiusMost = uLook.y;
    float far = speed + 2.0 * cell;

    float xVelocity = (p.x - origin.x - windShift) / reach;
    if (abs(xVelocity) > far) {
        return;
    }
    float homeColumn = floor(xVelocity / cell);
    for (int i = -1; i <= 1; i++) {
        float column = homeColumn + float(i);
        float fall = FALL_MIN + FALL_RANGE * hash(vec3(column, seed, 5.0)).x;
        float dropped = gravity / drag * drift * fall;
        float yVelocity = (p.y - origin.y + dropped) / reach;
        float homeRow = floor(yVelocity / cell);
        for (int j = -1; j <= 1; j++) {
            float row = homeRow + float(j);
            vec3 pick = hash(vec3(column, row, seed));
            if (pick.x > kept) {
                continue;
            }
            vec2 launch = (vec2(column, row) + 0.5 + (pick.yz - 0.5) * JITTER) * cell;
            float magnitude = length(launch);
            float limit = speed * (1.0 - RAGGED_EDGE * fract(pick.x * 13.7));
            if (magnitude > limit || magnitude < speed * INNER_SPEED) {
                continue;
            }
            if (dot(launch / magnitude, aim) < cosine) {
                continue;
            }
            vec3 id = hash(vec3(row, column, seed + 19.0));
            float sway = SWAY * cell * uAir.x * min(1.0, reach * drag) * sin(age * (3.0 + 4.0 * id.y) + id.z * TAU);
            vec2 centre = origin + launch * reach + vec2(windShift + sway * reach, -dropped);
            float radius = min(radiusMost, cell * reach / extent());
            stamp(p - centre, id, age, shot.w, radius, px);
        }
    }
}

void rain(vec2 p, float wide, float count, float pour, vec4 shot, float px)
{
    float age = shot.x;
    float seed = shot.y;
    float gravity = uFlight.z;
    float drag = uFlight.w;
    float speed = max(SLOWEST_RAIN, gravity / drag);
    float drift = age - (1.0 - exp(-drag * age)) / drag;
    float windShift = uAir.z * drift;
    float cell = sqrt(wide / count);
    float kept = SOFT_COUNT + (1.0 - SOFT_COUNT) * shot.z;
    float radius = min(uLook.y, cell / extent());
    float bandCentre = uAir.w * wide;
    float bandHalf = uRig.z * wide;

    float homeColumn = floor((p.x - windShift) / cell);
    for (int i = -1; i <= 1; i++) {
        float column = homeColumn + float(i);
        float fall = FALL_MIN + FALL_RANGE * hash(vec3(column, seed, 5.0)).x;
        float dropped = speed * drift * fall;
        float homeRow = floor((p.y - 1.0 + RAIN_LEAD + dropped) / cell);
        for (int j = -1; j <= 1; j++) {
            float row = homeRow + float(j);
            if (row < 0.0 || row * cell > pour * speed * fall) {
                continue;
            }
            vec3 pick = hash(vec3(column, row, seed));
            if (pick.x > kept) {
                continue;
            }
            vec2 grid = (vec2(column, row) + 0.5 + (pick.yz - 0.5) * JITTER) * cell;
            if (abs(grid.x - bandCentre) > bandHalf) {
                continue;
            }
            vec3 id = hash(vec3(row, column, seed + 19.0));
            float sway = SWAY * cell * uAir.x * sin(age * (3.0 + 4.0 * id.y) + id.z * TAU);
            vec2 centre = vec2(grid.x + windShift + sway, grid.y + 1.0 - RAIN_LEAD - dropped);
            stamp(p - centre, id, age, shot.w, radius, px);
        }
    }
}

void main()
{
    float wide = uTD2DInfos[0].res.z / uTD2DInfos[0].res.w;
    vec2 p = vec2(vUV.s * wide, vUV.t);
    float px = uTD2DInfos[0].res.y;
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec4 shots[SHOTS] = vec4[SHOTS](uShot0, uShot1, uShot2, uShot3);
    float count = uLook.x;
    float cone = uFlight.y;

    for (int s = 0; s < SHOTS; s++) {
        float kind = uKind[s];
        vec4 shot = shots[s];
        if (kind < -0.5 || shot.w <= 0.0) {
            continue;
        }
        float speed = uFlight.x * (SOFT_SPEED + (1.0 - SOFT_SPEED) * shot.z);
        if (kind < 0.5) {
            rain(p, wide, count, uPour[s], shot, px);
        } else if (kind < 1.5) {
            int cannons = int(uRig.x);
            float each = count / float(cannons);
            for (int e = 0; e < cannons; e++) {
                float x = cannons == 1 ? uAir.w * wide : (float(e) + 0.5) / float(cannons) * wide;
                cannon(p, vec2(x, -MUZZLE_DROP), vec2(0.0, 1.0), cone, speed, each, shot, float(e) * 3.1, px);
            }
        } else if (kind < 2.5) {
            float each = count * 0.5;
            vec2 aim = normalize(CORNER_AIM);
            cannon(p, vec2(0.0, -MUZZLE_DROP), aim, cone, speed * CORNER_SPEED, each, shot, 0.0, px);
            cannon(p, vec2(wide, -MUZZLE_DROP), vec2(-aim.x, aim.y), cone, speed * CORNER_SPEED, each, shot, 3.1, px);
        } else {
            cannon(p, vec2(uAir.w * wide, 0.5), vec2(0.0, 1.0), PI, speed, count, shot, 0.0, px);
        }
    }

    vec3 color = mix(source.rgb, paint.rgb, paint.a * uRig.y);
    fragColor = TDOutputSwizzle(vec4(color, source.a));
}
'''

EFFECT_SOURCE = r'''
import math
import random

PALETTE_INDEX = @@PALETTE_INDEX@@
COUNT_LOW = 40.0
COUNT_RANGE = 560.0
RADIUS_LOW = 0.006
RADIUS_RANGE = 0.04
SPEED_SCALE = 6.0
CONE_LOW = math.radians(4.0)
CONE_RANGE = math.radians(86.0)
GRAVITY_SCALE = 3.0
DRAG_LOW = 0.6
DRAG_RANGE = 5.0
SPIN_SCALE = 14.0
WIND_SCALE = 0.6
BAND_SCALE = 1.0
SEED_RANGE = 100.0
MAX_SHOTS = 4
FIRST_AGE = 1.0 / 30.0   # so the first frame already has the shot leaving the muzzle
SHORTEST_POUR = 0.2
GATE_POUR_LEAD = 0.1     # while held, Rain always has more paper on the way
KINDS = {"rain": 0.0, "burst": 1.0, "corners": 2.0, "blast": 3.0}
EMPTY = -1.0

_rng = random.Random()
_shots = []


def reset(block):
    global _shots
    _shots = []


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_shots(node):
    kinds = [EMPTY] * MAX_SHOTS
    pours = [0.0] * MAX_SHOTS
    for slot in range(MAX_SHOTS):
        if slot < len(_shots):
            shot = _shots[slot]
            kinds[slot] = shot["kind"]
            pours[slot] = shot["pour"]
            _write(node, 6 + slot, shot["age"], shot["seed"], shot["velocity"], shot["level"])
        else:
            _write(node, 6 + slot, 0.0, 0.0, 0.0, 0.0)
    _write(node, 4, *kinds)
    _write(node, 5, *pours)


def _fire(block, env, now):
    # Earlier shots finish on their own, so a quick run of hits piles up instead of cutting.
    release = max(0.0, block.par.Release.eval())
    for shot in _shots:
        shot["ends"] = min(shot["ends"], now + release)
    gate = block.par.Mode.eval() == "gate"
    hold = max(0.0, block.par.Hold.eval())
    _shots.append({
        "fired": now,
        "seed": _rng.random() * SEED_RANGE,
        "velocity": env.velocity,
        "kind": KINDS[block.par.Motion.eval()],
        "ends": float("inf") if gate else now + hold + release,
        "pour": 0.0 if gate else max(SHORTEST_POUR, hold),
        "gate": gate,
        "age": FIRST_AGE,
        "level": 1.0,
    })
    del _shots[:-MAX_SHOTS]


def _advance(block, env, now):
    release = max(0.0, block.par.Release.eval())
    curve = max(0.01, block.par.Curve.eval())
    newest = _shots[-1]
    for shot in _shots:
        shot["age"] = now - shot["fired"] + FIRST_AGE
        if shot is newest:
            shot["level"] = env.level
            if shot["gate"] and env.stage in ("attack", "hold"):
                shot["pour"] = shot["age"] + GATE_POUR_LEAD
        else:
            left = shot["ends"] - now
            if left <= 0.0:
                shot["level"] = 0.0
            elif release <= 0.0:
                shot["level"] = 1.0
            else:
                shot["level"] = min(1.0, left / release) ** curve
    _shots[:] = [shot for shot in _shots if shot is newest or shot["level"] > 0.0]


def _write_rest(node):
    _write(node, 0, 0.0, 0.0, 0.0, 0.0)
    _write(node, 1, 0.0, 0.0, 0.0, 0.0)
    _write(node, 2, 0.0, 0.0, 0.0, 0.0)
    _write(node, 3, 1.0, 0.0, 0.0, 0.0)
    _write_shots(node)


def apply(block, env):
    switch = op("switch")
    node = op("confetti")
    if env.stage == "idle":
        switch.par.index = 0
        reset(block)
        _write_rest(node)
        return

    switch.par.index = 1
    now = absTime.seconds
    if env.started or not _shots:
        _fire(block, env, now)
    _advance(block, env, now)

    par = block.par
    count = COUNT_LOW + COUNT_RANGE * _clamped(par.Count)
    radius = RADIUS_LOW + RADIUS_RANGE * _clamped(par.Size)
    _write(node, 0, count, radius, _clamped(par.Ink), _clamped(par.Shadow))
    spread = _clamped(par.Spread)
    _write(
        node,
        1,
        SPEED_SCALE * _clamped(par.Power),
        CONE_LOW + CONE_RANGE * spread,
        GRAVITY_SCALE * _clamped(par.Gravity),
        DRAG_LOW + DRAG_RANGE * _clamped(par.Drag),
    )
    wind = max(-1.0, min(1.0, par.Wind.eval())) * WIND_SCALE
    _write(node, 2, _clamped(par.Flutter), SPIN_SCALE * _clamped(par.Spin), wind, _clamped(par.Origin))
    cannons = max(1, min(5, int(round(par.Cannons.eval()))))
    _write(node, 3, float(cannons), _clamped(par.Mix), BAND_SCALE * spread, PALETTE_INDEX.get(par.Palette.eval(), -1) + 1)
    _write_shots(node)
'''

UNIFORMS = ["uLook", "uFlight", "uAir", "uRig", "uKind", "uPour", "uShot0", "uShot1", "uShot2", "uShot3"]


def build(library):
    block = new_block(library, "confetti_cannon")
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 1.0, "release": 1.0, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Confetti Cannon")
    add_menu(
        page,
        "Motion",
        "Motion",
        ["burst", "rain", "corners", "blast"],
        ["Burst Up From Below", "Rain From Top", "Corner Cannons", "Center Blast"],
        "burst",
    )
    add_float(page, "Cannons", "Cannons (Burst)", 3.0, 1.0, 5.0, True, True)
    add_float(page, "Origin", "Origin Across Wall", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Power", "Power", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Spread", "Spread", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Gravity", "Gravity", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Drag", "Drag", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Wind", "Wind", 0.0, -1.0, 1.0, True, True)
    add_float(page, "Flutter", "Flutter", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Spin", "Spin", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Count", "Count", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Size", "Size", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Ink", "Ink", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Shadow", "Shadow", 0.5, 0.0, 1.0, True, True)
    add_palette_menu(page, presets={"brand": "Brand"}, default="brand")
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    shader = palette_shader(SHADER, SWATCHES=glsl_swatches("BRAND", (*BRAND_COLORS, "white")))
    add_text(block, "confetti_shader", shader, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    confetti = add_top(block, glslTOP, "confetti", -300)
    confetti.par.pixeldat = "confetti_shader"
    confetti.par.outputresolution = "useinput"
    confetti.par.format = "useinput"
    confetti.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    confetti.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(confetti.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(confetti)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(confetti)
    switch.outputConnectors[0].connect(output)

    effect = palette_effect(EFFECT_SOURCE, PALETTE_INDEX=PALETTE_INDEX)
    add_text(block, "effect", effect, -500, -300)
    add_callbacks(block)
    return block
