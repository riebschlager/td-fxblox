# Window Shades: hold, the wall becomes a framed grid of windows and flat brand-color shades roll down
#
# One pass. Every window works out its own place in the cascade, its color, how far its shade has
# dropped, the black rail on the shade's edge, and the hard shadow the shade throws onto the video
# behind it. Entry runs on its own clock from the press; the exit runs over the Release time, so
# the block is back to the picture exactly when the envelope ends.

SHADER = r'''
uniform vec4 uGrid;    // columns, rows, seed, color count
uniform vec4 uTime;    // seconds since the press, exit progress (-1 while held), drop seconds, cascade seconds
uniform vec4 uShape;   // pattern, direction, exit, fill
uniform vec4 uLook;    // frame (of a window's short side), shadow (of its short side), bounce, coverage
uniform vec4 uMore;    // frame scale, color layout, exit window share, mix
uniform vec4 uColor0;
uniform vec4 uColor1;
uniform vec4 uColor2;
uniform vec4 uColor3;
uniform vec4 uColor4;

layout(location = 0) out vec4 fragColor;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
const float PI = 3.14159265;
const int MAX_CELLS = 64;        // longest column or row run the color steps walk
const float FALL = 0.6;          // share of a drop spent falling; the rest bounces on the sill
const float BOUNCE_HEIGHT = 0.18;  // of the window, the first rebound at full Bounce
const float RAIL = 0.9;          // the shade's rail, against the frame's width
const float RAIL_MIN = 3.0;      // pixels, so the rail still reads with the frame off
const float DUO_CUT = 0.42;      // video brightness where a duotone window flips to its own color
const float DUO_SOFT = 0.12;
const float DOT_SIZE = 0.03;     // halftone pitch, in wall heights
const float DOT_MAX = 0.42;      // of the pitch: the darkest video still leaves half the color showing
const float DOT_MIN = 0.12;      // of the pitch: even the brightest video keeps a pin dot

ivec2 cells;
float seed;

float hash(vec2 p)
{
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float cellHash(ivec2 c, float k)
{
    return hash(vec2(c) + vec2(k, seed));
}

// Where a window falls in the cascade, 0 first and 1 last. j counts rows from the top.
float order(int i, int j, int pattern)
{
    float cols = float(cells.x);
    float rows = float(cells.y);
    float x = cells.x > 1 ? float(i) / (cols - 1.0) : 0.5;
    float y = cells.y > 1 ? float(j) / (rows - 1.0) : 0.5;
    if (pattern == 0) {          // sweep left to right
        return x;
    } else if (pattern == 1) {   // sweep right to left
        return 1.0 - x;
    } else if (pattern == 2 || pattern == 3) {  // center out, edges in
        vec2 off = vec2(float(i) - 0.5 * (cols - 1.0), float(j) - 0.5 * (rows - 1.0));
        float far = max(length(vec2(0.5 * (cols - 1.0), 0.5 * (rows - 1.0))), 1e-4);
        float out_ = length(off) / far;
        return pattern == 2 ? out_ : 1.0 - out_;
    } else if (pattern == 4) {   // diagonal from the top left
        return float(i + j) / max(1.0, cols + rows - 2.0);
    } else if (pattern == 5) {   // checker: every other window, then the rest
        return 0.5 * float((i + j) % 2) + 0.5 * x;
    } else if (pattern == 6) {   // rows, back and forth like a typewriter
        int along = (j % 2 == 0) ? i : cells.x - 1 - i;
        return float(j * cells.x + along) / max(1.0, cols * rows - 1.0);
    } else if (pattern == 7) {   // rain: each column at its own pace
        return 0.6 * cellHash(ivec2(i, 0), 3.1) + 0.4 * y;
    } else if (pattern == 8) {   // spiral, from the outside in
        int r = min(min(i, j), min(cells.x - 1 - i, cells.y - 1 - j));
        int w = cells.x - 2 * r;
        int h = cells.y - 2 * r;
        int before = cells.x * cells.y - w * h;
        int x0 = i - r;
        int y0 = j - r;
        int pos;
        if (y0 == 0) {
            pos = x0;
        } else if (x0 == w - 1) {
            pos = (w - 1) + y0;
        } else if (y0 == h - 1) {
            pos = (w - 1) + (h - 1) + (w - 1 - x0);
        } else {
            pos = 2 * (w - 1) + (h - 1) + (h - 1 - y0);
        }
        return float(before + pos) / max(1.0, cols * rows - 1.0);
    }
    return cellHash(ivec2(i, j), 7.7);  // random
}

// Steps of 1..n-1 between neighbors, so two windows side by side never share a color.
int steps(int upTo, float k, int n)
{
    int total = 0;
    for (int s = 1; s < MAX_CELLS; s++) {
        if (s > upTo) {
            break;
        }
        total += 1 + int(floor(cellHash(ivec2(s, 0), k) * float(n - 1)));
    }
    return total;
}

int colorIndex(int i, int j, int layout_, int n)
{
    if (n <= 1) {
        return 0;
    }
    if (layout_ == 1) {          // columns
        return steps(i, 11.3, n) % n;
    } else if (layout_ == 2) {   // rows
        return steps(j, 13.9, n) % n;
    } else if (layout_ == 3) {   // diagonal bands
        return (i + j) % n;
    } else if (layout_ == 4) {   // checker
        return (i + j) % 2;
    }
    return (steps(i, 11.3, n) + steps(j, 13.9, n)) % n;  // random, no matching neighbors
}

vec3 colorAt(int index)
{
    if (index == 0) return uColor0.rgb;
    if (index == 1) return uColor1.rgb;
    if (index == 2) return uColor2.rgb;
    if (index == 3) return uColor3.rgb;
    return uColor4.rgb;
}

// How far down its window a shade has reached, with a little bounce on the sill.
float arrive(float u, float bounce)
{
    if (u <= 0.0) {
        return 0.0;
    }
    if (u >= 1.0) {
        return 1.0;
    }
    if (u < FALL) {
        float x = u / FALL;
        return x * x;
    }
    float v = (u - FALL) / (1.0 - FALL);
    return 1.0 - bounce * BOUNCE_HEIGHT * abs(sin(2.0 * PI * v)) * (1.0 - v) * (1.0 - v);
}

// Distance into the window from the side its shade comes from, 0 to 1.
float depth(vec2 local, int direction, int i, int j)
{
    if (direction == 5) {  // alternate: down and across, woven
        direction = ((i + j) % 2 == 0) ? 0 : 2;
    }
    if (direction == 0) return local.y;            // from the top
    if (direction == 1) return 1.0 - local.y;      // from the bottom
    if (direction == 2) return local.x;            // from the left
    if (direction == 3) return 1.0 - local.x;      // from the right
    return 1.0 - abs(local.x - 0.5) * 2.0;         // curtains, from both sides
}

// Pixels the shade travels across its window, for antialiasing and the rail.
float travel(vec2 cellPx, int direction, int i, int j)
{
    if (direction == 5) {
        direction = ((i + j) % 2 == 0) ? 0 : 2;
    }
    if (direction <= 1) return cellPx.y;
    if (direction <= 3) return cellPx.x;
    return 0.5 * cellPx.x;
}

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 res = uTD2DInfos[0].res.zw;
    cells = ivec2(max(vec2(1.0), uGrid.xy));
    seed = uGrid.z;
    int n = int(clamp(uGrid.w, 1.0, 5.0));
    int pattern = int(uShape.x + 0.5);
    int direction = int(uShape.y + 0.5);
    int exitStyle = int(uShape.z + 0.5);
    int fill = int(uShape.w + 0.5);

    vec2 grid = vUV.st * vec2(cells);
    ivec2 cell = ivec2(min(floor(grid), vec2(cells) - 1.0));
    int i = cell.x;
    int j = cells.y - 1 - cell.y;  // rows counted from the top
    vec2 cellPx = res / vec2(cells);
    float shortSide = min(cellPx.x, cellPx.y);
    vec2 px = vUV.st * res;

    // How far along its own drop this window is.
    float place = order(i, j, pattern);
    float u = (uTime.x - place * uTime.w) / max(uTime.z, 1e-4);
    float bottom = arrive(u, uLook.z);
    float top = 0.0;
    if (uTime.y >= 0.0) {
        float window = uMore.z;
        float leave = exitStyle == 1 ? 1.0 - place : place;
        float v = clamp((uTime.y - leave * (1.0 - window)) / window, 0.0, 1.0);
        if (exitStyle == 1) {
            bottom = min(bottom, 1.0 - v * v * (3.0 - 2.0 * v));  // roll back up
        } else {
            top = v * v;  // drop through and fall out the bottom
        }
    }
    if (cellHash(cell, 1.9) >= uLook.w) {
        bottom = 0.0;  // this window stays open
    }

    float run = travel(cellPx, direction, i, j);
    float aa = 1.0 / run;
    float framePx = uLook.x * shortSide * uMore.x;
    float rail = max(RAIL * framePx, RAIL_MIN * step(1e-4, uLook.w)) / run;

    vec2 local = fract(grid);
    local.y = 1.0 - local.y;  // from the top of the window
    float d = depth(local, direction, i, j);
    float covered = smoothstep(top - aa, top + aa, d) * (1.0 - smoothstep(bottom - aa, bottom + aa, d));
    covered *= step(1e-4, bottom - top);
    float onRail = covered * max(
        smoothstep(bottom - rail - aa, bottom - rail + aa, d),
        step(1e-4, top) * (1.0 - smoothstep(top + rail - aa, top + rail + aa, d)));

    // The shade's hard shadow falls down and right onto the video, inside the same window.
    vec2 shift = vec2(1.0, -1.0) * uLook.y * shortSide / cellPx;
    vec2 behind = fract(grid) - shift;
    float shadow = 0.0;
    if (all(greaterThanEqual(behind, vec2(0.0))) && all(lessThanEqual(behind, vec2(1.0))) && uLook.y > 0.0) {
        vec2 bl = vec2(behind.x, 1.0 - behind.y);
        float sd = depth(bl, direction, i, j);
        shadow = smoothstep(top - aa, top + aa, sd) * (1.0 - smoothstep(bottom - aa, bottom + aa, sd));
        shadow *= step(1e-4, bottom - top);
    }

    int index = colorIndex(i, j, int(uMore.y + 0.5), n);
    vec3 color = colorAt(index);
    vec3 shade = color;
    if (fill == 1) {         // duotone: the video printed in two flat brand colors, never dimmed
        vec3 partner = n > 1 ? colorAt((index + 1) % n) : vec3(0.0);
        float luma = dot(source.rgb, LUMA);
        shade = mix(partner, color, smoothstep(DUO_CUT - DUO_SOFT, DUO_CUT + DUO_SOFT, luma));
    } else if (fill == 2) {  // halftone: black dots on the shade, sized by the video's darkness
        float pitch = DOT_SIZE * res.y;
        mat2 turn = mat2(0.7071, -0.7071, 0.7071, 0.7071);
        vec2 q = turn * px / pitch;
        vec2 center = (floor(q) + 0.5);
        vec2 at = (transpose(turn) * (center * pitch)) / res;
        float luma = dot(texture(sTD2DInputs[0], clamp(at, 0.0, 1.0)).rgb, LUMA);
        // Auto-leveled, so dark clips still print as dots on color, not a black window.
        float dark = 1.0 - smoothstep(0.05, 0.7, luma);
        float radius = mix(DOT_MIN, DOT_MAX, sqrt(dark));
        float dist = length(q - center);
        float dotInk = 1.0 - smoothstep(radius - 1.0 / pitch, radius + 1.0 / pitch, dist);
        vec3 ink = dot(color, LUMA) < 0.2 ? vec3(1.0) : vec3(0.0);
        shade = mix(color, ink, dotInk);
    }

    vec3 result = source.rgb * (1.0 - max(shadow - covered, 0.0));
    result = mix(result, shade, covered);
    result = mix(result, vec3(0.0), onRail);

    // The window frames: black mullions around every window, outer edges included.
    vec2 edge = min(local, 1.0 - local) * cellPx;
    float toFrame = min(edge.x, edge.y);
    float inFrame = 1.0 - smoothstep(0.5 * framePx - 0.75, 0.5 * framePx + 0.75, toFrame);
    result = mix(result, vec3(0.0), inFrame * step(0.5, framePx));

    fragColor = TDOutputSwizzle(mix(source, vec4(result, source.a), uMore.w));
}
'''

