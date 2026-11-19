# Zoom Punch: stateless hit

EFFECT_SOURCE = r'''
import math


def reset(block):
    return


def apply(block, env):
    idle = env.stage == "idle"
    op("switch").par.index = 0 if idle else 1
    if idle:
        op("transform").par.sx = 1.0
        op("transform").par.sy = 1.0
        return

    amount = block.par.Amount.eval() * env.velocity
    value = env.level
    if env.stage == "release":
        value += block.par.Overshoot.eval() * math.sin(env.progress * math.pi * 2.0) * (1.0 - env.progress)
    scale = max(0.01, 1.0 + amount * value)
    op("transform").par.sx = scale
    op("transform").par.sy = scale
    op("cross").par.cross = max(0.0, min(1.0, block.par.Mix.eval()))
'''


def build(library):
    block = new_block(library, "zoom_punch")
    add_control_page(block, {"mode": "hit", "attack": 0.0, "hold": 0.0, "release": 0.28, "curve": 2.5, "velocitydepth": 1.0})
    page = block.appendCustomPage("Zoom Punch")
    add_float(page, "Amount", "Amount", 0.6, -0.9, 3.0)
    add_float(page, "Overshoot", "Overshoot", 0.12, 0.0, 0.6, clamp_low=True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    source = add_top(block, inTOP, "in1", -600)
    transform = add_top(block, transformTOP, "transform", -400)
    transform.par.extend = "mirror"
    cross = add_top(block, crossTOP, "cross", -200)
    cross.par.cross = 1.0
    switch = add_top(block, switchTOP, "switch", 0)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 200)

    source.outputConnectors[0].connect(transform)
    cross.inputConnectors[0].connect(source)
    cross.inputConnectors[1].connect(transform)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(cross)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
