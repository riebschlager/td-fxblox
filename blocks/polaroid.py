# Polaroid: tail hit, a portrait from a folder flashes onto a side screen, develops, then falls off
#
# This block breaks one repo rule on purpose: it loads its pictures from disk at runtime instead of
# embedding them, so the folder can change without a rebuild. Three Movie File In TOPs take turns.
# One shows the current photo, one holds the photo falling away, and one quietly preloads the next
# pick, so a press never waits on a file. A single GLSL Multi pass draws both cards over the input.
#
# Envelope: a hit is the flash. The photo develops over Develop seconds on its own clock. Hold is
# how long the card stays up (counted from the press), and Release is the fall. A new press while a
# card is up drops the old card and flashes the new one in on top.

POLAROID_SHADER = r'''
// Input 0 is the picture. Inputs 1-3 are the photo slots. Distances are in wall heights.
uniform vec4 uRegion;  // left, bottom, width, height of the area, as shares of the wall
uniform vec4 uCard0;   // current card: slot (0 none, 1-3), photo aspect, develop, flash
uniform vec4 uPose0;   // scatter x, scatter y (-1..1 of the free room), tilt (radians), fall (0..1)
uniform vec4 uFall0;   // drift (-1..1), spin (radians), seed, unused
uniform vec4 uCard1;   // the card falling away, same layout
uniform vec4 uPose1;
uniform vec4 uFall1;
uniform vec4 uLook;    // size, tone, mottle, shadow
uniform vec4 uMix;     // spill, clip, mix, unused

layout(location = 0) out vec4 fragColor;

const float PI = 3.14159265;
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

// Instax-style card, in photo widths: thin sides and top, a deep strip along the bottom.
const float SIDE = 0.06;
const float TOP = 0.06;
const float BOTTOM = 0.25;
const vec3 PAPER = vec3(0.95, 0.94, 0.9);

const vec3 UNDEVELOPED = vec3(0.13, 0.17, 0.18);  // the murky teal-grey of fresh film
const vec3 EMERGING = vec3(0.72, 0.84, 0.9);      // the cool cast as the image first comes up
const vec3 WARM_PASS = vec3(0.05, 0.015, -0.035); // colors swing warm while they arrive
const float MOTTLE_REACH = 0.35;                  // how far uneven chemistry can lag behind

const vec2 SHADOW_OFFSET = vec2(0.012, -0.018);
const float SHADOW_SOFT = 0.02;
const float SHADOW_DARK = 0.6;
const float DRIFT = 0.35;  // card widths a falling card slides sideways

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

float box(vec2 q, vec2 halfSize)
{
    vec2 d = abs(q) - halfSize;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}

vec3 photo(int slot, vec2 uv)
{
    if (slot == 1) {
        return texture(sTD2DInputs[1], uv).rgb;
    }
    if (slot == 2) {
        return texture(sTD2DInputs[2], uv).rgb;
    }
    return texture(sTD2DInputs[3], uv).rgb;
}

// The finished print: lifted blacks, softer whites, cool shadows, warm highlights, a vignette.
vec3 finish(vec3 c, float tone, vec2 uv)
{
    float l = dot(c, LUMA);
    vec3 v = mix(c, vec3(l), 0.18);
    v = 0.06 + v * 0.9;
    v *= mix(vec3(0.92, 0.97, 1.04), vec3(1.05, 1.0, 0.9), smoothstep(0.2, 0.8, l));
    float edge = length(uv - 0.5) * 1.25;
    v *= 1.0 - 0.35 * pow(edge, 2.5);
    return mix(c, v, tone);
}

// Fresh film is murky teal-grey. Shadows and shapes come up first in a cool mono, then color
// arrives, swinging warm on the way, and settles on the finished print.
vec3 develop(vec3 c, float d, float tone, vec2 uv)
{
    float l = dot(c, LUMA);
    float shapes = smoothstep(0.0, 0.55, d);
    vec3 ghost = mix(UNDEVELOPED, EMERGING * l + UNDEVELOPED * 0.35, shapes);
    float color = smoothstep(0.3, 1.0, d);
    vec3 result = mix(ghost, finish(c, tone, uv), color);
    return result + WARM_PASS * sin(color * PI);
}

float inRegion(vec2 p, vec2 origin, vec2 size)
{
    return step(origin.x, p.x) * step(p.x, origin.x + size.x) * step(origin.y, p.y) * step(p.y, origin.y + size.y);
}

vec2 unrotate(vec2 d, float angle)
{
    float c = cos(angle);
    float s = sin(angle);
    return vec2(c * d.x + s * d.y, -s * d.x + c * d.y);
}

void drawCard(vec2 p, vec2 origin, vec2 size, float pixel, vec4 info, vec4 pose, vec4 fall, inout vec3 col)
{
    int slot = int(info.x + 0.5);
    if (slot == 0) {
        return;
    }
    float aspect = max(info.y, 0.1);
    vec2 cardSize = vec2(1.0 + 2.0 * SIDE, 1.0 / aspect + TOP + BOTTOM);
    float scale = uLook.x * min(size.x / cardSize.x, size.y / cardSize.y);
    vec2 halfCard = cardSize * scale * 0.5;
    vec2 room = max(size * 0.5 - halfCard, vec2(0.0));
    vec2 center = origin + size * 0.5 + pose.xy * room;

    // Gravity: slow to leave, then gone off the bottom of the wall.
    float f = pose.w;
    center.y -= f * f * (center.y + length(halfCard) * 1.1 + 0.02);
    center.x += fall.x * DRIFT * halfCard.x * 2.0 * f;
    float angle = pose.z + fall.y * f * f;

    vec2 q = unrotate(p - center, angle) / scale;
    float aa = pixel / scale;
    float alpha = 1.0 - smoothstep(-aa, aa, box(q, cardSize * 0.5));

    vec2 qs = unrotate(p - SHADOW_OFFSET - center, angle);
    float shadow = uLook.w * SHADOW_DARK * (1.0 - smoothstep(-SHADOW_SOFT, SHADOW_SOFT, box(qs, cardSize * 0.5 * scale)));

    if (uMix.y > 0.5) {
        float keep = inRegion(p, origin, size);
        alpha *= keep;
        shadow *= keep;
    }
    if (alpha <= 0.0 && shadow <= 0.0) {
        return;
    }

    vec2 imageMin = vec2(-0.5, -cardSize.y * 0.5 + BOTTOM);
    vec2 imageSize = vec2(1.0, 1.0 / aspect);
    vec2 uv = (q - imageMin) / imageSize;
    float inImage = 1.0 - smoothstep(-aa, aa, box(q - imageMin - imageSize * 0.5, imageSize * 0.5));

    float n = 0.65 * noise(uv * 5.0 + fall.z) + 0.35 * noise(uv * 13.0 + fall.z * 1.7);
    float m = uLook.z * MOTTLE_REACH;
    float d = clamp((info.z - n * m) / (1.0 - m), 0.0, 1.0);
    vec3 picture = develop(photo(slot, clamp(uv, 0.0, 1.0)), d, uLook.y, uv);

    vec3 paper = PAPER * (0.97 + 0.03 * noise(q * 40.0));
    vec3 surface = mix(paper, picture, inImage);
    surface = mix(surface, vec3(1.0), clamp(info.w, 0.0, 1.0));

    col = mix(col, vec3(0.0), shadow * (1.0 - alpha));
    col = mix(col, surface, alpha);
}

void main()
{
    vec4 source = texture(sTD2DInputs[0], vUV.st);
    vec2 wall = vec2(uTD2DInfos[0].res.z / uTD2DInfos[0].res.w, 1.0);
    vec2 p = vUV.st * wall;
    vec2 origin = uRegion.xy * wall;
    vec2 size = uRegion.zw * wall;
    float pixel = uTD2DInfos[0].res.y;

    vec3 col = source.rgb;
    col = mix(col, vec3(1.0), clamp(uMix.x, 0.0, 1.0) * inRegion(p, origin, size));
    drawCard(p, origin, size, pixel, uCard1, uPose1, uFall1, col);
    drawCard(p, origin, size, pixel, uCard0, uPose0, uFall0, col);

    fragColor = TDOutputSwizzle(mix(source, vec4(col, source.a), clamp(uMix.z, 0.0, 1.0)));
}
'''

