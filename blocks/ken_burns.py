# Ken Burns: hold to show one photo over the scene, drifting slowly from one framing to another
#
# Drag any TOP onto Photo. The photo sits in a fixed frame over the scene, fitted whole inside
# the wall and scaled by Size, with nothing behind it, so the scene shows around the frame.
# Inside the frame the photo moves from the Start framing to the End framing over Duration
# seconds on its own clock. Each framing is a focus point (the spot the view centers on) and a
# zoom (1 is the whole photo). The view is kept inside the photo, so no edge ever shows.
#
# The press brings the photo in over Attack, and letting go takes it away over Release. As in
# Transition, the entrance and exit each have their own style and easing, and a new half
# starts from wherever the photo is, so nothing jumps. The move keeps running through the
# exit, so the photo never freezes as it leaves. One block shows one photo. Use one block per
# photo.

SHADER = r'''
uniform vec4 uFrame;   // frame center x, center y (shares of the wall), size (1 fits the wall), unused
uniform vec4 uShift;   // frame offset x, y (in frame widths and heights), unused x2
uniform vec4 uView;    // view center x, y (shares of the photo), zoom (1 whole photo), opacity
uniform vec4 uCover;   // cover shape (0 none, 1 wipe, 2 iris), hidden amount, direction x, y

layout(location = 0) out vec4 fragColor;

const float SOFT = 0.04;  // wipe and iris edge, as a share of the frame

// 0 to 1 across the frame along d.
float along(vec2 p, vec2 d)
{
    float span = 0.5 * (abs(d.x) + abs(d.y));
    return clamp(dot(p - 0.5, d) / max(span, 1e-4) * 0.5 + 0.5, 0.0, 1.0);
}

// How hidden a point is, given its place c in the order (low hides first).
float covered(float c, float h)
{
    float x = clamp((h * (1.0 + SOFT) - c) / SOFT, 0.0, 1.0);
    return x * x * (3.0 - 2.0 * x);
}

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 wall = uTD2DInfos[0].res.zw;
    vec2 photo = max(uTD2DInfos[1].res.zw, vec2(1.0));

    // The whole photo fitted inside the wall, then scaled by size.
    float fit = min(wall.x / photo.x, wall.y / photo.y) * uFrame.z;
    vec2 framePx = max(photo * fit, vec2(1e-3));
    vec2 centerPx = uFrame.xy * wall + uShift.xy * framePx;
    vec2 local = (vUV.st * wall - centerPx) / framePx + 0.5;

    // Distance to the frame edge in pixels, for a one-pixel antialiased edge.
    vec2 edge = min(local, 1.0 - local) * framePx;
    float inside = clamp(min(edge.x, edge.y) + 0.5, 0.0, 1.0);

    vec2 uv = uView.xy + (local - 0.5) / max(uView.z, 1e-3);
    vec4 picture = texture(sTD2DInputs[1], clamp(uv, 0.0, 1.0));

    float shown = 1.0;
    int shape = int(uCover.x + 0.5);
    if (shape == 1) {            // wipe: an edge sweeps along the direction
        shown = 1.0 - covered(along(local, uCover.zw), uCover.y);
    } else if (shape == 2) {     // iris: a circle closes on, or opens from, the frame center
        vec2 p = (local - 0.5) * framePx;
        float r = length(p) / max(0.5 * length(framePx), 1e-3);
        shown = 1.0 - covered(1.0 - r, uCover.y);
    }

    // Photo over the scene, premultiplied.
    float k = clamp(uView.w, 0.0, 1.0) * inside * shown;
    fragColor = TDOutputSwizzle(picture * k + source * (1.0 - picture.a * k));
}
'''