EFFECT_SOURCE = r'''
import random

COLUMNS_MIN = 2
COLUMNS_RANGE = 46
COLUMNS_CURVE = 1.5     # more of the knob for the counts that matter
ROWS_MIN = 1
ROWS_RANGE = 23
DROP_MIN = 0.08         # seconds a shade takes to fall, at Drop Time 0
DROP_RANGE = 0.6
SPREAD_MAX = 1.5        # seconds the cascade takes at full Spread
FRAME_MAX = 0.12        # of a window's short side
SHADOW_MAX = 0.15
SOFT_COVER = 0.35       # a soft hit closes this share of the windows a hard hit closes
SOFT_BOUNCE = 0.3
EXIT_WINDOW = 0.4       # share of the exit each window takes to clear
FRAME_LEAVE = 0.6       # share of the exit before the frames start to thin

BLUE = (0.1451, 0.4510, 1.0)
PINK = (1.0, 0.5529, 1.0)
YELLOW = (0.9529, 0.8667, 0.2431)
RED = (1.0, 0.2588, 0.3020)
CYAN = (0.2667, 0.9725, 1.0)
WHITE = (1.0, 1.0, 1.0)
BLACK = (0.0, 0.0, 0.0)
COLOR_SETS = {
    "full": [BLUE, PINK, YELLOW, RED, CYAN],
    "hot": [PINK, RED, YELLOW],
    "cool": [BLUE, CYAN, WHITE],
    "primary": [BLUE, YELLOW, RED],
    "pinkyellow": [PINK, YELLOW],
    "bluewhite": [BLUE, WHITE],
    "ink": [BLACK, WHITE],
}
PATTERNS = ["sweepright", "sweepleft", "centerout", "edgesin", "diagonal", "checker", "rows", "rain", "spiral", "random"]
DIRECTIONS = ["down", "up", "right", "left", "curtains", "alternate"]
EXITS = ["drop", "rollup", "cut"]
FILLS = ["flat", "duotone", "halftone"]
LAYOUTS = ["random", "columns", "rows", "diagonal", "checker"]

_rng = random.Random()
_hit = None
_last_pattern = None


def reset(block):
    global _hit, _last_pattern
    _hit = None
    _last_pattern = None


def _pattern(block):
    # Shuffle picks a different pattern from the last press, so two hits never look the same.
    global _last_pattern
    choice = block.par.Pattern.eval()
    if choice == "shuffle":
        choice = _rng.choice([name for name in PATTERNS if name != _last_pattern])
    _last_pattern = choice
    return PATTERNS.index(choice)


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


def _write_rest(shades):
    _write(shades, 0, 1.0, 1.0, 0.0, 1.0)
    _write(shades, 1, 0.0, -1.0, 1.0, 0.0)
    _write(shades, 2, 0.0, 0.0, 0.0, 0.0)
    _write(shades, 3, 0.0, 0.0, 0.0, 0.0)
    _write(shades, 4, 0.0, 0.0, EXIT_WINDOW, 0.0)
    for index in range(5, 10):
        _write(shades, index, 0.0, 0.0, 0.0, 0.0)


def _aspect():
    source = op("in1")
    return max(0.01, float(source.width) / max(1.0, float(source.height)))


def grid_for(block):
    columns = COLUMNS_MIN + int(round(COLUMNS_RANGE * _clamped(block.par.Columns) ** COLUMNS_CURVE))
    if block.par.Cells.eval() == "square":
        rows = max(1, int(round(columns / _aspect())))
    else:
        rows = ROWS_MIN + int(round(ROWS_RANGE * _clamped(block.par.Rows)))
    return columns, rows


def _roll(block, velocity):
    # The grid, order and colors hold for the whole press, because changing them mid-air would jump.
    colors = list(COLOR_SETS[block.par.Colorset.eval()])
    turn = _rng.randrange(len(colors))
    colors = colors[turn:] + colors[:turn]
    columns, rows = grid_for(block)
    return {
        "pressed": absTime.seconds,
        "columns": columns,
        "rows": rows,
        "seed": _rng.random() * 100.0,
        "colors": colors,
        "pattern": _pattern(block),
        "direction": DIRECTIONS.index(block.par.Direction.eval()),
        "layout": LAYOUTS.index(block.par.Colorlayout.eval()),
        "velocity": velocity,
    }


def apply(block, env):
    global _hit
    switch = op("switch")
    shades = op("shades")
    exit_style = block.par.Exit.eval()
    if env.stage == "idle" or (env.stage == "release" and exit_style == "cut"):
        switch.par.index = 0
        _hit = None
        _write_rest(shades)
        return

    switch.par.index = 1
    if env.started or _hit is None:
        _hit = _roll(block, env.velocity)

    held = max(0.0, absTime.seconds - _hit["pressed"])
    drop = DROP_MIN + DROP_RANGE * _clamped(block.par.Droptime)
    spread = SPREAD_MAX * _clamped(block.par.Spread)
    leaving = env.progress if env.stage == "release" else -1.0
    frame_scale = _smooth(held / drop)
    if leaving >= 0.0:
        frame_scale *= 1.0 - _smooth((leaving - FRAME_LEAVE) / (1.0 - FRAME_LEAVE))

    velocity = _hit["velocity"]
    coverage = _clamped(block.par.Coverage) * (SOFT_COVER + (1.0 - SOFT_COVER) * velocity)
    bounce = _clamped(block.par.Bounce) * (SOFT_BOUNCE + (1.0 - SOFT_BOUNCE) * velocity)
    colors = _hit["colors"]

    _write(shades, 0, _hit["columns"], _hit["rows"], _hit["seed"], len(colors))
    _write(shades, 1, held, leaving, drop, spread)
    _write(shades, 2, _hit["pattern"], _hit["direction"], EXITS.index(exit_style), FILLS.index(block.par.Fill.eval()))
    _write(shades, 3, _clamped(block.par.Frame) * FRAME_MAX, _clamped(block.par.Shadow) * SHADOW_MAX, bounce, coverage)
    _write(shades, 4, frame_scale, _hit["layout"], EXIT_WINDOW, _clamped(block.par.Mix))
    for index in range(5):
        red, green, blue = colors[index % len(colors)]
        _write(shades, 5 + index, red, green, blue, 0.0)
'''

