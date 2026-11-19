# Strobe: stateless hold, flashes the inverted image while pressed

EFFECT_SOURCE = r'''
_frame = 0


def reset(block):
    global _frame
    _frame = 0


def apply(block, env):
    global _frame
    idle = env.stage == "idle"
    op("switch").par.index = 0 if idle else 1
    if idle:
        op("cross").par.cross = 0.0
        _frame = 0
        return

    # Count whole frames so the flashes stay even at high rates.
    rate = max(0.0, min(1.0, block.par.Rate.eval()))
    hertz = 2.0 + 28.0 * rate ** 2
    cycle = max(2, int(round(project.cookRate / hertz)))
    width = max(0.0, min(1.0, block.par.Width.eval()))
    lit = max(1, min(cycle - 1, int(round(cycle * width))))

    if env.started:
        _frame = 0
    _frame = _frame % cycle
    flash = env.level * env.velocity * max(0.0, min(1.0, block.par.Mix.eval()))
    op("cross").par.cross = flash if _frame < lit else 0.0
    _frame += 1
'''


def build(library):
    block = new_block(library, "strobe")
    add_control_page(block, {"mode": "gate", "attack": 0.0, "hold": 0.25, "release": 0.0, "curve": 1.0, "velocitydepth": 0.0})
    page = block.appendCustomPage("Strobe")
    add_float(page, "Rate", "Rate", 0.45, 0.0, 1.0, True, True)
    add_float(page, "Width", "Flash Width", 0.5, 0.0, 1.0, True, True)
    add_float(page, "Mix", "Mix", 1.0, 0.0, 1.0, True, True)

    source = add_top(block, inTOP, "in1", -600)
    # Set once here: animating a Level TOP rebuilds its lookup table every frame.
    level = add_top(block, levelTOP, "level", -400)
    level.par.invert = 1.0
    cross = add_top(block, crossTOP, "cross", -200)
    cross.par.cross = 0.0
    switch = add_top(block, switchTOP, "switch", 0)
    switch.par.blend = False
    switch.par.index = 0
    output = add_top(block, outTOP, "out1", 200)

    source.outputConnectors[0].connect(level)
    cross.inputConnectors[0].connect(source)
    cross.inputConnectors[1].connect(level)
    switch.inputConnectors[0].connect(source)
    switch.inputConnectors[1].connect(cross)
    switch.outputConnectors[0].connect(output)

    add_text(block, "effect", EFFECT_SOURCE, -500, -300)
    add_callbacks(block)
    return block
