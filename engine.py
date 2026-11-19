# Shared block engine, stored as text in every block's "engine" DAT.
# Runs inside the block, where op, me and absTime exist. kit.py fills in optional integration settings.
PANIC_CHOP = '@@PANIC_CHOP@@'
PANIC_CHANNEL = '@@PANIC_CHANNEL@@'
SCENE_TAG = '@@SCENE_TAG@@'
GAP_FRAMES = 30        # a longer gap between ticks means the scene was away
SCENE_SYNC_FRAMES = 120
MAX_DT = 0.1


class Envelope:
    def __init__(self):
        self.prev = 0.0
        self.clear()

    def clear(self):
        self.stage = "idle"
        self.t = 0.0
        self.level = 0.0
        self.velocity = 1.0
        self.from_level = 0.0
        self.progress = 0.0
        self.started = False

    def _release(self):
        self.stage = "release"
        self.t = 0.0
        self.progress = 0.0
        self.from_level = self.level

    def _fire(self, trigger, p):
        depth = max(0.0, min(1.0, p["velocitydepth"]))
        self.velocity = 1.0 - depth + depth * min(1.0, trigger)
        self.started = True
        self.t = 0.0
        self.progress = 0.0
        self.from_level = self.level
        if p["attack"] > 0:
            self.stage = "attack"
        else:
            self.stage = "hold"
            self.level = 1.0

    def update(self, trigger, dt, p):
        rising = trigger > 0 and self.prev <= 0
        falling = trigger <= 0 and self.prev > 0
        self.prev = trigger
        self.started = False
        gate = p["mode"] == "gate"

        if rising:
            active = self.stage != "idle"
            sustaining = self.stage in ("attack", "hold")
            if gate and sustaining:
                pass
            elif not gate and active and p["retrigger"] == "ignore":
                pass
            else:
                self._fire(trigger, p)

        if falling and gate and self.stage in ("attack", "hold"):
            self._release()

        if self.started:
            return self.level

        self.t += dt
        if self.stage == "attack":
            amount = min(1.0, self.t / p["attack"])
            self.level = self.from_level + (1.0 - self.from_level) * amount
            if amount >= 1.0:
                self.stage = "hold"
                self.t = 0.0
        elif self.stage == "hold":
            self.level = 1.0
            if not gate and self.t >= p["hold"]:
                self._release()
            elif gate and p["maxhold"] > 0 and self.t >= p["maxhold"]:
                self._release()

        if self.stage == "release":
            if p["release"] <= 0:
                self.clear()
            else:
                self.progress = min(1.0, self.t / p["release"])
                remaining = (1.0 - self.progress) ** max(0.01, p["curve"])
                self.level = self.from_level * remaining
                if self.progress >= 1.0:
                    self.clear()
        return self.level


ENV = Envelope()
_last_frame = None
_last_seconds = None
_was_idle = True


def _block():
    return me.parent()


def _effect():
    return op("effect").module


def _params(block):
    par = block.par
    return {
        "mode": par.Mode.eval(),
        "retrigger": par.Retrigger.eval(),
        "attack": max(0.0, par.Attack.eval()),
        "hold": max(0.0, par.Hold.eval()),
        "release": max(0.0, par.Release.eval()),
        "curve": par.Curve.eval(),
        "velocitydepth": par.Velocitydepth.eval(),
        "maxhold": max(0.0, par.Maxhold.eval()),
    }


def _panic_value():
    if not PANIC_CHOP:
        return 0.0
    source = op(PANIC_CHOP)
    if source is None:
        return 0.0
    channel = source[PANIC_CHANNEL]
    if channel is None:
        return 0.0
    return channel.eval()


def scene_path():
    current = _block()
    while current is not None:
        if SCENE_TAG in current.tags:
            return current.path
        current = current.parent()
    return ""


def sync_scene_watch():
    if not SCENE_TAG:
        return
    watch = op("scene_watch")
    path = scene_path()
    current = watch.par.op.eval()
    current_path = current.path if current is not None else ""
    if current_path != path:
        watch.par.op = path


def reset():
    global _was_idle
    ENV.clear()
    _effect().reset(_block())
    _effect().apply(_block(), ENV)
    _was_idle = True


def tick():
    global _last_frame, _last_seconds, _was_idle
    frame = absTime.frame
    seconds = absTime.seconds

    if _last_frame is None:
        sync_scene_watch()
        _effect().reset(_block())
    elif frame % SCENE_SYNC_FRAMES == 0:
        sync_scene_watch()

    if _last_frame is not None and frame - _last_frame > GAP_FRAMES:
        reset()
    dt = 0.0 if _last_seconds is None else max(0.0, min(MAX_DT, seconds - _last_seconds))
    _last_frame = frame
    _last_seconds = seconds

    block = _block()
    if _panic_value() > 0:
        ENV.prev = block.par.Trigger.eval()
        if not _was_idle or ENV.stage != "idle":
            reset()
        return

    trigger = block.par.Trigger.eval()
    if ENV.stage == "idle" and trigger <= 0 and ENV.prev <= 0:
        if not _was_idle:
            _effect().apply(block, ENV)
            _was_idle = True
        return

    ENV.update(trigger, dt, _params(block))
    _effect().apply(block, ENV)
    _was_idle = ENV.stage == "idle"