UNIFORMS = ["uGrid", "uTime", "uShape", "uLook", "uMore", "uColor0", "uColor1", "uColor2", "uColor3", "uColor4"]


def build(library):
    block = new_block(library, "window_shades")
    # Held: shades drop on the press and stay down. Hit mode holds them for Hold, then exits.
    # Release is how long the exit takes. Curve isn't used.
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.6, "release": 0.7, "curve": 1.0, "velocitydepth": 1.0})
    page = block.appendCustomPage("Window Shades")
    add_menu(
        page,
        "Pattern",
        "Pattern",
        ["sweepright", "sweepleft", "centerout", "edgesin", "diagonal", "checker", "rows", "rain", "spiral", "random", "shuffle"],
        ["Sweep Right", "Sweep Left", "Center Out", "Edges In", "Diagonal", "Checker", "Rows", "Rain", "Spiral", "Random", "Shuffle (new each press)"],
        "sweepright",
    )
    add_menu(
        page,
        "Direction",
        "Shade Direction",
        ["down", "up", "right", "left", "curtains", "alternate"],
        ["Down", "Up", "From Left", "From Right", "Curtains", "Alternate"],
        "down",
    )
    add_menu(page, "Exit", "Exit", ["drop", "rollup", "cut"], ["Drop Through", "Roll Up", "Hard Cut"], "drop")
    add_menu(
        page,
        "Colorset",
        "Color Set",
        ["full", "hot", "cool", "primary", "pinkyellow", "bluewhite", "ink"],
        ["Full Brand", "Hot", "Cool", "Primary", "Pink + Yellow", "Blue + White", "Ink"],
        "full",
    )
    add_menu(
        page,
        "Colorlayout",
        "Color Layout",
        ["random", "columns", "rows", "diagonal", "checker"],
        ["Random", "Column Stripes", "Row Stripes", "Diagonal", "Checker"],
        "random",
    )
    add_menu(page, "Fill", "Fill", ["flat", "duotone", "halftone"], ["Flat", "Duotone", "Halftone"], "flat")
    add_menu(page, "Cells", "Cells", ["square", "free"], ["Square", "Free Rows"], "square")
    add_float(page, "Columns", "Columns", 0.45, 0.0, 1.0, True, True)
    add_float(page, "Rows", "Rows (Free)", 0.2, 0.0, 1.0, True, True)
    add_float(page, "Spread", "Spread", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Droptime", "Drop Time", 0.3, 0.0, 1.0, True, True)
    add_float(page, "Coverage", "Coverage", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Bounce", "Bounce", 0.6, 0.0, 1.0, True, True)
    add_float(page, "Frame", "Frame", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Shadow", "Shadow", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "shades_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    shades = add_top(block, glslTOP, "shades", -300)
    shades.par.pixeldat = "shades_shader"
    shades.par.outputresolution = "useinput"
    shades.par.format = "useinput"
    shades.par.inputfiltertype = "linear"
    # Vectors is a sequence: entries past the first don't exist until it grows.
    shades.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(shades.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    source.outputConnectors[0].connect(shades)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(shades)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
