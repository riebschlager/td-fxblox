# Bulb Grid: hold, a grid of flat printed bulbs runs marquee patterns over the video, then burns out bulb by bulb

SHADER = r'''
uniform vec4 uGrid;     // columns, rows, bulb diameter (of a cell), cover (1 is every bulb)
uniform vec4 uPattern;  // pattern, step, period in cells, density
uniform vec4 uAim;      // center x, center y, axis (1 is down), alternate
uniform vec4 uStyle;    // ink, shadow distance (both of a cell), mix
uniform vec4 uColor;    // single tone (-1 is candy), candy shift

layout(location = 0) out vec4 fragColor;

const vec3 BLACK = vec3(0.0);
const vec3 INKS[6] = vec3[6](
    vec3(0.9529, 0.8667, 0.2431),   // yellow
    vec3(0.1451, 0.4510, 1.0),      // blue
    vec3(1.0, 0.5529, 1.0),         // pink
    vec3(1.0, 0.2588, 0.3020),      // red
    vec3(0.2667, 0.9725, 1.0),      // cyan
    vec3(1.0)                       // white
);
const float MAX_INK = 0.6;          // of a bulb's radius, so a bulb always keeps its color
const float MIN_SPARKLE = 0.1;      // share of bulbs lit at the bottom of the Density knob
const float SPARKLE_RANGE = 0.7;
const float SWEEP_SHARE = 0.5;      // of the wall, the widest a sweep bar gets
const float SEED_WRAP = 1024.0;     // keeps the sparkle hash in its accurate range

float hash12(vec2 p)
{
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

bool every(float place, float period)
{
    return mod(place, period) < 0.5;
}

bool patternLit(vec2 cell, vec2 centerCell, vec2 grid, vec2 aspect, float cellSize)
{
    int pattern = int(uPattern.x + 0.5);
    float tick = uPattern.y;
    float period = uPattern.z;
    float column = cell.x;
    float row = grid.y - 1.0 - cell.y;  // row 0 is the top of the wall

    bool down = uAim.z > 0.5;
    float along = down ? row : column;
    float side = down ? column : row;
    float span = down ? grid.y : grid.x;
    if (uAim.w > 0.5 && mod(side, 2.0) > 0.5) {
        along = -along;
    }

    if (pattern == 0) {         // chase
        return every(along - tick, period);
    }
    if (pattern == 1) {         // checker
        return every(column + row + tick, 2.0);
    }
    if (pattern == 2) {         // rings: distance measured on the wall stays round
        vec2 center = (cell + 0.5) / grid - uAim.xy;
        return every(floor(length(center * aspect) / cellSize) - tick, period);
    }
    if (pattern == 3) {         // diamonds
        vec2 away = abs(cell - centerCell);
        return every(away.x + away.y - tick, period);
    }
    if (pattern == 4) {         // sweep: the bar wraps straight around the wall
        float width = max(1.0, ceil(uPattern.w * SWEEP_SHARE * span));
        return mod(along - tick, span) < width;
    }
    if (pattern == 5) {         // diagonal wave
        return every(column + row - tick, period);
    }
    // sparkle: a new draw on every step
    vec2 seed = cell + 1.5 + mod(tick, SEED_WRAP) * vec2(7.0, 13.0);
    return hash12(seed) < MIN_SPARKLE + SPARKLE_RANGE * uPattern.w;
}

// Signed distance in wall heights, positive inside a lit bulb.
float bulbField(vec2 uv, vec2 grid, vec2 aspect, float cellSize, out vec2 cell)
{
    cell = clamp(floor(uv * grid), vec2(0.0), grid - 1.0);
    vec2 centerCell = clamp(floor(uAim.xy * grid), vec2(0.0), grid - 1.0);
    // Cover draws from its own hash, so the same bulbs go first on every release.
    if (hash12(cell + 1.5) >= uGrid.w || !patternLit(cell, centerCell, grid, aspect, cellSize)) {
        return -1.0;
    }
    vec2 center = (cell + 0.5) / grid;
    return 0.5 * uGrid.z * cellSize - length((uv - center) * aspect);
}

void main()
{
    vec4 info = uTD2DInfos[0].res;
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 aspect = vec2(info.z / info.w, 1.0);
    vec2 grid = max(uGrid.xy, vec2(1.0));
    float pixel = 1.0 / info.w;

    // Bulbs fit the shorter side of a cell, so they stay round on any grid.
    float cellSize = min(aspect.x / grid.x, aspect.y / grid.y);
    float ink = min(uStyle.x, MAX_INK * 0.5 * uGrid.z) * cellSize;
    float drop = uStyle.y * cellSize;

    vec2 cell;
    float inside = bulbField(vUV.st, grid, aspect, cellSize, cell);
    vec2 shadowCell;
    float shadow = bulbField(vUV.st - vec2(drop, -drop) / aspect, grid, aspect, cellSize, shadowCell);

    int tone = int(uColor.x + 0.5);
    if (uColor.x < 0.0) {
        // One across is one tone, one down is two: touching bulbs never match.
        tone = int(mod(cell.x + 2.0 * cell.y + uColor.y, 5.0));
    }

    // One pixel of antialiasing keeps the edges hard without crawling on the LEDs.
    vec3 color = mix(source.rgb, BLACK, drop > 0.0 ? clamp(shadow / pixel + 0.5, 0.0, 1.0) : 0.0);
    color = mix(color, BLACK, clamp(inside / pixel + 0.5, 0.0, 1.0));
    color = mix(color, INKS[tone], clamp((inside - ink) / pixel + 0.5, 0.0, 1.0));

    fragColor = TDOutputSwizzle(vec4(mix(source.rgb, color, uStyle.z), source.a));
}
'''