EFFECT_SOURCE = r'''
import math

STYLES = ["fade", "push", "scale", "slide", "wipe", "iris"]
COVER_SHAPES = {"wipe": 1.0, "iris": 2.0}
DIRECTIONS = {"left": (-1.0, 0.0), "right": (1.0, 0.0), "up": (0.0, 1.0), "down": (0.0, -1.0)}
PUSH_MAX = 1.0          # push at full Amount: the view gets this much tighter (2x) as it goes
SCALE_MAX = 0.5         # scale at full Amount: the frame shrinks by this much as it goes
SLIDE_MAX = 0.5         # slide at full Amount: the frame travels this much of its own size
BACK_MAX = 3.4          # Back easing's overshoot at full Overshoot
BOUNCE_MAX = 0.7        # Bounce easing: how much speed survives each landing at full Overshoot
BOUNCES = 4
ELASTIC_TURNS = 2.5
ELASTIC_DAMP = 6.0

_hidden = 1.0           # 0 fully shown, 1 fully gone
_phase = None
_from = 1.0
_level_from = 0.0
_move_from = None       # absTime.seconds when the move started


def reset(block):
    global _hidden, _phase, _from, _level_from, _move_from
    _hidden = 1.0
    _phase = None
    _from = 1.0
    _level_from = 0.0
    _move_from = None


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _bounce(t, e):
    if e <= 1e-4:
        return t * t
    total = 1.0 + sum(2.0 * e ** k for k in range(1, BOUNCES + 1))
    s = t * total
    if s < 1.0:
        return s * s
    s -= 1.0
    for k in range(1, BOUNCES + 1):
        span = 2.0 * e ** k
        if s < span:
            x = s - span * 0.5
            return 1.0 - (e ** (2 * k) - x * x)
        s -= span
    return 1.0


def ease(name, t, overshoot):
    """0 at t 0 and exactly 1 at t 1. Back and Elastic run past 1, Bounce comes back short of it."""
    t = max(0.0, min(1.0, t))
    if t >= 1.0:
        return 1.0
    out = 1.0 - (1.0 - t) ** 3
    if name == "linear":
        return t
    if name == "easein":
        return t ** 3
    if name == "easeout":
        return out
    if name == "easeinout":
        return 4.0 * t ** 3 if t < 0.5 else 1.0 - (2.0 - 2.0 * t) ** 3 * 0.5
    if name == "back":
        s = BACK_MAX * overshoot
        u = t - 1.0
        return 1.0 + (s + 1.0) * u ** 3 + s * u * u
    if name == "bounce":
        return _bounce(t, BOUNCE_MAX * overshoot)
    if name == "elastic":
        wobble = 1.0 - (1.0 - t) * math.exp(-ELASTIC_DAMP * t) * math.cos(math.tau * ELASTIC_TURNS * t)
        return out + (wobble - out) * overshoot
    return t


def view(block, e, push=1.0):
    """Center and zoom of the view, e of the way from Start to End, kept inside the photo."""
    z0 = max(1.0, block.par.Startzoom.eval())
    z1 = max(1.0, block.par.Endzoom.eval())
    # Zoom moves evenly in log space, so the push feels like a steady speed.
    zoom = max(1.0, math.exp(math.log(z0) + (math.log(z1) - math.log(z0)) * e) * push)
    half = 0.5 / zoom
    x = _clamped(block.par.Startx) + (_clamped(block.par.Endx) - _clamped(block.par.Startx)) * e
    y = _clamped(block.par.Starty) + (_clamped(block.par.Endy) - _clamped(block.par.Starty)) * e
    return min(max(x, half), 1.0 - half), min(max(y, half), 1.0 - half), zoom


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def _write_rest(node):
    for index in range(4):
        _write(node, index, 0.0, 0.0, 0.0, 0.0)


def _start(phase, level):
    # A new half starts from wherever the photo is now, so nothing jumps.
    global _phase, _from, _level_from
    _phase = phase
    _from = _hidden
    _level_from = level


def apply(block, env):
    global _hidden, _move_from
    switch = op("switch")
    node = op("kenburns")
    if env.stage == "idle" or block.par.Photo.eval() is None:
        switch.par.index = 0
        _write_rest(node)
        reset(block)
        return

    # The move runs on its own clock from the first press, through the exit, and isn't
    # restarted by a press while the photo is still up.
    now = absTime.seconds
    if _move_from is None:
        _move_from = now

    phase = "out" if env.stage == "release" else "in"
    if env.started or phase != _phase:
        _start(phase, env.level)

    overshoot = _clamped(block.par.Overshoot)
    if phase == "in":
        # Attack ramps the level linearly from where it was; that ramp is this half's clock.
        if env.stage == "hold" or _level_from >= 1.0:
            t = 1.0
        else:
            t = (env.level - _level_from) / (1.0 - _level_from)
        _hidden = _from * (1.0 - ease(block.par.Ineasing.eval(), t, overshoot))
        style = block.par.Entrance.eval()
        sign = -1.0  # comes in moving toward Direction, so it starts on the far side
    else:
        _hidden = _from + (1.0 - _from) * ease(block.par.Outeasing.eval(), env.progress, overshoot)
        style = block.par.Exit.eval()
        sign = 1.0   # leaves carrying on toward Direction

    h = _hidden
    amount = _clamped(block.par.Amount)
    dx, dy = DIRECTIONS[block.par.Direction.eval()]
    dx, dy = dx * sign, dy * sign

    duration = max(0.1, block.par.Duration.eval())
    e = ease(block.par.Moveeasing.eval(), (now - _move_from) / duration, 0.0)
    push = 1.0 + PUSH_MAX * amount * h if style == "push" else 1.0
    x, y, zoom = view(block, e, push)

    size = _clamped(block.par.Size)
    if style == "scale":
        size *= max(0.01, 1.0 - SCALE_MAX * amount * h)
    shift = SLIDE_MAX * amount * h if style == "slide" else 0.0

    cover = COVER_SHAPES.get(style, 0.0)
    opacity = 1.0 if cover else max(0.0, min(1.0, 1.0 - h))
    opacity *= env.velocity * _clamped(block.par.Mix)

    switch.par.index = 1
    _write(node, 0, _clamped(block.par.Posx), _clamped(block.par.Posy), size, 0.0)
    _write(node, 1, dx * shift, dy * shift, 0.0, 0.0)
    _write(node, 2, x, y, zoom, opacity)
    _write(node, 3, cover, h, dx, dy)
'''

