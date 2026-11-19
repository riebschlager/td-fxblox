# Stutter: rolling-buffer hold

EFFECT_SOURCE = r'''
_phase = 0
_recording_since = None


def reset(block):
    global _phase, _recording_since
    _phase = 0
    _recording_since = absTime.frame
    op("cache").par.resetpulse.pulse()


def _recorded(size):
    global _recording_since
    if _recording_since is None:
        _recording_since = absTime.frame
    return max(0, min(size, absTime.frame - _recording_since))


def apply(block, env):
    global _phase
    cache = op("cache")
    cross = op("cross")
    if env.stage == "idle":
        if not cache.par.active.eval():
            cache.par.active = True
        cache.par.outputindex = 0
        cross.par.cross = 0.0
        _phase = 0
        return

    size = int(cache.par.cachesize.eval())
    length = 2 + int((size - 2) * max(0.0, min(1.0, block.par.Looplength.eval())) ** 2)
    available = max(1, _recorded(size))
    length = min(length, available)
    played = max(1, int(round(length * max(0.0, min(1.0, block.par.Portion.eval())))))

    if env.started:
        _phase = 0
        cache.par.active = False
    _phase = _phase % played
    cache.par.outputindex = -(length - 1) + _phase
    _phase += 1
    cross.par.cross = max(0.0, min(1.0, env.level * env.velocity * block.par.Mix.eval()))
'''


def build(library):
    block = new_block(library, "stutter")
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.25, "release": 0.03, "curve": 1.0, "velocitydepth": 0.0})
    page = block.appendCustomPage("Stutter")
    add_float(page, "Bufferseconds", "Buffer (s)", 1.0, 0.1, 4.0, clamp_low=True)
    add_float(page, "Looplength", "Loop Length", 0.45, 0.0, 1.0, True, True)
    add_float(page, "Portion", "Portion Played", 1.0, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    source = add_top(block, inTOP, "in1", -600)
    cache = add_top(block, cacheTOP, "cache", -400)
    cache.par.active = True
    cache.par.interp = False
    cache.par.cachesize.expr = "max(2, int(round(parent().par.Bufferseconds * project.cookRate)))"
    cross = add_top(block, crossTOP, "cross", -200)
    cross.par.cross = 0.0
    output = add_top(block, outTOP, "out1", 0)

    source.outputConnectors[0].connect(cache)
    cross.inputConnectors[0].connect(source)
    cross.inputConnectors[1].connect(cache)
    cross.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