EFFECT_SOURCE = r'''
import math

PATTERNS = ["chase", "checker", "rings", "diamonds", "sweep", "wave", "sparkle"]
TONES = {"yellow": 0, "blue": 1, "pink": 2, "red": 3, "cyan": 4, "white": 5}
BRAND_COLORS = 5        # the rotating palettes skip white
MAX_STEPS = 16.0        # pattern steps per second at the top of the Speed knob
MIN_PERIOD = 2          # lit one bulb in this many at the top of the Density knob
MAX_PERIOD = 8          # and at the bottom
MIN_DIAMETER = 0.3      # of a cell, at the bottom of the Bulb Size knob
MAX_DIAMETER = 0.95
SOFT_SIZE = 0.55        # share of the bulb size on the softest press
POP = 1.2               # how far bulbs overshoot on a press
POP_SECONDS = 0.08
INK_RANGE = 0.12        # of a cell
SHADOW_RANGE = 0.2      # of a cell
STEP_WRAP = 720720      # a multiple of every period, so the wrap can't jump
MAX_DT = 0.1

_travel = 0.0
_presses = 0
_fired = None
_last_seconds = None


def reset(block):
    global _travel, _presses, _fired, _last_seconds
    _travel = 0.0
    _presses = 0
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
    _write(node, 0, 26.0, 8.0, 0.0, 0.0)
    _write(node, 1, 0.0, 0.0, 2.0, 0.0)
    _write(node, 2, 0.5, 0.5, 0.0, 0.0)
    _write(node, 3, 0.0, 0.0, 0.0, 0.0)
    _write(node, 4, 0.0, 0.0, 0.0, 0.0)


def _pattern(block):
    index = PATTERNS.index(block.par.Pattern.eval())
    if block.par.Cycle.eval() == "press":
        index = (index + _presses - 1) % len(PATTERNS)
    return float(index)


def _color(block):
    palette = block.par.Palette.eval()
    turn = (_presses - 1) % BRAND_COLORS
    if palette == "candy":
        return -1.0, float(turn)
    if palette == "press":
        return float(turn), 0.0
    return float(TONES[palette]), 0.0


def _diameter(block, env, age):
    size = MIN_DIAMETER + (MAX_DIAMETER - MIN_DIAMETER) * _clamped(block.par.Bulbsize)
    size *= SOFT_SIZE + (1.0 - SOFT_SIZE) * env.velocity
    if age < POP_SECONDS:
        size *= POP + (1.0 - POP) * age / POP_SECONDS
    return min(1.0, size)


def apply(block, env):
    global _travel, _presses, _fired, _last_seconds
    switch = op("switch")
    bulbs = op("bulbs")
    if env.stage == "idle":
        switch.par.index = 0
        _fired = None
        _last_seconds = None
        _write_rest(bulbs)
        return

    switch.par.index = 1
    now = absTime.seconds
    if env.started or _fired is None:
        _fired = now
    if env.started:
        _presses += 1
    dt = 0.0 if _last_seconds is None else max(0.0, min(MAX_DT, now - _last_seconds))
    _last_seconds = now

    # The run carries through the release, and the next press picks up here.
    _travel = (_travel + _clamped(block.par.Speed) * MAX_STEPS * dt) % STEP_WRAP
    sign = -1 if block.par.Direction.eval() == "reverse" else 1
    tick = (sign * (math.floor(_travel) + _presses)) % STEP_WRAP

    density = _clamped(block.par.Density)
    period = round(MAX_PERIOD + (MIN_PERIOD - MAX_PERIOD) * density)
    columns = max(1, int(round(block.par.Columns.eval())))
    rows = max(1, int(round(block.par.Rows.eval())))
    tone, shift = _color(block)

    _write(bulbs, 0, float(columns), float(rows), _diameter(block, env, now - _fired), max(0.0, min(1.0, env.level)))
    _write(bulbs, 1, _pattern(block), float(tick), float(period), density)
    _write(
        bulbs, 2,
        _clamped(block.par.Centerx), _clamped(block.par.Centery),
        1.0 if block.par.Axis.eval() == "down" else 0.0,
        1.0 if block.par.Alternate.eval() == "on" else 0.0,
    )
    _write(bulbs, 3, _clamped(block.par.Ink) * INK_RANGE, _clamped(block.par.Shadow) * SHADOW_RANGE, _clamped(block.par.Mix))
    _write(bulbs, 4, tone, shift)
'''