UNIFORMS = ["uFrame", "uShift", "uView", "uCover"]


def build(library):
    block = new_block(library, "ken_burns")
    # Held: the press brings the photo in over Attack and keeps it up, letting go takes it away
    # over Release. Hit mode keeps it up for Hold seconds. Curve isn't used: the two easing menus
    # shape the entrance and exit instead. Max Hold is off, so a long hold never drops the photo.
    add_control_page(block, {"mode": "gate", "attack": 1.0, "hold": 4.0, "release": 1.5, "curve": 1.0, "velocitydepth": 0.0})
    block.par.Maxhold.default = 0.0
    block.par.Maxhold.val = 0.0

    page = block.appendCustomPage("Ken Burns")
    page.appendTOP("Photo", label="Photo")
    add_float(page, "Size", "Size (1 fits the wall)", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Posx", "Position X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Posy", "Position Y (0 bottom)", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Duration", "Move Duration (s)", 12.0, 1.0, 60.0, clamp_low=True)
    add_menu(page, "Moveeasing", "Move Easing", ["linear", "easein", "easeout", "easeinout"],
             ["Linear", "Ease In", "Ease Out", "Ease In-Out"], "easeinout")
    add_float(page, "Startx", "Start Focus X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Starty", "Start Focus Y (0 bottom)", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Startzoom", "Start Zoom", 1.0, 1.0, 4.0, clamp_low=True)
    add_float(page, "Endx", "End Focus X", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Endy", "End Focus Y (0 bottom)", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Endzoom", "End Zoom", 1.4, 1.0, 4.0, clamp_low=True)
    styles = ["fade", "push", "scale", "slide", "wipe", "iris"]
    style_labels = ["Fade", "Push (zoom through + fade)", "Scale (+ fade)", "Slide (+ fade)", "Wipe", "Iris"]
    add_menu(page, "Entrance", "Entrance", styles, style_labels, "fade")
    add_menu(page, "Exit", "Exit", styles, style_labels, "fade")
    add_menu(page, "Direction", "Slide / Wipe Toward", ["left", "right", "up", "down"],
             ["Left", "Right", "Up", "Down"], "right")
    add_float(page, "Amount", "Push / Scale / Slide Amount", 0.5, 0.0, 1.0, True, True)
    easings = ["linear", "easein", "easeout", "easeinout", "back", "bounce", "elastic"]
    easing_labels = ["Linear", "Ease In", "Ease Out", "Ease In-Out", "Back (overshoot)", "Bounce", "Elastic"]
    add_menu(page, "Ineasing", "Entrance Easing", easings, easing_labels, "easeout")
    add_menu(page, "Outeasing", "Exit Easing", easings, easing_labels, "easein")
    add_float(page, "Overshoot", "Overshoot / Bounce", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "kenburns_shader", SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -500)
    photo = block.create(selectTOP, "photo")
    photo.par.top.expr = "parent().par.Photo.eval()"
    photo.nodeX = -500
    photo.nodeY = -120
    node = add_top(block, glslTOP, "kenburns", -300)
    node.par.pixeldat = "kenburns_shader"
    node.par.outputresolution = "useinput"
    node.par.format = "useinput"
    # Mipmapped, so a large photo shrunk onto the wall doesn't shimmer.
    node.par.inputfiltertype = "mipmap"
    node.seq.vec.numBlocks = len(UNIFORMS)
    for index, uniform in enumerate(UNIFORMS):
        setattr(node.par, "vec%dname" % index, uniform)
    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    node.inputConnectors[0].connect(source)
    node.inputConnectors[1].connect(photo)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(node)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