EFFECT_SOURCE = r'''
import math
import os
import random

SLOTS = ["photo_a", "photo_b", "photo_c"]
EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".exr")
FALLBACK_ASPECT = 0.75         # used until a photo's file has opened
FLASH_PEAK = 0.06              # seconds the flash stays fully white
FLASH_DECAY = 0.3              # seconds for the flash to fade after that
SPILL_SCALE = 0.6
TILT_MAX = math.radians(14.0)
SPIN_MAX = math.radians(40.0)  # extra turn a card picks up as it falls
MIN_FALL = 0.05                # seconds, so Release 0 still clears an outgoing card
MAX_DT = 0.1

_rng = random.Random()
_files = []
_folder = None
_bag = []
_sequence = 0
_last_pick = None
_next_slot = 0
_next_file = None
_current = None
_outgoing = None
_last_seconds = None


def _folder_path(block):
    raw = block.par.Folder.eval().strip()
    if not raw:
        return ""
    if not os.path.isabs(raw):
        raw = os.path.join(project.folder, raw)
    return os.path.normpath(raw)


def _scan(block):
    """Lists the folder again only when the Folder parameter changes."""
    global _files, _folder, _bag, _sequence
    path = _folder_path(block)
    if path == _folder:
        return
    _folder = path
    _bag = []
    _sequence = 0
    try:
        names = sorted(n for n in os.listdir(path) if n.lower().endswith(EXTENSIONS) and not n.startswith("."))
    except OSError:
        names = []
    _files = [os.path.join(path, n).replace("\\", "/") for n in names]


def _wanted(block):
    """A fixed Photo number, if one is set."""
    number = int(block.par.Photo.eval())
    if number > 0 and _files:
        return _files[(number - 1) % len(_files)]
    return None


def _pick(block):
    global _bag, _sequence, _last_pick
    if not _files:
        return None
    fixed = _wanted(block)
    if fixed is not None:
        choice = fixed
    elif block.par.Order.eval() == "inorder":
        choice = _files[_sequence % len(_files)]
        _sequence += 1
    else:
        # Shuffle bag: every photo once before any repeats, and never the same one twice running.
        if not _bag:
            _bag = list(_files)
            _rng.shuffle(_bag)
            if len(_bag) > 1 and _bag[-1] == _last_pick:
                _bag[0], _bag[-1] = _bag[-1], _bag[0]
        choice = _bag.pop()
    _last_pick = choice
    return choice


def _load(slot, path):
    node = op(SLOTS[slot])
    node.par.file = path or ""
    if path:
        try:
            node.preload(0)
        except Exception:
            pass  # it opens on its next cook instead


def _prepare_next(block, slot):
    global _next_slot, _next_file
    _next_slot = slot
    _next_file = _pick(block)
    _load(slot, _next_file)


def reset(block):
    global _current, _outgoing, _last_seconds
    _current = None
    _outgoing = None
    _last_seconds = None
    _scan(block)
    if _next_file is None or _next_file not in _files:
        _prepare_next(block, _next_slot)


def _clamped(par):
    return max(0.0, min(1.0, par.eval()))


def _write(node, index, x, y, z, w):
    par = node.par
    setattr(par, "vec%dvaluex" % index, x)
    setattr(par, "vec%dvaluey" % index, y)
    setattr(par, "vec%dvaluez" % index, z)
    setattr(par, "vec%dvaluew" % index, w)


def region(block):
    choice = block.par.Region.eval()
    if choice in ("left", "right"):
        width = block.par.Sidewidth.eval()
        return (0.0 if choice == "left" else 1.0 - width, 0.0, width, 1.0)
    return (block.par.Posx.eval(), block.par.Posy.eval(), max(0.0, block.par.Width.eval()), max(0.0, block.par.Height.eval()))


def flash_curve(age):
    if age <= FLASH_PEAK:
        return 1.0
    return math.exp(-(age - FLASH_PEAK) / FLASH_DECAY * 3.0)


def _aspect(card):
    if card["aspect_known"]:
        return card["aspect"]
    node = op(SLOTS[card["slot"]])
    if node.isOpen and node.fileWidth > 0 and node.fileHeight > 0:
        card["aspect"] = float(node.fileWidth) / float(node.fileHeight)
        card["aspect_known"] = True
    return card["aspect"]


def _new_card(block, slot, path, strength):
    scatter = _clamped(block.par.Scatter)
    return {
        "slot": slot,
        "file": path,
        "age": 0.0,
        "fall": 0.0,
        "strength": strength,
        "aspect": FALLBACK_ASPECT,
        "aspect_known": False,
        "x": _rng.uniform(-1.0, 1.0) * scatter,
        "y": _rng.uniform(-1.0, 1.0) * scatter,
        "tilt": _rng.uniform(-1.0, 1.0) * _clamped(block.par.Tilt) * TILT_MAX,
        "drift": _rng.uniform(-1.0, 1.0),
        "spin": _rng.choice((-1.0, 1.0)) * _rng.uniform(0.4, 1.0) * SPIN_MAX,
        "seed": _rng.uniform(0.0, 100.0),
    }


def _fire(block, env):
    global _current, _outgoing
    _scan(block)
    if not _files:
        return
    fixed = _wanted(block)
    if _next_file is None or _next_file not in _files or (fixed is not None and fixed != _next_file):
        # Nothing usable preloaded. Load now: the flash and the undeveloped film cover the wait.
        _prepare_next(block, _next_slot)
    # A third press while a card is still falling reuses its slot, so that card is gone.
    _outgoing = _current
    _current = _new_card(block, _next_slot, _next_file, env.velocity)
    busy = {_current["slot"]}
    if _outgoing is not None:
        busy.add(_outgoing["slot"])
    _prepare_next(block, min(set(range(len(SLOTS))) - busy))


def _write_card(node, index, card, block):
    base = 1 + index * 3
    if card is None:
        _write(node, base, 0.0, 0.0, 0.0, 0.0)
        _write(node, base + 1, 0.0, 0.0, 0.0, 0.0)
        _write(node, base + 2, 0.0, 0.0, 0.0, 0.0)
        return
    develop_time = max(0.01, block.par.Develop.eval())
    develop = max(0.0, min(1.0, card["age"] / develop_time))
    flash = _clamped(block.par.Flash) * card["strength"] * flash_curve(card["age"])
    _write(node, base, card["slot"] + 1.0, _aspect(card), develop, flash)
    _write(node, base + 1, card["x"], card["y"], card["tilt"], card["fall"])
    _write(node, base + 2, card["drift"], card["spin"], card["seed"], 0.0)


def _write_look(block, node):
    _write(node, 0, *region(block))
    _write(node, 7, max(0.05, _clamped(block.par.Size)), _clamped(block.par.Tone), _clamped(block.par.Mottle), _clamped(block.par.Shadow))


def apply(block, env):
    global _current, _outgoing, _last_seconds
    switch = op("switch")
    node = op("polaroid")
    _write_look(block, node)
    if env.stage == "idle":
        switch.par.index = 0
        _current = None
        _outgoing = None
        _last_seconds = None
        _write_card(node, 0, None, block)
        _write_card(node, 1, None, block)
        _write(node, 8, 0.0, 1.0 if block.par.Clip.eval() else 0.0, _clamped(block.par.Mix), 0.0)
        return

    now = absTime.seconds
    dt = 0.0 if _last_seconds is None else max(0.0, min(MAX_DT, now - _last_seconds))
    _last_seconds = now

    if env.started:
        _fire(block, env)
    elif _current is not None:
        _current["age"] += dt

    if _current is None:
        # No photos in the folder: pass the picture through untouched.
        switch.par.index = 0
        return
    switch.par.index = 1

    _current["fall"] = env.progress if env.stage == "release" else 0.0
    if _outgoing is not None and not env.started:
        _outgoing["age"] += dt
        _outgoing["fall"] += dt / max(MIN_FALL, block.par.Release.eval())
        if _outgoing["fall"] >= 1.0:
            _outgoing = None

    spill = _clamped(block.par.Spill) * SPILL_SCALE * _current["strength"] * flash_curve(_current["age"])
    _write_card(node, 0, _current, block)
    _write_card(node, 1, _outgoing, block)
    _write(node, 8, spill, 1.0 if block.par.Clip.eval() else 0.0, _clamped(block.par.Mix), 0.0)
'''