UNIFORMS = ["uGrid", "uPattern", "uAim", "uStyle", "uColor"]


def build(library):
    block = new_block(library, "bulb_grid")
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.25, "release": 0.25, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Bulb Grid")
    add_menu(
        page, "Pattern", "Pattern",
        ["chase", "checker", "rings", "diamonds", "sweep", "wave", "sparkle"],
        ["Marquee Chase", "Checker Flip", "Rings", "Diamonds", "Sweep", "Diagonal Wave", "Sparkle"],
        "chase",
    )
    add_menu(page, "Cycle", "Change Pattern", ["off", "press"], ["Off", "Every Press"], "off")
    add_float(page, "Columns", "Columns", 26.0, 1.0, 120.0, True, True)
    add_float(page, "Rows", "Rows", 8.0, 1.0, 40.0, True, True)
    add_float(page, "Bulbsize", "Bulb Size", 0.7, 0.0, 1.0, True, True)
    add_float(page, "Speed", "Speed", 0.35, 0.0, 1.0, True, True)
    add_float(page, "Density", "Density", 0.75, 0.0, 1.0, True, True)
    add_menu(page, "Direction", "Direction", ["forward", "reverse"], ["Forward", "Reverse"], "forward")
    add_menu(page, "Axis", "Chase and Sweep Axis", ["across", "down"], ["Across", "Down"], "across")
    add_menu(page, "Alternate", "Alternate Lines", ["off", "on"], ["Off", "On"], "off")
    add_float(page, "Centerx", "Center X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Centery", "Center Y", 0.5, 0.0, 1.0, True, True)
    add_menu(
        page, "Palette", "Palette",
        ["yellow", "blue", "pink", "red", "cyan", "white", "candy", "press"],
        ["Yellow", "Blue", "Pink", "Red", "Cyan", "White", "Candy", "New Color Every Press"],
        "yellow",
    )
    add_float(page, "Ink", "Ink Line", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Shadow", "Hard Shadow", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "bulbs_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    bulbs = add_top(block, glslTOP, "bulbs", -300)
    bulbs.par.pixeldat = "bulbs_shader"
    bulbs.par.outputresolution = "useinput"
    bulbs.par.format = "useinput"
    bulbs.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    bulbs.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(bulbs.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(bulbs)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(bulbs)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