POLAROID_UNIFORMS = ["uRegion", "uCard0", "uPose0", "uFall0", "uCard1", "uPose1", "uFall1", "uLook", "uMix"]


def build(library):
    block = new_block(library, "polaroid")
    # Attack is unused: the photo develops on its own clock (Develop). Hold counts from the press
    # and includes developing. Release is the fall. Curve is unused.
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 10.0, "release": 1.1, "curve": 1.0, "velocitydepth": 0.6})
    page = block.appendCustomPage("Polaroid")
    folder = page.appendStr("Folder", label="Photo Folder")[0]
    folder.default = ""
    folder.val = ""
    add_menu(page, "Order", "Order", ["shuffle", "inorder"], ["Shuffle", "In Order"], "shuffle")
    photo = page.appendInt("Photo", label="Fixed Photo (0 = off)")[0]
    _set_range(photo, 0, 0, 30, clamp_low=True)
    add_menu(
        page,
        "Region",
        "Region",
        ["left", "right", "custom"],
        ["Left Screen", "Right Screen", "Custom"],
        "left",
    )
    add_float(page, "Sidewidth", "Side Region Width", 1440 / 6720, 0.0, 1.0, True, True)
    add_float(page, "Posx", "Custom Left", 0.0, 0.0, 1.0)
    add_float(page, "Posy", "Custom Bottom", 0.0, 0.0, 1.0)
    add_float(page, "Width", "Custom Width", 0.2143, 0.0, 1.0, clamp_low=True)
    add_float(page, "Height", "Custom Height", 1.0, 0.0, 1.0, clamp_low=True)
    add_float(page, "Size", "Size", 0.82, 0.0, 1.0, True, True)
    add_float(page, "Tilt", "Tilt", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Scatter", "Scatter", 0.3, 0.0, 1.0, True, True)
    add_float(page, "Develop", "Develop (s)", 4.0, 0.5, 12.0, clamp_low=True)
    add_float(page, "Flash", "Flash", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Spill", "Flash Spill", 0.25, 0.0, 1.0, True, True)
    add_float(page, "Tone", "Vintage Tone", 0.4, 0.0, 1.0, True, True)
    add_float(page, "Mottle", "Uneven Develop", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Shadow", "Shadow", 0.5, 0.0, 1.0, True, True)
    clip = page.appendToggle("Clip", label="Keep Inside Region")[0]
    clip.default = True
    clip.val = True
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    add_text(block, "polaroid_shader", POLAROID_SHADER, -300, -150)

    source = add_top(block, inTOP, "in1", -700)
    slots = []
    for index, name in enumerate(["photo_a", "photo_b", "photo_c"]):
        slot = add_top(block, moviefileinTOP, name, -500)
        slot.nodeY = 150 + 130 * index
        slot.par.playmode = "specify"
        slot.par.index = 0
        slot.par.loadingerrorimage = "zero"
        slots.append(slot)

    polaroid = add_top(block, glslmultiTOP, "polaroid", -300)
    polaroid.par.pixeldat = "polaroid_shader"
    polaroid.par.outputresolution = "useinput"
    polaroid.par.format = "useinput"
    polaroid.par.inputfiltertype = "linear"
    polaroid.seq.vec.numBlocks = len(POLAROID_UNIFORMS)
    for index, uniform in enumerate(POLAROID_UNIFORMS):
        setattr(polaroid.par, "vec%dname" % index, uniform)

    switch = add_top(block, switchTOP, "switch", -100)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 100)

    polaroid.inputConnectors[0].connect(source)
    for index, slot in enumerate(slots):
        polaroid.inputConnectors[index + 1].connect(slot)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(polaroid)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
