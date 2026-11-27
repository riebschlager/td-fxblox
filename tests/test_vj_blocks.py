"""Simulate the FX Blox builder and component runtime without TouchDesigner.

Runs the real build.py against a small stub of the TouchDesigner API, then runs
each block's stored engine and effect text frame by frame. The stub raises on
any operator or parameter name missing from td_op_params.json.

What can't be proven here is how a block looks and feels, or anything
TouchDesigner's own semantics decide. Those are the checks Chris runs by hand.
"""

import json
import math
import re
import runpy
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_PY = REPO_ROOT / 'build.py'
BLOCKS_DIR = BUILD_PY.parent / 'blocks'
OPERATORS = json.loads((Path(__file__).parent / 'td_op_params.json').read_text())['operators']
ALL_BLOCKS = sorted(p.stem for p in BLOCKS_DIR.glob('*.py'))

CONTROL_PARS = ['Trigger', 'Mode', 'Retrigger', 'Attack', 'Hold', 'Release',
                'Curve', 'Velocitydepth', 'Maxhold', 'Reset']
FPS = 60

# The imported effects' original palette, expressed as hexadecimal RGB.
REFERENCE_PALETTE = {
    'BLACK': '000000',
    'BLUE': '2573ff',
    'CYAN': '44f8ff',
    'PINK': 'ff8dff',
    'RED': 'ff424d',
    'WHITE': 'ffffff',
    'YELLOW': 'f3dd3e',
}


# ------------------------------------------------------------ TouchDesigner stub

class Par:
    def __init__(self, owner, name):
        self.owner = owner
        self.name = name
        self.val = '' if name == 'op' else 0
        self.expr = None
        self.pulses = 0

    def eval(self):
        if self.name == 'op' or getattr(self, 'kind', None) == 'top':
            return self.owner.op(self.val) if self.val else None
        if self.expr:
            scope = {'parent': self.owner.parent, 'project': self.owner.world.project}
            return eval(self.expr, scope)
        return self.val

    def pulse(self):
        self.pulses += 1

    def __mul__(self, other):
        return self.eval() * other


class Pars:
    """Attribute access that fails on a name nobody verified."""

    def __init__(self, owner, names):
        object.__setattr__(self, '_pars', {n: Par(owner, n) for n in names})
        object.__setattr__(self, '_owner', owner)

    def _add(self, name):
        assert re.fullmatch('[A-Z][a-z0-9]*', name), 'bad custom parameter name: ' + name
        self._pars[name] = Par(self._owner, name)
        return self._pars[name]

    def __getattr__(self, name):
        if name not in self._pars:
            raise AttributeError('%s has no verified parameter %r' % (self._owner.type, name))
        entry = re.match(r'vec(\d+)', name)
        if entry and int(entry.group(1)) >= self._owner.seq.vec.numBlocks:
            raise AttributeError('%s sequence has not grown to %r' % (self._owner.type, name))
        return self._pars[name]

    def __setattr__(self, name, value):
        getattr(self, name).val = value


class Page:
    def __init__(self, owner, name):
        self.owner = owner
        self.name = name
        self.pars = []

    def _append(self, name):
        par = self.owner.par._add(name)
        self.pars.append(par)
        return [par]

    def appendFloat(self, name, label=None):
        return self._append(name)

    def appendMenu(self, name, label=None):
        return self._append(name)

    def appendPulse(self, name, label=None):
        return self._append(name)

    def appendStr(self, name, label=None):
        return self._append(name)

    def appendInt(self, name, label=None):
        return self._append(name)

    def appendToggle(self, name, label=None):
        return self._append(name)

    def appendTOP(self, name, label=None):
        pars = self._append(name)
        pars[0].kind = 'top'
        pars[0].val = ''
        return pars


class Connector:
    def __init__(self, owner, index, is_input):
        self.owner, self.index, self.is_input = owner, index, is_input

    def connect(self, other):
        if self.is_input:
            self.owner.inputs[self.index] = other
        else:
            other.inputs[len(other.inputs)] = self.owner


class Channel:
    def __init__(self, world):
        self.world = world

    def eval(self):
        return self.world.panic


class Node:
    width, height = 6720, 2048  # TOP resolution: the LED wall

    def __init__(self, world, parent, op_type, name):
        self.world, self._parent, self.type, self.name = world, parent, op_type, name
        self._children = {}
        self.seq = _Namespace()
        self.seq.vec = _Namespace()
        self.seq.vec.numBlocks = 1
        self.par = Pars(self, OPERATORS.get(op_type, {'params': []})['params'])
        self.pages = []
        self.inputs = {}
        self.tags = set()
        self.text = ''
        self.nodeX = self.nodeY = 0
        self.viewer = False
        self.saved_to = None
        self._module = None
        # Movie File In TOP members. A file "opens" when preloaded or cooked.
        self.preloads = []
        self.isOpen = False
        self.fileWidth = self.fileHeight = 0

    @property
    def path(self):
        if self._parent is None:
            return '/'
        return self._parent.path.rstrip('/') + '/' + self.name

    @property
    def children(self):
        return list(self._children.values())

    def parent(self):
        return self._parent

    def create(self, op_type, name):
        assert op_type in OPERATORS, 'unverified operator: ' + op_type
        assert name not in self._children, 'name in use: ' + name
        node = Node(self.world, self, op_type, name)
        self._children[name] = node
        return node

    def destroy(self):
        del self._parent._children[self.name]

    def op(self, path):
        if path.startswith('/'):
            return self.world.root.op(path.strip('/')) if path != '/' else self.world.root
        node = self
        for part in path.split('/'):
            node = node._parent if part == '..' else node._children.get(part)
            if node is None:
                return None
        return node

    def appendCustomPage(self, name):
        self.pages.append(Page(self, name))
        return self.pages[-1]

    @property
    def inputConnectors(self):
        return [Connector(self, i, True) for i in range(3 if self.type == 'glslTOP' else 4)]

    @property
    def outputConnectors(self):
        return [Connector(self, 0, False)]

    def preload(self, index):
        self.preloads.append(self.par.file.val)

    def save(self, target):
        self.saved_to = target
        Path(target).write_text('tox')

    def __getitem__(self, channel):  # CHOP channel lookup, for panic
        return Channel(self.world) if channel == 'note_25' else None

    @property
    def module(self):
        if self._module is None:
            self._module = _Namespace()
            namespace = {'op': self._parent.op, 'me': self, 'absTime': self.world.abs_time,
                         'project': self.world.project}
            exec(self.text, namespace)
            self._module.__dict__ = namespace
        return self._module


class _Namespace:
    pass


class AbsTime:
    frame = 1000
    seconds = 100.0


class World:
    def __init__(self, tmp_path):
        self.root = Node(self, None, 'baseCOMP', '')
        self.abs_time = AbsTime()
        self.project = _Namespace()
        self.project.folder = str(tmp_path / 'touchdesigner')
        self.project.cookRate = FPS
        Path(self.project.folder).mkdir()
        self.panic = 0.0
        midi = self.root.create('baseCOMP', 'midi')
        midi.create('baseCOMP', 'out1')

    def run_build(self, blocks=None):
        options = {
            'blocks': blocks, 'output': Path(self.project.folder).parent / 'dist',
            'panic_chop': '/midi/out1', 'panic_channel': 'note_25', 'scene_tag': 'TDScene',
        }
        namespace = {'project': self.project, 'op': self.root.op, 'FXBLOX': options}
        namespace.update({name: name for name in OPERATORS})
        runpy.run_path(str(BUILD_PY), init_globals=namespace, run_name='__main__')

    @property
    def library(self):
        return self.root.op('vj_block_library')

    def block(self, name):
        return self.library.op(name)

    def tick(self, block, frames=1):
        for _ in range(frames):
            self.abs_time.frame += 1
            self.abs_time.seconds += 1.0 / FPS
            block.op('tick').module.onFrameStart()


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    w.run_build()
    return w


@pytest.fixture
def zoom(world):
    block = world.block('zoom_punch')
    block.par.Overshoot = 0.0
    return block


@pytest.fixture
def stutter(world):
    return world.block('stutter')


def scale(block):
    return block.op('transform').par.sx.val


def assert_zoom_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert block.op('transform').par.sx.val == 1.0
    assert block.op('transform').par.sy.val == 1.0


def assert_stutter_at_rest(block):
    assert block.op('cache').par.active.val is True
    assert block.op('cache').par.outputindex.val == 0
    assert block.op('cross').par.cross.val == 0.0


# ------------------------------------------------------------ build

def test_all_blocks_found():
    assert {'zoom_punch', 'stutter'} <= set(ALL_BLOCKS)


@pytest.mark.parametrize('name', ALL_BLOCKS)
def test_block_contract(world, name):
    block = world.block(name)
    assert block.type == 'baseCOMP'
    assert block.saved_to.endswith('/dist/%s.tox' % name)
    assert Path(block.saved_to).exists()
    assert block.op('in1').type == 'inTOP'
    assert block.op('out1').type == 'outTOP'
    assert block.op('out1').inputs, 'out1 is not connected'
    assert [p.name for p in block.pages[0].pars] == CONTROL_PARS
    assert block.pages[0].name == 'Control'
    assert len(block.pages) == 2
    assert block.pages[1].pars[-1].name == 'Mix'
    assert 'midi' not in block.op('effect').text.lower()


@pytest.mark.parametrize('name', ALL_BLOCKS)
def test_engine_text_is_filled_in(world, name):
    engine = world.block(name).op('engine').text
    assert '@@' not in engine
    runtime = world.block(name).op('engine').module
    assert runtime.PANIC_CHOP == '/midi/out1'
    assert runtime.PANIC_CHANNEL == 'note_25'
    assert runtime.SCENE_TAG == 'TDScene'


def test_named_build_replaces_only_that_block(world):
    stutter_before, zoom_before = world.block('stutter'), world.block('zoom_punch')
    world.run_build(['zoom_punch'])
    assert world.block('stutter') is stutter_before
    assert world.block('zoom_punch') is not zoom_before


def test_blocks_keep_their_position_when_rebuilt_alone(world):
    before = world.block('zoom_punch').nodeX
    world.run_build(['zoom_punch'])
    assert world.block('zoom_punch').nodeX == before


def test_library_blocks_never_overlap(world):
    world.run_build(['strobe'])
    positions = [block.nodeX for block in world.library.children]
    assert len(set(positions)) == len(positions)


def test_public_build_works_without_show_integration(tmp_path):
    w = World(tmp_path)
    source = tmp_path / 'checkout'
    source.mkdir()
    for filename in ('build.py', 'kit.py', 'engine.py'):
        shutil.copyfile(REPO_ROOT / filename, source / filename)
    (source / 'blocks').mkdir()
    shutil.copyfile(BLOCKS_DIR / 'zoom_punch.py', source / 'blocks' / 'zoom_punch.py')
    namespace = {'op': w.root.op, 'FXBLOX': {'blocks': ['zoom_punch']}}
    namespace.update({name: name for name in OPERATORS})
    runpy.run_path(str(source / 'build.py'), init_globals=namespace, run_name='__main__')
    block = w.block('zoom_punch')
    assert block.op('scene_watch') is None
    w.panic = 1.0
    block.par.Trigger = 1.0
    w.tick(block)
    assert block.op('switch').par.index.val == 1
    assert sorted(path.name for path in (source / 'dist').glob('*.tox')) == ['zoom_punch.tox']


def test_unknown_block_changes_nothing(world):
    before = world.block('stutter')
    with pytest.raises(ValueError, match='nope'):
        world.run_build(['stutter', 'nope'])
    assert world.block('stutter') is before


def test_zoom_wiring(zoom):
    assert zoom.op('transform').inputs[0] is zoom.op('in1')
    assert zoom.op('cross').inputs == {0: zoom.op('in1'), 1: zoom.op('transform')}
    assert zoom.op('switch').inputs == {0: zoom.op('in1'), 1: zoom.op('cross')}
    assert zoom.op('out1').inputs[0] is zoom.op('switch')


def test_stutter_wiring(stutter):
    assert stutter.op('cache').inputs[0] is stutter.op('in1')
    assert stutter.op('cross').inputs == {0: stutter.op('in1'), 1: stutter.op('cache')}
    assert stutter.op('out1').inputs[0] is stutter.op('cross')


# ------------------------------------------------------------ Zoom Punch playing

def test_press_lands_on_first_frame(world, zoom):
    world.tick(zoom, 3)
    zoom.par.Trigger = 1.0
    world.tick(zoom)
    assert zoom.op('switch').par.index.val == 1
    assert scale(zoom) == pytest.approx(1.6)


def test_velocity_scales_the_result(world, zoom):
    world.tick(zoom, 3)
    zoom.par.Trigger = 0.5
    world.tick(zoom)
    assert scale(zoom) == pytest.approx(1.3)


def test_returns_exactly_to_rest(world, zoom):
    world.tick(zoom, 3)
    zoom.par.Trigger = 1.0
    world.tick(zoom)
    zoom.par.Trigger = 0.0
    world.tick(zoom, FPS)
    assert_zoom_at_rest(zoom)


def press_again_mid_release(world, zoom, retrigger):
    zoom.par.Retrigger = retrigger
    world.tick(zoom, 3)
    zoom.par.Trigger = 1.0
    world.tick(zoom)
    zoom.par.Trigger = 0.0
    world.tick(zoom, 8)
    before = scale(zoom)
    zoom.par.Trigger = 1.0
    world.tick(zoom)
    return before, scale(zoom)


def test_retrigger_restart(world, zoom):
    before, after = press_again_mid_release(world, zoom, 'restart')
    assert before < 1.6
    assert after == pytest.approx(1.6)


def test_retrigger_ignore(world, zoom):
    before, after = press_again_mid_release(world, zoom, 'ignore')
    assert after < before


def test_max_hold_releases_a_held_gate(world, zoom):
    zoom.par.Mode = 'gate'
    zoom.par.Maxhold = 0.1
    world.tick(zoom, 3)
    zoom.par.Trigger = 1.0
    world.tick(zoom, FPS * 2)
    assert_zoom_at_rest(zoom)


def test_panic_clears_and_held_pad_does_not_refire(world, zoom):
    zoom.par.Mode = 'gate'
    world.tick(zoom, 3)
    zoom.par.Trigger = 1.0
    world.tick(zoom, 3)
    assert zoom.op('switch').par.index.val == 1
    world.panic = 1.0
    world.tick(zoom)
    assert_zoom_at_rest(zoom)
    world.panic = 0.0
    world.tick(zoom, 3)
    assert_zoom_at_rest(zoom)


def test_reset_pulse_clears_state(world, zoom):
    zoom.par.Mode = 'gate'
    world.tick(zoom, 3)
    zoom.par.Trigger = 1.0
    world.tick(zoom, 3)
    zoom.op('reset_watch').module.onPulse()
    assert_zoom_at_rest(zoom)


def test_frame_gap_resets(world, zoom):
    zoom.par.Mode = 'gate'
    world.tick(zoom, 3)
    zoom.par.Trigger = 1.0
    world.tick(zoom, 3)
    world.abs_time.frame += 31
    world.tick(zoom)
    assert_zoom_at_rest(zoom)


def test_scene_watch_finds_the_enclosing_scene(world, zoom):
    world.library.tags.add('TDScene')
    world.tick(zoom)
    assert zoom.op('scene_watch').par.op.val == world.library.path


# ------------------------------------------------------------ Stutter playing

def test_stutter_works_on_the_very_first_frame(world, stutter):
    stutter.par.Trigger = 1.0
    world.tick(stutter)
    assert stutter.op('cache').par.active.val is False
    assert stutter.op('cache').par.outputindex.val == 0
    assert stutter.op('cross').par.cross.val == 1.0


def test_stutter_loops_within_recorded_frames(world, stutter):
    world.tick(stutter, 5)
    stutter.par.Trigger = 1.0
    seen = []
    for _ in range(12):
        world.tick(stutter)
        seen.append(stutter.op('cache').par.outputindex.val)
    assert min(seen) >= -4
    assert max(seen) <= 0


def test_stutter_loop_length_after_full_buffer(world, stutter):
    world.tick(stutter, FPS + 5)
    stutter.par.Trigger = 1.0
    world.tick(stutter)
    assert stutter.op('cache').par.outputindex.val == -12
    world.tick(stutter)
    assert stutter.op('cache').par.outputindex.val == -11


def test_stutter_returns_exactly_to_rest(world, stutter):
    world.tick(stutter, 10)
    stutter.par.Trigger = 1.0
    world.tick(stutter, 5)
    stutter.par.Trigger = 0.0
    world.tick(stutter, FPS)
    assert_stutter_at_rest(stutter)


def test_stutter_panic(world, stutter):
    world.tick(stutter, 10)
    stutter.par.Trigger = 1.0
    world.tick(stutter, 5)
    world.panic = 1.0
    world.tick(stutter)
    assert_stutter_at_rest(stutter)


# ------------------------------------------------------------ Strobe playing

@pytest.fixture
def strobe(world):
    return world.block('strobe')


def flashes(world, block, frames):
    seen = []
    for _ in range(frames):
        world.tick(block)
        seen.append(block.op('cross').par.cross.val)
    return seen


def assert_strobe_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert block.op('cross').par.cross.val == 0.0


def test_strobe_wiring(strobe):
    assert strobe.op('level').inputs[0] is strobe.op('in1')
    assert strobe.op('level').par.invert.val == 1.0
    assert strobe.op('cross').inputs == {0: strobe.op('in1'), 1: strobe.op('level')}
    assert strobe.op('switch').inputs == {0: strobe.op('in1'), 1: strobe.op('cross')}
    assert strobe.op('out1').inputs[0] is strobe.op('switch')


def test_strobe_flashes_on_the_very_first_frame(world, strobe):
    strobe.par.Trigger = 1.0
    world.tick(strobe)
    assert strobe.op('switch').par.index.val == 1
    assert strobe.op('cross').par.cross.val == 1.0


def test_strobe_default_pattern_is_even(world, strobe):
    strobe.par.Trigger = 1.0
    assert flashes(world, strobe, 16) == [1.0] * 4 + [0.0] * 4 + [1.0] * 4 + [0.0] * 4


def test_strobe_top_rate_alternates_frames(world, strobe):
    strobe.par.Rate = 1.0
    strobe.par.Trigger = 1.0
    assert flashes(world, strobe, 4) == [1.0, 0.0, 1.0, 0.0]


def test_strobe_cycle_never_goes_solid_or_dark(world, strobe):
    strobe.par.Rate = 1.0
    strobe.par.Trigger = 1.0
    for width in (0.0, 1.0):
        strobe.par.Width = width
        assert sorted(set(flashes(world, strobe, 4))) == [0.0, 1.0]


def test_strobe_velocity_scales_when_depth_is_up(world, strobe):
    strobe.par.Velocitydepth = 1.0
    strobe.par.Trigger = 0.5
    world.tick(strobe)
    assert strobe.op('cross').par.cross.val == pytest.approx(0.5)


def test_strobe_stops_the_frame_the_pad_lifts(world, strobe):
    strobe.par.Trigger = 1.0
    world.tick(strobe, 2)
    strobe.par.Trigger = 0.0
    world.tick(strobe)
    assert_strobe_at_rest(strobe)


def test_strobe_each_press_starts_lit(world, strobe):
    strobe.par.Trigger = 1.0
    world.tick(strobe, 6)
    strobe.par.Trigger = 0.0
    world.tick(strobe)
    strobe.par.Trigger = 1.0
    world.tick(strobe)
    assert strobe.op('cross').par.cross.val == 1.0


def test_strobe_panic_and_held_pad_does_not_refire(world, strobe):
    strobe.par.Trigger = 1.0
    world.tick(strobe, 3)
    world.panic = 1.0
    world.tick(strobe)
    assert_strobe_at_rest(strobe)
    world.panic = 0.0
    world.tick(strobe, 3)
    assert_strobe_at_rest(strobe)


def test_strobe_reset_and_frame_gap(world, strobe):
    strobe.par.Trigger = 1.0
    world.tick(strobe, 3)
    strobe.op('reset_watch').module.onPulse()
    assert_strobe_at_rest(strobe)
    strobe.par.Trigger = 0.0
    world.tick(strobe, 2)
    strobe.par.Trigger = 1.0
    world.tick(strobe, 3)
    world.abs_time.frame += 31
    world.tick(strobe)
    assert_strobe_at_rest(strobe)


def test_strobe_max_hold(world, strobe):
    strobe.par.Maxhold = 0.1
    strobe.par.Trigger = 1.0
    world.tick(strobe, FPS)
    assert_strobe_at_rest(strobe)


# ------------------------------------------------------------ Radial Burst playing

@pytest.fixture
def burst(world):
    return world.block('radial_burst')


def uniforms(block, name, index=0):
    par = block.op(name).par
    return tuple(getattr(par, 'vec%dvalue%s' % (index, axis)).val for axis in 'xyzw')


def assert_burst_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert uniforms(block, 'composite', 0) == (0.5, 0.5, 2.0, 0.0)
    assert uniforms(block, 'composite', 1) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'composite', 2)[:2] == (0.0, 0.0)
    for name in ('blur1', 'blur2', 'blur3'):
        assert uniforms(block, name)[2] == 0.0


def test_burst_wiring(burst):
    assert burst.op('extract').inputs[0] is burst.op('in1')
    assert burst.op('blur1').inputs[0] is burst.op('extract')
    assert burst.op('blur2').inputs[0] is burst.op('blur1')
    assert burst.op('blur3').inputs[0] is burst.op('blur2')
    assert burst.op('composite').inputs == {0: burst.op('in1'), 1: burst.op('blur3')}
    assert burst.op('switch').inputs == {0: burst.op('in1'), 1: burst.op('composite')}
    assert burst.op('out1').inputs[0] is burst.op('switch')


def test_burst_only_the_extract_pass_halves_resolution(burst):
    assert burst.op('extract').par.outputresolution.val == 'half'
    for name in ('blur1', 'blur2', 'blur3', 'composite'):
        assert burst.op(name).par.outputresolution.val == 'useinput'
    assert burst.op('composite').par.format.val == 'useinput'


def test_burst_shader_uniforms_match_their_entries(burst):
    for name in ('extract', 'blur1', 'blur2', 'blur3', 'composite'):
        node = burst.op(name)
        text = burst.op(node.par.pixeldat.val).text
        for index in range(node.seq.vec.numBlocks):
            uniform = getattr(node.par, 'vec%dname' % index).val
            assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_burst_lands_on_the_very_first_frame(world, burst):
    burst.par.Trigger = 1.0
    world.tick(burst)
    assert burst.op('switch').par.index.val == 1
    x, y, front, punch = uniforms(burst, 'composite', 0)
    assert (x, y) == (0.5, 0.5)
    assert front == pytest.approx(0.25)
    assert punch == pytest.approx(0.06)
    glow, smear, _, spokes = uniforms(burst, 'composite', 1)
    assert glow == pytest.approx(1.8)
    assert smear == pytest.approx(0.45)
    assert spokes == pytest.approx(0.7)
    assert uniforms(burst, 'composite', 2)[1] == 1.0
    assert uniforms(burst, 'blur1')[2] == pytest.approx(1.204, abs=0.001)


def test_burst_blur_spans_step_down_by_ten(world, burst):
    burst.par.Trigger = 1.0
    world.tick(burst)
    first = uniforms(burst, 'blur1')[2]
    assert uniforms(burst, 'blur2')[2] == pytest.approx(first / 10)
    assert uniforms(burst, 'blur3')[2] == pytest.approx(first / 100)


def test_burst_velocity_scales_the_result(world, burst):
    burst.par.Trigger = 0.5
    world.tick(burst)
    assert uniforms(burst, 'composite', 1)[0] == pytest.approx(0.9)
    assert uniforms(burst, 'blur1')[2] < 0.5


def test_burst_front_travels_and_soft_hits_stop_short(world, burst):
    burst.par.Trigger = 0.2
    world.tick(burst)
    start = uniforms(burst, 'composite', 0)[2]
    world.tick(burst, 12)
    soft = uniforms(burst, 'composite', 0)[2]
    assert start == pytest.approx(0.25)
    assert soft == pytest.approx(0.55)
    burst.par.Trigger = 0.0
    world.tick(burst, FPS * 2)
    burst.par.Trigger = 1.0
    world.tick(burst, 13)
    assert uniforms(burst, 'composite', 0)[2] == pytest.approx(1.35)


def test_burst_front_off_covers_the_wall(world, burst):
    burst.par.Front = 0.0
    burst.par.Trigger = 1.0
    world.tick(burst)
    assert uniforms(burst, 'composite', 0)[2] == 2.0


def test_burst_recoils_inward_during_release(world, burst):
    burst.par.Trigger = 1.0
    world.tick(burst)
    burst.par.Trigger = 0.0
    punches = []
    for _ in range(30):
        world.tick(burst)
        punches.append(uniforms(burst, 'composite', 0)[3])
    assert min(punches) < 0.0


def test_burst_scatter_off_stays_on_center_and_on_moves_it(world, burst):
    burst.par.Centerx = 0.2
    burst.par.Trigger = 1.0
    world.tick(burst)
    assert uniforms(burst, 'composite', 0)[:2] == (0.2, 0.5)
    burst.par.Trigger = 0.0
    world.tick(burst, FPS)
    burst.par.Scatter = 1.0
    seen = set()
    for _ in range(4):
        burst.par.Trigger = 1.0
        world.tick(burst)
        x, y = uniforms(burst, 'composite', 0)[:2]
        assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
        seen.add((x, y))
        burst.par.Trigger = 0.0
        world.tick(burst, FPS)
    assert len(seen) > 1


def test_burst_each_hit_gets_a_new_ray_pattern(world, burst):
    seeds = set()
    for _ in range(3):
        burst.par.Trigger = 1.0
        world.tick(burst)
        seeds.add(uniforms(burst, 'composite', 2)[0])
        burst.par.Trigger = 0.0
        world.tick(burst, FPS)
    assert len(seeds) == 3


def test_burst_returns_exactly_to_rest(world, burst):
    burst.par.Trigger = 1.0
    world.tick(burst, 2)
    burst.par.Trigger = 0.0
    world.tick(burst, FPS)
    assert_burst_at_rest(burst)


def test_burst_retrigger_restarts_the_front(world, burst):
    burst.par.Trigger = 1.0
    world.tick(burst, 2)
    burst.par.Trigger = 0.0
    world.tick(burst, 10)
    assert uniforms(burst, 'composite', 0)[2] > 0.5
    burst.par.Trigger = 1.0
    world.tick(burst)
    assert uniforms(burst, 'composite', 0)[2] == pytest.approx(0.25)


def test_burst_panic_and_held_pad_does_not_refire(world, burst):
    burst.par.Trigger = 1.0
    world.tick(burst)
    world.panic = 1.0
    world.tick(burst)
    assert_burst_at_rest(burst)
    world.panic = 0.0
    world.tick(burst, 3)
    assert_burst_at_rest(burst)


def test_burst_reset_and_frame_gap(world, burst):
    burst.par.Trigger = 1.0
    world.tick(burst)
    burst.op('reset_watch').module.onPulse()
    assert_burst_at_rest(burst)
    burst.par.Trigger = 0.0
    world.tick(burst, 2)
    burst.par.Trigger = 1.0
    world.tick(burst)
    world.abs_time.frame += 31
    world.tick(burst)
    assert_burst_at_rest(burst)


def test_burst_max_hold_in_gate_mode(world, burst):
    burst.par.Mode = 'gate'
    burst.par.Maxhold = 0.1
    burst.par.Trigger = 1.0
    world.tick(burst, FPS * 2)
    assert_burst_at_rest(burst)


def test_stutter_reset_clears_the_buffer(world, stutter):
    world.tick(stutter, 10)
    pulses = stutter.op('cache').par.resetpulse.pulses
    stutter.op('reset_watch').module.onPulse()
    assert stutter.op('cache').par.resetpulse.pulses == pulses + 1
    assert_stutter_at_rest(stutter)


# ------------------------------------------------------------ Shockwave playing

@pytest.fixture
def shock(world):
    return world.block('shockwave')


def rings(block):
    return [uniforms(block, 'shock', index) for index in range(4)]


def live(block):
    return [ring for ring in rings(block) if ring[3] > 0.0]


def hit(world, block, velocity=1.0):
    block.par.Trigger = velocity
    world.tick(block)
    block.par.Trigger = 0.0


def assert_shock_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert rings(block) == [(0.5, 0.5, -1.0, 0.0)] * 4
    assert uniforms(block, 'shock', 4) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'shock', 5) == (1.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'shock', 6)[:2] == (0.0, 0.0)


def test_shock_wiring(shock):
    assert shock.op('shock').inputs[0] is shock.op('in1')
    assert shock.op('switch').inputs == {0: shock.op('in1'), 1: shock.op('shock')}
    assert shock.op('out1').inputs[0] is shock.op('switch')


def test_shock_shader_uniforms_match_their_entries(shock):
    node = shock.op('shock')
    text = shock.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 7
    for index in range(7):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_shock_lands_on_the_very_first_frame(world, shock):
    hit(world, shock)
    assert shock.op('switch').par.index.val == 1
    assert live(shock) == [pytest.approx((0.5, 0.5, 0.1, 0.75))]
    assert uniforms(shock, 'shock', 6)[1] == 1.0


def test_shock_velocity_scales_amplitude_and_width(world, shock):
    hit(world, shock, 1.0)
    hard_width = uniforms(shock, 'shock', 4)[0]
    assert live(shock)[0][3] == pytest.approx(0.75)
    world.tick(shock, FPS)
    hit(world, shock, 0.5)
    assert live(shock)[0][3] == pytest.approx(0.375)
    assert uniforms(shock, 'shock', 4)[0] == pytest.approx(hard_width * 0.75)


def test_shock_front_travels_out_and_fades(world, shock):
    hit(world, shock)
    fronts, amps = [], []
    for _ in range(30):
        world.tick(shock)
        fronts.append(live(shock)[0][2])
        amps.append(live(shock)[0][3])
    assert fronts == sorted(fronts) and fronts[-1] > 1.0
    assert amps == sorted(amps, reverse=True)


def test_shock_curve_shapes_the_fade(world, shock):
    shock.par.Curve = 3.0
    hit(world, shock)
    world.tick(shock, 21)
    steep = live(shock)[0][3]
    world.tick(shock, FPS)
    shock.par.Curve = 0.5
    hit(world, shock)
    world.tick(shock, 21)
    assert steep < live(shock)[0][3]


def test_shock_release_sets_the_flight(world, shock):
    shock.par.Release = 0.25
    hit(world, shock)
    world.tick(shock, 13)
    assert len(live(shock)) == 1
    world.tick(shock, 4)
    assert_shock_at_rest(shock)


def test_shock_retrigger_stacks_and_older_rings_keep_flying(world, shock):
    hit(world, shock)
    world.tick(shock, 10)
    before = live(shock)[0][2]
    hit(world, shock)
    fronts = sorted(ring[2] for ring in live(shock))
    assert fronts[0] == pytest.approx(0.1)
    assert fronts[1] > before


def test_shock_fifth_press_replaces_only_the_oldest(world, shock):
    for _ in range(4):
        hit(world, shock)
        world.tick(shock, 3)
    assert len(live(shock)) == 4
    before = sorted(ring[2] for ring in live(shock))
    hit(world, shock)
    after = sorted(ring[2] for ring in live(shock))
    assert len(after) == 4
    assert after[0] == pytest.approx(0.1)
    assert after[-1] < before[-1]


def test_shock_retrigger_ignore_launches_nothing(world, shock):
    shock.par.Retrigger = 'ignore'
    hit(world, shock)
    world.tick(shock, 5)
    hit(world, shock)
    assert len(live(shock)) == 1


def test_shock_each_ring_keeps_its_launch_origin(world, shock):
    shock.par.Originx = 0.2
    hit(world, shock)
    world.tick(shock, 2)
    shock.par.Originx = 0.8
    world.tick(shock, 2)
    assert [ring[0] for ring in live(shock)] == [0.2]
    hit(world, shock)
    assert sorted(ring[0] for ring in live(shock)) == [0.2, 0.8]


def test_shock_look_controls_reach_the_shader(world, shock):
    shock.par.Roundness = 0.0
    shock.par.Chroma = 1.0
    shock.par.Aftershock = 0.25
    shock.par.Lensing = 0.1
    shock.par.Mix = 0.5
    hit(world, shock)
    assert uniforms(shock, 'shock', 5) == pytest.approx((0.0, 0.25, 0.35, 0.1))
    assert uniforms(shock, 'shock', 6)[1] == 0.5


def test_shock_returns_exactly_to_rest(world, shock):
    hit(world, shock)
    world.tick(shock, FPS)
    assert_shock_at_rest(shock)


def test_shock_starts_clean_after_returning_to_rest(world, shock):
    hit(world, shock)
    world.tick(shock, FPS)
    hit(world, shock)
    assert len(live(shock)) == 1


def test_shock_panic_and_held_pad_does_not_refire(world, shock):
    shock.par.Trigger = 1.0
    world.tick(shock)
    world.panic = 1.0
    world.tick(shock)
    assert_shock_at_rest(shock)
    world.panic = 0.0
    world.tick(shock, 3)
    assert_shock_at_rest(shock)


def test_shock_reset_and_frame_gap(world, shock):
    hit(world, shock)
    shock.op('reset_watch').module.onPulse()
    assert_shock_at_rest(shock)
    world.tick(shock, 2)
    hit(world, shock)
    world.abs_time.frame += 31
    world.tick(shock)
    assert_shock_at_rest(shock)


def test_shock_max_hold_in_gate_mode(world, shock):
    shock.par.Mode = 'gate'
    shock.par.Maxhold = 0.1
    shock.par.Trigger = 1.0
    world.tick(shock, FPS * 2)
    assert_shock_at_rest(shock)


# ------------------------------------------------------------ Slice Shift playing

@pytest.fixture
def slicer(world):
    return world.block('slice_shift')


def cut(block):
    return uniforms(block, 'slice', 0)


def motion(block):
    return uniforms(block, 'slice', 1)


def look(block):
    return uniforms(block, 'slice', 2)


def assert_slice_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert cut(block) == (0.0, 1.0, 0.0, 0.0)
    assert motion(block) == (1.0, 0.5, 0.0, 0.0)
    assert look(block) == (0.0, 0.0, 0.0, 0.0)


def test_slice_wiring(slicer):
    assert slicer.op('slice').inputs[0] is slicer.op('in1')
    assert slicer.op('switch').inputs == {0: slicer.op('in1'), 1: slicer.op('slice')}
    assert slicer.op('out1').inputs[0] is slicer.op('switch')


def test_slice_shader_uniforms_match_their_entries(slicer):
    node = slicer.op('slice')
    text = slicer.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 3
    for index in range(3):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_slice_lands_on_the_very_first_frame(world, slicer):
    hit(world, slicer)
    assert slicer.op('switch').par.index.val == 1
    assert motion(slicer)[0] == 0.0
    assert motion(slicer)[3] == pytest.approx(0.75)
    assert look(slicer) == pytest.approx((1.0, 0.5, 0.6, 1.0))


def test_slice_soft_hit_is_fewer_slabs_and_less_violent(world, slicer):
    hit(world, slicer, 1.0)
    hard_cut, hard_motion, hard_look = cut(slicer), motion(slicer), look(slicer)
    assert hard_cut[1] == 22.0
    world.tick(slicer, FPS)
    hit(world, slicer, 0.1)
    assert cut(slicer)[1] < hard_cut[1] / 3
    assert cut(slicer)[3] < hard_cut[3]
    assert motion(slicer)[3] < hard_motion[3] / 2
    assert look(slicer)[0] == pytest.approx(hard_look[0] * 0.1)
    assert look(slicer)[1] == pytest.approx(hard_look[1] * 0.1)


def test_slice_slices_knob_sets_the_most_slabs(world, slicer):
    slicer.par.Slices = 1.0
    hit(world, slicer)
    assert cut(slicer)[1] == 40.0
    world.tick(slicer, FPS)
    slicer.par.Slices = 0.0
    hit(world, slicer)
    assert cut(slicer)[1] == 4.0


def test_slice_zip_runs_forward_and_reaches_home(world, slicer):
    hit(world, slicer)
    seen = []
    for _ in range(32):
        world.tick(slicer)
        seen.append(motion(slicer)[0])
    assert seen == sorted(seen)
    assert seen[-1] > 0.9


def test_slice_curve_shapes_the_zip(world, slicer):
    hit(world, slicer)
    world.tick(slicer, 10)
    linear = motion(slicer)[0]
    world.tick(slicer, FPS)
    slicer.par.Curve = 3.0
    hit(world, slicer)
    world.tick(slicer, 10)
    assert motion(slicer)[0] > linear


def test_slice_every_hit_gets_a_new_cut_and_strike(world, slicer):
    seen = set()
    for _ in range(4):
        hit(world, slicer)
        seen.add((cut(slicer)[2], motion(slicer)[1]))
        world.tick(slicer, 5)
    assert len(seen) == 4


def test_slice_cut_stays_put_during_a_hit(world, slicer):
    hit(world, slicer)
    first = cut(slicer)
    slicer.par.Angle = 1.0
    slicer.par.Slices = 0.0
    world.tick(slicer, 5)
    assert cut(slicer) == first


def test_slice_angle_is_a_quarter_turn_at_full(world, slicer):
    slicer.par.Angle = 1.0
    hit(world, slicer)
    assert cut(slicer)[0] == pytest.approx(1.5707963)


def test_slice_random_angle_varies_only_when_asked(world, slicer):
    angles = set()
    for _ in range(3):
        hit(world, slicer)
        angles.add(cut(slicer)[0])
        world.tick(slicer, FPS)
    assert len(angles) == 1
    slicer.par.Randomangle = 1.0
    for _ in range(3):
        hit(world, slicer)
        angles.add(cut(slicer)[0])
        world.tick(slicer, FPS)
    assert len(angles) > 1


def test_slice_retrigger_restarts_with_a_new_cut(world, slicer):
    hit(world, slicer)
    seed = cut(slicer)[2]
    world.tick(slicer, 10)
    assert motion(slicer)[0] > 0.2
    hit(world, slicer)
    assert motion(slicer)[0] == 0.0
    assert cut(slicer)[2] != seed


def test_slice_retrigger_ignore_keeps_the_cut(world, slicer):
    slicer.par.Retrigger = 'ignore'
    hit(world, slicer)
    seed = cut(slicer)[2]
    world.tick(slicer, 5)
    before = motion(slicer)[0]
    hit(world, slicer)
    assert cut(slicer)[2] == seed
    assert motion(slicer)[0] > before


def test_slice_gate_holds_the_cut_open(world, slicer):
    slicer.par.Mode = 'gate'
    slicer.par.Trigger = 1.0
    world.tick(slicer, FPS)
    assert motion(slicer)[0] == 0.0
    assert motion(slicer)[3] == pytest.approx(0.75)
    slicer.par.Trigger = 0.0
    world.tick(slicer, 5)
    assert motion(slicer)[0] > 0.0


def test_slice_attack_ramps_and_release_does_not_jump(world, slicer):
    slicer.par.Mode = 'gate'
    slicer.par.Attack = 1.0
    slicer.par.Trigger = 1.0
    world.tick(slicer, 2)
    world.tick(slicer, 15)
    partway = motion(slicer)[3]
    assert 0.0 < partway < 0.75
    slicer.par.Trigger = 0.0
    world.tick(slicer)
    assert motion(slicer)[3] == pytest.approx(partway, abs=0.02)


def test_slice_look_controls_reach_the_shader(world, slicer):
    slicer.par.Throw = 1.0
    slicer.par.Stagger = 1.0
    slicer.par.Seam = 0.25
    slicer.par.Split = 1.0
    slicer.par.Spring = 0.1
    slicer.par.Mix = 0.5
    hit(world, slicer)
    assert motion(slicer)[2:] == pytest.approx((0.85, 1.5))
    assert look(slicer) == pytest.approx((0.25, 1.0, 0.1, 0.5))


def test_slice_returns_exactly_to_rest(world, slicer):
    hit(world, slicer)
    world.tick(slicer, FPS)
    assert_slice_at_rest(slicer)


def test_slice_panic_and_held_pad_does_not_refire(world, slicer):
    slicer.par.Trigger = 1.0
    world.tick(slicer)
    world.panic = 1.0
    world.tick(slicer)
    assert_slice_at_rest(slicer)
    world.panic = 0.0
    world.tick(slicer, 3)
    assert_slice_at_rest(slicer)


def test_slice_reset_and_frame_gap(world, slicer):
    hit(world, slicer)
    slicer.op('reset_watch').module.onPulse()
    assert_slice_at_rest(slicer)
    world.tick(slicer, 2)
    hit(world, slicer)
    world.abs_time.frame += 31
    world.tick(slicer)
    assert_slice_at_rest(slicer)


def test_slice_max_hold_in_gate_mode(world, slicer):
    slicer.par.Mode = 'gate'
    slicer.par.Maxhold = 0.1
    slicer.par.Trigger = 1.0
    world.tick(slicer, FPS * 2)
    assert_slice_at_rest(slicer)


# ------------------------------------------------------------ Kaleido Burst playing

@pytest.fixture
def kaleido(world):
    return world.block('kaleido_burst')


def front(block):
    return uniforms(block, 'kaleido', 0)[0]


def assert_kaleido_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert uniforms(block, 'kaleido', 0) == (-1.0, 0.01, 0.0, 0.0)
    assert uniforms(block, 'kaleido', 1) == (6.0, 1.0, 1.0, 0.0)
    assert uniforms(block, 'kaleido', 2) == (0.0, 0.0, 0.0, 0.0)


def fully_open(world, block, velocity=1.0):
    block.par.Trigger = velocity
    world.tick(block, FPS)


def test_kaleido_wiring(kaleido):
    assert kaleido.op('kaleido').inputs[0] is kaleido.op('in1')
    assert kaleido.op('switch').inputs == {0: kaleido.op('in1'), 1: kaleido.op('kaleido')}
    assert kaleido.op('out1').inputs[0] is kaleido.op('switch')


def test_kaleido_shader_uniforms_match_their_entries(kaleido):
    node = kaleido.op('kaleido')
    text = kaleido.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 3
    for index in range(3):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_kaleido_lands_on_the_very_first_frame(world, kaleido):
    kaleido.par.Trigger = 1.0
    world.tick(kaleido)
    assert kaleido.op('switch').par.index.val == 1
    assert front(kaleido) == pytest.approx(0.08)
    assert uniforms(kaleido, 'kaleido', 2)[3] == 1.0


def test_kaleido_front_clears_the_wall_and_holds(world, kaleido):
    fully_open(world, kaleido)
    _, lip, _, _ = uniforms(kaleido, 'kaleido', 0)
    assert front(kaleido) == pytest.approx(1.0 + lip * 2.5)
    world.tick(kaleido, FPS * 3)
    assert kaleido.op('switch').par.index.val == 1
    assert front(kaleido) == pytest.approx(1.0 + lip * 2.5)


def test_kaleido_velocity_scales_the_result(world, kaleido):
    kaleido.par.Trigger = 1.0
    world.tick(kaleido, 3)
    hard_front = front(kaleido)
    hard_segments = uniforms(kaleido, 'kaleido', 1)[0]
    hard_energy = uniforms(kaleido, 'kaleido', 0)[2]
    hard_prism = uniforms(kaleido, 'kaleido', 2)[2]
    kaleido.par.Trigger = 0.0
    world.tick(kaleido, FPS)
    assert_kaleido_at_rest(kaleido)
    kaleido.par.Trigger = 0.1
    world.tick(kaleido, 3)
    assert front(kaleido) < hard_front
    assert uniforms(kaleido, 'kaleido', 1)[0] < hard_segments
    assert uniforms(kaleido, 'kaleido', 0)[2] < hard_energy
    assert uniforms(kaleido, 'kaleido', 2)[2] < hard_prism


def test_kaleido_implosion_accelerates_and_flashes(world, kaleido):
    fully_open(world, kaleido)
    kaleido.par.Trigger = 0.0
    fronts = []
    cores = []
    for _ in range(int(FPS * 0.4) - 1):
        world.tick(kaleido)
        fronts.append(front(kaleido))
        cores.append(uniforms(kaleido, 'kaleido', 0)[3])
    steps = [a - b for a, b in zip(fronts, fronts[1:])]
    assert all(step > 0 for step in steps)
    assert steps[-1] > steps[0] * 3
    assert cores[0] == 0.0
    assert cores[-1] > 0.5


def test_kaleido_spin_winds_up_on_release(world, kaleido):
    fully_open(world, kaleido)
    before = uniforms(kaleido, 'kaleido', 1)[3]
    world.tick(kaleido)
    held_step = uniforms(kaleido, 'kaleido', 1)[3] - before
    kaleido.par.Trigger = 0.0
    world.tick(kaleido, int(FPS * 0.4) - 2)
    before = uniforms(kaleido, 'kaleido', 1)[3]
    world.tick(kaleido)
    released_step = (uniforms(kaleido, 'kaleido', 1)[3] - before) % (2.0 * 3.141592653589793)
    assert released_step > held_step * 3


def test_kaleido_press_during_implosion_catches_the_front(world, kaleido):
    fully_open(world, kaleido)
    kaleido.par.Trigger = 0.0
    world.tick(kaleido, int(FPS * 0.25))
    caught = front(kaleido)
    kaleido.par.Trigger = 1.0
    world.tick(kaleido)
    assert 0.08 < front(kaleido) == pytest.approx(caught, abs=0.08)
    world.tick(kaleido, 5)
    assert front(kaleido) > caught


def test_kaleido_controls_reach_the_shader(world, kaleido):
    kaleido.par.Segments = 0.0
    kaleido.par.Rings = 0.0
    kaleido.par.Mix = 0.5
    kaleido.par.Trigger = 0.5
    world.tick(kaleido)
    assert uniforms(kaleido, 'kaleido', 1)[:3] == (4, 1, 3)
    assert uniforms(kaleido, 'kaleido', 2)[3] == 0.5
    kaleido.par.Centers = 'one'
    kaleido.par.Rings = 1.0
    world.tick(kaleido)
    assert uniforms(kaleido, 'kaleido', 1)[1:3] == (5, 1)


def test_kaleido_returns_exactly_to_rest(world, kaleido):
    fully_open(world, kaleido)
    kaleido.par.Trigger = 0.0
    world.tick(kaleido, FPS)
    assert_kaleido_at_rest(kaleido)


def test_kaleido_starts_clean_after_returning_to_rest(world, kaleido):
    fully_open(world, kaleido)
    kaleido.par.Trigger = 0.0
    world.tick(kaleido, FPS)
    kaleido.par.Trigger = 1.0
    world.tick(kaleido)
    assert front(kaleido) == pytest.approx(0.08)
    assert uniforms(kaleido, 'kaleido', 1)[3] == 0.0
    assert uniforms(kaleido, 'kaleido', 2)[1] == 0.0


def test_kaleido_panic_and_held_pad_does_not_refire(world, kaleido):
    fully_open(world, kaleido)
    world.panic = 1.0
    world.tick(kaleido)
    assert_kaleido_at_rest(kaleido)
    world.panic = 0.0
    world.tick(kaleido, 3)
    assert_kaleido_at_rest(kaleido)


def test_kaleido_reset_and_frame_gap(world, kaleido):
    fully_open(world, kaleido)
    kaleido.op('reset_watch').module.onPulse()
    assert_kaleido_at_rest(kaleido)
    kaleido.par.Trigger = 0.0
    world.tick(kaleido, 2)
    kaleido.par.Trigger = 1.0
    world.tick(kaleido)
    world.abs_time.frame += 31
    world.tick(kaleido)
    assert_kaleido_at_rest(kaleido)


def test_kaleido_max_hold(world, kaleido):
    kaleido.par.Maxhold = 0.1
    kaleido.par.Trigger = 1.0
    world.tick(kaleido, FPS * 2)
    assert_kaleido_at_rest(kaleido)


# ------------------------------------------------------------ Halftone Hit playing

@pytest.fixture
def halftone(world):
    return world.block('halftone_hit')


def screen(block):
    return uniforms(block, 'halftone', 0)


def assert_halftone_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert screen(block) == (0.05, 0.0, 0.0, 1.0)
    assert uniforms(block, 'halftone', 1) == (0.5, 0.5, 0.0, 0.0)
    assert uniforms(block, 'halftone', 2) == (0.0, 0.0, 0.0, 0.0)


def finish_halftone(world, block):
    block.par.Trigger = 0.0
    world.tick(block, FPS * 2)


def test_halftone_wiring(halftone):
    assert halftone.op('halftone').inputs[0] is halftone.op('in1')
    assert halftone.op('switch').inputs == {0: halftone.op('in1'), 1: halftone.op('halftone')}
    assert halftone.op('out1').inputs[0] is halftone.op('switch')


def test_halftone_shader_uniforms_match_their_entries(halftone):
    node = halftone.op('halftone')
    text = halftone.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 3
    for index in range(3):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_halftone_lands_on_the_very_first_frame(world, halftone):
    halftone.par.Trigger = 1.0
    world.tick(halftone)
    assert halftone.op('switch').par.index.val == 1
    cell, split, _, swell = screen(halftone)
    assert cell > 0.05
    assert split > 0.0
    assert swell == 0.0
    assert uniforms(halftone, 'halftone', 1)[3] == 1.0


def test_halftone_velocity_changes_the_print(world, halftone):
    halftone.par.Trigger = 1.0
    world.tick(halftone)
    hard_cell, hard_split, _, _ = screen(halftone)
    finish_halftone(world, halftone)
    halftone.par.Trigger = 0.1
    world.tick(halftone)
    soft_cell, soft_split, _, _ = screen(halftone)
    assert soft_cell < hard_cell * 0.5
    assert soft_split < hard_split * 0.2


def test_halftone_swells_while_plates_snap_into_register(world, halftone):
    halftone.par.Trigger = 1.0
    world.tick(halftone, 4)
    halftone.par.Trigger = 0.0
    cells, splits, swells = [], [], []
    for _ in range(int(FPS * 0.4)):
        world.tick(halftone)
        cell, split, _, swell = screen(halftone)
        cells.append(cell)
        splits.append(split)
        swells.append(swell)
    assert len(set(cells)) == 1
    assert all(b >= a for a, b in zip(swells, swells[1:]))
    assert swells[-1] > 0.9
    assert all(b <= a for a, b in zip(splits, splits[1:]))
    middle = len(splits) // 2
    assert splits[middle] < splits[0] * (1.0 - swells[middle])


def test_halftone_each_press_turns_the_screens_even_after_a_rest(world, halftone):
    rolls = []
    for _ in range(3):
        halftone.par.Trigger = 1.0
        world.tick(halftone)
        rolls.append(screen(halftone)[2])
        finish_halftone(world, halftone)
        assert_halftone_at_rest(halftone)
    assert len(set(rolls)) == 3
    assert rolls[0] == 0.0


def test_halftone_retrigger_restarts_the_print_and_turns_it(world, halftone):
    halftone.par.Trigger = 1.0
    world.tick(halftone, 3)
    halftone.par.Trigger = 0.0
    world.tick(halftone, int(FPS * 0.25))
    first_roll = screen(halftone)[2]
    assert screen(halftone)[3] > 0.3
    halftone.par.Trigger = 1.0
    world.tick(halftone)
    assert screen(halftone)[3] == 0.0
    assert screen(halftone)[2] != first_roll


def test_halftone_bolts_use_bigger_cells(world, halftone):
    halftone.par.Trigger = 1.0
    world.tick(halftone)
    dot_cell = screen(halftone)[0]
    finish_halftone(world, halftone)
    halftone.par.Shape = 'bolt'
    halftone.par.Trigger = 1.0
    world.tick(halftone)
    assert screen(halftone)[0] == pytest.approx(dot_cell * 1.7)


def test_halftone_bolts_rock_but_never_spin_away(world, halftone):
    halftone.par.Shape = 'bolt'
    for _ in range(12):
        halftone.par.Trigger = 1.0
        world.tick(halftone)
        assert abs(screen(halftone)[2]) <= 0.28
        assert uniforms(halftone, 'halftone', 2)[0] == 2.0
        halftone.par.Trigger = 0.0
        world.tick(halftone)


def test_halftone_controls_reach_the_shader(world, halftone):
    halftone.par.Originx = 0.2
    halftone.par.Originy = 0.9
    halftone.par.Inkfloor = 0.3
    halftone.par.Mix = 0.5
    halftone.par.Shape = 'ring'
    halftone.par.Ink = 'paper'
    halftone.par.Split = 0.0
    halftone.par.Trigger = 1.0
    world.tick(halftone)
    assert uniforms(halftone, 'halftone', 1) == (0.2, 0.9, 0.3, 0.5)
    assert uniforms(halftone, 'halftone', 2)[:2] == (1.0, 1.0)
    assert screen(halftone)[1] == 0.0


def test_halftone_returns_exactly_to_rest(world, halftone):
    halftone.par.Trigger = 1.0
    world.tick(halftone, 5)
    finish_halftone(world, halftone)
    assert_halftone_at_rest(halftone)


def test_halftone_panic_and_held_pad_does_not_refire(world, halftone):
    halftone.par.Trigger = 1.0
    world.tick(halftone, 2)
    world.panic = 1.0
    world.tick(halftone)
    assert_halftone_at_rest(halftone)
    world.panic = 0.0
    world.tick(halftone, 3)
    assert_halftone_at_rest(halftone)


def test_halftone_reset_and_frame_gap(world, halftone):
    halftone.par.Trigger = 1.0
    world.tick(halftone, 2)
    halftone.op('reset_watch').module.onPulse()
    assert_halftone_at_rest(halftone)
    halftone.par.Trigger = 0.0
    world.tick(halftone, 2)
    halftone.par.Trigger = 1.0
    world.tick(halftone)
    world.abs_time.frame += 31
    world.tick(halftone)
    assert_halftone_at_rest(halftone)


def test_halftone_max_hold_in_gate_mode(world, halftone):
    halftone.par.Mode = 'gate'
    halftone.par.Maxhold = 0.1
    halftone.par.Trigger = 1.0
    world.tick(halftone, FPS * 2)
    assert_halftone_at_rest(halftone)


# ------------------------------------------------------------ Gradient Map playing

@pytest.fixture
def gradient(world):
    return world.block('gradient_map')


def tone_map(block):
    return uniforms(block, 'map', 0)


def assert_gradient_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert uniforms(block, 'map', 0) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'map', 1) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'bleed_h', 0) == (0.0, 0.0, 1.0, 0.0)
    assert uniforms(block, 'bleed_v', 0) == (0.0, 0.0, 0.0, 0.0)


def finish_gradient(world, block):
    block.par.Trigger = 0.0
    world.tick(block, int(FPS * 0.5))


def map_shader(block):
    return block.op(block.op('map').par.pixeldat.val).text


def test_gradient_wiring(gradient):
    assert gradient.op('smooth').inputs == {0: gradient.op('in1')}
    assert gradient.op('smooth').par.top.val == 'levels'
    assert gradient.op('levels').inputs == {0: gradient.op('in1'), 1: gradient.op('smooth')}
    assert gradient.op('bleed_h').inputs == {0: gradient.op('in1')}
    assert gradient.op('bleed_v').inputs == {0: gradient.op('bleed_h')}
    assert gradient.op('map').inputs == {0: gradient.op('in1'), 1: gradient.op('levels'), 2: gradient.op('bleed_v')}
    assert gradient.op('switch').inputs == {0: gradient.op('in1'), 1: gradient.op('map')}
    assert gradient.op('out1').inputs[0] is gradient.op('switch')


def test_gradient_levels_pass_is_one_pixel(gradient):
    for name in ('levels', 'smooth'):
        par = gradient.op(name).par
        assert (par.outputresolution.val, par.resolutionw.val, par.resolutionh.val) == ('custom', 1, 1)
        assert par.format.val == 'rgba32float'
    assert gradient.op('map').par.outputresolution.val == 'useinput'
    assert gradient.op('bleed_h').par.outputresolution.val == 'quarter'
    assert gradient.op('bleed_v').par.outputresolution.val == 'useinput'


def assert_uniforms_match_shaders(block):
    for node in block.children:
        if node.type != 'glslTOP':
            continue
        text = block.op(node.par.pixeldat.val).text
        declared = re.findall(r'^uniform vec\d (\w+);', text, re.M)
        entries = [getattr(node.par, 'vec%dname' % index).val for index in range(node.seq.vec.numBlocks)]
        entries = [entry for entry in entries if entry]
        assert entries == declared, node.name


def test_gradient_shader_uniforms_match_their_entries(gradient):
    assert_uniforms_match_shaders(gradient)


def test_gradient_palettes_match_shader_rows(gradient):
    labels = [
        'Viridis', 'Plasma', 'Inferno', 'Magma', 'Cividis', 'Turbo',
        'Spectral', 'RdBu', 'PuOr', 'PiYG', 'YlGnBu', 'YlOrRd',
    ]
    shader = map_shader(gradient)
    table = re.search(r'const vec3 STOPS.*?\(\n(.*?)\n\);', shader, re.S).group(1)
    rows = [
        [
            ''.join('%02x' % int(float(channel)) for channel in rgb.split(','))
            for rgb in re.findall(r'vec3\(([\d., ]+)\) / 255\.0', row)
        ]
        for row in table.splitlines()
    ]
    assert gradient.par.Palette.menuNames == [label.lower() for label in labels]
    assert gradient.par.Palette.menuLabels == labels
    assert gradient.par.Palette.default == 'viridis'
    assert f'const int PALETTES = {len(labels)};' in shader
    assert 'const int STOP_COUNT = 5;' in shader
    assert len(rows) == len(labels)
    assert all(len(set(row)) == len(row) == 5 for row in rows)
    assert rows[0] == ['440154', '3b528b', '21918c', '5ec962', 'fde725']
    assert rows[-1] == ['bd0026', 'f03b20', 'fd8d3c', 'fecc5c', 'ffffb2']


def test_gradient_lands_on_the_very_first_frame(world, gradient):
    gradient.par.Trigger = 1.0
    world.tick(gradient)
    assert gradient.op('switch').par.index.val == 1
    assert tone_map(gradient)[3] == 1.0
    assert uniforms(gradient, 'map', 1) == (1.0, 1.0, 1.0, 0.0)


def test_gradient_defaults_to_hard_bands_and_neutral_shift(world, gradient):
    gradient.par.Trigger = 1.0
    world.tick(gradient)
    assert tone_map(gradient)[:3] == (0.0, 0.0, 0.0)


def test_gradient_velocity_scales_the_reveal(world, gradient):
    gradient.par.Trigger = 0.1
    world.tick(gradient)
    assert tone_map(gradient)[3] == pytest.approx(0.46)
    finish_gradient(world, gradient)
    gradient.par.Trigger = 1.0
    world.tick(gradient)
    assert tone_map(gradient)[3] == 1.0


def test_gradient_holds_while_pressed_then_wipes_back(world, gradient):
    gradient.par.Trigger = 1.0
    world.tick(gradient, FPS)
    assert tone_map(gradient)[3] == 1.0
    gradient.par.Trigger = 0.0
    previous = 1.0
    for _ in range(4):
        world.tick(gradient)
        assert 0.0 < tone_map(gradient)[3] < previous
        previous = tone_map(gradient)[3]
    world.tick(gradient, FPS)
    assert_gradient_at_rest(gradient)


def test_gradient_controls_reach_the_shader(world, gradient):
    gradient.par.Palette = 'ylorrd'
    gradient.par.Softness = 0.4
    gradient.par.Shift = 0.75
    gradient.par.Autolevels = 0.25
    gradient.par.Mix = 0.5
    gradient.par.Bleed = 0.0
    gradient.par.Reveal = 'center'
    gradient.par.Trigger = 1.0
    world.tick(gradient)
    assert tone_map(gradient) == (11.0, 0.4, 0.25, 1.0)
    assert uniforms(gradient, 'map', 1) == (0.25, 0.5, 0.0, 3.0)
    assert uniforms(gradient, 'bleed_v', 0)[:2] == (0.0, 0.0)


def test_gradient_reveal_styles_match_the_shader(gradient):
    assert gradient.par.Reveal.menuNames == ['tone', 'left', 'right', 'center', 'dissolve']
    assert gradient.par.Reveal.val == 'tone'
    text = map_shader(gradient)
    for index in range(1, 5):
        assert 'style == %d' % index in text


def test_gradient_levels_measure_outright_on_a_press_then_ease(world, gradient):
    gradient.par.Trigger = 1.0
    world.tick(gradient)
    assert uniforms(gradient, 'levels', 0)[0] == 1.0
    world.tick(gradient)
    assert 0.0 < uniforms(gradient, 'levels', 0)[0] < 0.5
    gradient.par.Trigger = 0.0
    world.tick(gradient, 2)
    gradient.par.Trigger = 1.0
    world.tick(gradient)
    assert uniforms(gradient, 'levels', 0)[0] == 1.0


def test_gradient_bleed_swells_with_the_press_and_tightens_on_release(world, gradient):
    gradient.par.Bleed = 1.0
    gradient.par.Trigger = 0.3
    world.tick(gradient)
    soft = uniforms(gradient, 'bleed_v', 0)[1]
    finish_gradient(world, gradient)
    gradient.par.Trigger = 1.0
    world.tick(gradient)
    hard = uniforms(gradient, 'bleed_v', 0)[1]
    assert 0.0 < soft < hard
    # Horizontal steps are in UV, so they shrink by the wall's aspect to stay round.
    assert uniforms(gradient, 'bleed_h', 0)[0] == pytest.approx(hard * 2048 / 6720)
    assert uniforms(gradient, 'bleed_h', 0)[2] == 1.0
    assert uniforms(gradient, 'bleed_v', 0)[2] == 0.0
    gradient.par.Trigger = 0.0
    previous = hard
    for _ in range(4):
        world.tick(gradient)
        assert uniforms(gradient, 'bleed_v', 0)[1] < previous
        previous = uniforms(gradient, 'bleed_v', 0)[1]


def test_gradient_bleed_off_uses_the_sharp_brightness(world, gradient):
    gradient.par.Bleed = 0.0
    gradient.par.Trigger = 1.0
    world.tick(gradient)
    assert uniforms(gradient, 'map', 1)[2] == 0.0


def test_gradient_reset_clears_the_eased_levels(world, gradient):
    before = gradient.op('smooth').par.resetpulse.pulses
    gradient.op('reset_watch').module.onPulse()
    assert gradient.op('smooth').par.resetpulse.pulses == before + 1


def test_gradient_retrigger_restarts_the_reveal(world, gradient):
    gradient.par.Trigger = 1.0
    world.tick(gradient, 2)
    gradient.par.Trigger = 0.0
    world.tick(gradient, 4)
    assert tone_map(gradient)[3] < 1.0
    gradient.par.Trigger = 1.0
    world.tick(gradient)
    assert tone_map(gradient)[3] == 1.0


def test_gradient_panic_and_held_pad_does_not_refire(world, gradient):
    gradient.par.Trigger = 1.0
    world.tick(gradient, 2)
    world.panic = 1.0
    world.tick(gradient)
    assert_gradient_at_rest(gradient)
    world.panic = 0.0
    world.tick(gradient, 3)
    assert_gradient_at_rest(gradient)


def test_gradient_reset_and_frame_gap(world, gradient):
    gradient.par.Trigger = 1.0
    world.tick(gradient, 2)
    gradient.op('reset_watch').module.onPulse()
    assert_gradient_at_rest(gradient)
    gradient.par.Trigger = 0.0
    world.tick(gradient, 2)
    gradient.par.Trigger = 1.0
    world.tick(gradient)
    world.abs_time.frame += 31
    world.tick(gradient)
    assert_gradient_at_rest(gradient)


def test_gradient_max_hold(world, gradient):
    gradient.par.Maxhold = 0.1
    gradient.par.Trigger = 1.0
    world.tick(gradient, FPS * 2)
    assert_gradient_at_rest(gradient)


# ------------------------------------------------------------ Gradient Flow playing

@pytest.fixture
def flow(world):
    return world.block('gradient_flow')


def flow_rings(block):
    return uniforms(block, 'map', 0)


def flow_reach(block):
    return uniforms(block, 'map', 1)


def assert_flow_at_rest(block):
    assert block.op('switch').par.index.val == 0
    for index in range(3):
        assert uniforms(block, 'map', index) == (0.0, 0.0, 0.0, 0.0)


def finish_flow(world, block):
    block.par.Trigger = 0.0
    world.tick(block, FPS * 2)


def test_flow_wiring(flow):
    jumps = [flow.op('jump%d' % index) for index in range(12)]
    assert flow.op('jump12') is None
    assert flow.op('smooth').inputs == {0: flow.op('in1')}
    assert flow.op('smooth').par.top.val == 'levels'
    assert flow.op('levels').inputs == {0: flow.op('in1'), 1: flow.op('smooth')}
    assert flow.op('mask').inputs == {0: flow.op('in1'), 1: flow.op('levels')}
    assert flow.op('edge').inputs == {0: flow.op('mask')}
    previous = flow.op('edge')
    for index, jump in enumerate(jumps):
        assert jump.inputs == {0: previous}
        assert jump.par.vec0valuex.val == float(index)
        assert jump.par.format.val == 'rgba32float'
        previous = jump
    assert flow.op('map').inputs == {0: flow.op('in1'), 1: jumps[-1], 2: flow.op('levels')}
    assert flow.op('switch').inputs == {0: flow.op('in1'), 1: flow.op('map')}
    assert flow.op('out1').inputs[0] is flow.op('switch')


def test_flow_field_follows_detail_and_rings_draw_full_size(flow):
    assert flow.op('mask').par.outputresolution.eval() == 'quarter'
    flow.par.Detail = 'eighth'
    assert flow.op('mask').par.outputresolution.eval() == 'eighth'
    assert flow.par.Detail.menuNames == ['half', 'quarter', 'eighth']
    assert flow.op('edge').par.outputresolution.val == 'useinput'
    assert flow.op('map').par.outputresolution.val == 'useinput'
    assert flow.op('map').par.format.val == 'useinput'


def test_flow_jump_passes_cover_a_half_detail_field(flow):
    longest = 6720 // 2
    assert longest * 0.5 ** 12 < 1.0


def test_flow_shader_uniforms_match_their_entries(flow):
    assert_uniforms_match_shaders(flow)


def test_flow_palettes_match_gradient_map(flow, gradient):
    pattern = r'const vec3 STOPS.*?\);'
    flow_text = flow.op('map_shader').text
    assert re.search(pattern, flow_text, re.S).group(0) == re.search(pattern, map_shader(gradient), re.S).group(0)
    assert flow.par.Palette.menuNames == gradient.par.Palette.menuNames
    assert flow.par.Palette.menuLabels == gradient.par.Palette.menuLabels
    assert flow.par.Palette.default == gradient.par.Palette.default


def test_flow_default_attack_swells_in(world, flow):
    flow.par.Trigger = 1.0
    world.tick(flow)
    assert flow.op('switch').par.index.val == 1
    reaches = []
    for _ in range(6):
        world.tick(flow)
        reaches.append(flow_reach(flow)[0])
    assert reaches == sorted(reaches)
    assert reaches[0] < reaches[-1]
    assert flow_reach(flow)[2] == 1.0


def test_flow_lands_on_the_first_frame_with_no_attack(world, flow):
    flow.par.Attack = 0.0
    flow.par.Trigger = 1.0
    world.tick(flow)
    assert flow.op('switch').par.index.val == 1
    assert flow_reach(flow) == pytest.approx((0.3, 1.0, 1.0, 1.0))
    rings = flow_rings(flow)
    assert rings[0] == 0.0
    assert rings[1] == pytest.approx(1.0 + 15.0 * 0.33)
    assert rings[3] == 0.5
    assert uniforms(flow, 'map', 2)[0] == 0.5
    assert uniforms(flow, 'mask', 0)[0] == 0.5


def test_flow_velocity_scales_the_reach(world, flow):
    flow.par.Attack = 0.0
    flow.par.Velocitydepth = 1.0
    flow.par.Trigger = 0.4
    world.tick(flow)
    assert flow_reach(flow)[0] == pytest.approx(0.3 * 0.4)
    assert flow_reach(flow)[2] == 1.0


def test_flow_rings_travel_with_flow(world, flow):
    flow.par.Attack = 0.0
    flow.par.Trigger = 1.0
    world.tick(flow)
    start = flow_rings(flow)[2]
    world.tick(flow, 6)
    outward = flow_rings(flow)[2]
    assert outward - start == pytest.approx(0.3 * 1.5 * 6 / FPS)
    flow.par.Flow = 0.5
    world.tick(flow, 6)
    assert flow_rings(flow)[2] == pytest.approx(outward)
    flow.par.Flow = 0.0
    world.tick(flow)
    assert flow_rings(flow)[2] == pytest.approx((outward - 1.5 / FPS) % 1.0)


def test_flow_retrigger_keeps_the_pattern_and_reach_without_jumps(world, flow):
    flow.par.Trigger = 1.0
    world.tick(flow, 30)
    flow.par.Trigger = 0.0
    world.tick(flow, 6)
    before = (flow_rings(flow)[2], flow_reach(flow)[0])
    flow.par.Trigger = 1.0
    world.tick(flow)
    after = (flow_rings(flow)[2], flow_reach(flow)[0])
    assert after[0] == pytest.approx(before[0] + 0.3 * 1.5 / FPS)
    assert after[1] == pytest.approx(before[1], abs=0.02)


def test_flow_release_pulls_the_aura_back_then_fades_the_inside(world, flow):
    flow.par.Trigger = 1.0
    world.tick(flow, 30)
    flow.par.Trigger = 0.0
    previous = flow_reach(flow)
    for _ in range(FPS):
        world.tick(flow)
        current = flow_reach(flow)
        if flow.op('switch').par.index.val == 0:
            break
        assert current[0] <= previous[0]
        assert current[2] <= previous[2]
        assert previous[2] - current[2] < 0.2
        previous = current
    world.tick(flow, FPS)
    assert_flow_at_rest(flow)


def test_flow_levels_measure_outright_on_a_press_then_ease(world, flow):
    flow.par.Trigger = 1.0
    world.tick(flow)
    assert uniforms(flow, 'levels', 0)[0] == 1.0
    world.tick(flow)
    assert 0.0 < uniforms(flow, 'levels', 0)[0] < 0.5


def test_flow_controls_reach_the_shader(world, flow):
    flow.par.Attack = 0.0
    flow.par.Palette = 'ylorrd'
    flow.par.Rings = 1.0
    flow.par.Reach = 0.5
    flow.par.Softness = 0.2
    flow.par.Threshold = 0.7
    flow.par.Inside = 0.25
    flow.par.Mix = 0.5
    flow.par.Trigger = 1.0
    world.tick(flow)
    rings = flow_rings(flow)
    assert (rings[0], rings[1], rings[3]) == (11.0, 16.0, 0.2)
    assert flow_reach(flow) == pytest.approx((0.25, 0.25, 1.0, 0.5))
    assert uniforms(flow, 'map', 2)[0] == 0.7
    assert uniforms(flow, 'mask', 0)[0] == 0.7


def test_flow_returns_exactly_to_rest(world, flow):
    flow.par.Trigger = 1.0
    world.tick(flow, 10)
    finish_flow(world, flow)
    assert_flow_at_rest(flow)


def test_flow_panic_and_held_pad_does_not_refire(world, flow):
    flow.par.Trigger = 1.0
    world.tick(flow, 10)
    world.panic = 1.0
    world.tick(flow)
    assert_flow_at_rest(flow)
    world.panic = 0.0
    world.tick(flow, 3)
    assert_flow_at_rest(flow)


def test_flow_reset_clears_phase_and_levels(world, flow):
    flow.par.Trigger = 1.0
    world.tick(flow, 20)
    assert flow_rings(flow)[2] > 0.0
    before = flow.op('smooth').par.resetpulse.pulses
    flow.op('reset_watch').module.onPulse()
    assert flow.op('smooth').par.resetpulse.pulses == before + 1
    assert_flow_at_rest(flow)
    flow.par.Trigger = 0.0
    world.tick(flow, 2)
    flow.par.Attack = 0.0
    flow.par.Trigger = 1.0
    world.tick(flow)
    assert flow_rings(flow)[2] == 0.0


def test_flow_frame_gap_resets(world, flow):
    flow.par.Trigger = 1.0
    world.tick(flow, 5)
    world.abs_time.frame += 31
    world.tick(flow)
    assert_flow_at_rest(flow)


def test_flow_max_hold(world, flow):
    flow.par.Maxhold = 0.1
    flow.par.Trigger = 1.0
    world.tick(flow, FPS * 3)
    assert_flow_at_rest(flow)


# ------------------------------------------------------------ Gong Ring playing

GONG_RING = 'ring'


@pytest.fixture
def gong(world):
    return world.block('gong_ring')


def strikes(block):
    return [uniforms(block, GONG_RING, index) for index in range(4)]


def bands(block):
    return [uniforms(block, GONG_RING, index + 4) for index in range(4)]


def ringing(block):
    """(strike, band) for every occupied slot."""
    return [(s, b) for s, b in zip(strikes(block), bands(block)) if s[3] > 0.0]


def assert_gong_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert strikes(block) == [(0.5, 0.5, 0.0, 0.0)] * 4
    assert bands(block) == [(0.0, 0.0, 0.0, 0.0)] * 4
    assert uniforms(block, GONG_RING, 8) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, GONG_RING, 9) == (0.0, 0.0, 0.0, 0.0)


def test_gong_defaults_ring_for_two_bars(gong):
    assert gong.par.Release.val == 4.5
    assert gong.par.Release.max == 8.0
    assert gong.par.Mode.val == 'hit'
    assert gong.par.Nodelines.val == 0.0
    assert gong.par.Glint.val > 0.0


def test_gong_wiring(gong):
    assert gong.op('ring').inputs[0] is gong.op('in1')
    assert gong.op('switch').inputs == {0: gong.op('in1'), 1: gong.op('ring')}
    assert gong.op('out1').inputs[0] is gong.op('switch')


def test_gong_shader_uniforms_match_their_entries(gong):
    node = gong.op('ring')
    text = gong.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 10
    for index in range(10):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_gong_lands_on_the_very_first_frame(world, gong):
    hit(world, gong)
    assert gong.op('switch').par.index.val == 1
    (strike, band), = ringing(gong)
    assert strike[:3] == pytest.approx((0.5, 0.5, 0.0))
    assert band[0] == pytest.approx(0.9)  # the heave is at full strength at once
    assert band[3] == pytest.approx(0.9)  # and so is the impact bulge
    assert uniforms(gong, GONG_RING, 9)[0] == 1.0


def test_gong_energy_climbs_from_heave_to_shimmer(world, gong):
    hit(world, gong)
    frames = 270
    low, high = [], []
    for _ in range(frames - 2):
        world.tick(gong)
        band = ringing(gong)[0][1]
        low.append(band[0])
        high.append(band[2])
    assert high[0] < 0.02 * max(high)
    assert low == sorted(low, reverse=True)
    peak = high.index(max(high))
    assert 0.1 * frames < peak < 0.5 * frames  # the bloom comes well after the hit
    assert high[peak] > low[peak]


def test_gong_soft_hit_is_a_lower_note_not_just_quieter(world, gong):
    def peak_ratio(velocity):
        hit(world, gong, velocity)
        world.tick(gong, 80)
        band = ringing(gong)[0][1]
        ratio = band[2] / band[0]
        world.tick(gong, 300)
        assert_gong_at_rest(gong)
        return ratio

    assert peak_ratio(0.3) < peak_ratio(1.0) * 0.5


def test_gong_velocity_sets_strength_and_reach(world, gong):
    hit(world, gong, 1.0)
    (hard_strike, hard_band), = ringing(gong)
    world.tick(gong, 300)
    hit(world, gong, 0.5)
    (soft_strike, soft_band), = ringing(gong)
    assert soft_band[0] == pytest.approx(hard_band[0] * 0.5)
    assert soft_strike[3] < hard_strike[3]


def test_gong_rings_spread_after_the_hit(world, gong):
    hit(world, gong)
    first = ringing(gong)[0][0][3]
    world.tick(gong, 40)
    assert first > 0.0
    assert ringing(gong)[0][0][3] > first * 2.5


def test_gong_rings_for_the_full_release_then_rests_exactly(world, gong):
    hit(world, gong)
    world.tick(gong, 265)
    assert len(ringing(gong)) == 1
    world.tick(gong, 10)
    assert_gong_at_rest(gong)


def test_gong_strikes_stack_and_older_ones_keep_ringing(world, gong):
    hit(world, gong)
    world.tick(gong, 10)
    hit(world, gong)
    ages = sorted(s[2] for s, _ in ringing(gong))
    assert ages == pytest.approx([0.0, 11 / FPS])


def test_gong_every_strike_finishes_before_idle(world, gong):
    hit(world, gong)
    world.tick(gong, 120)
    hit(world, gong)
    world.tick(gong, 268)
    assert gong.op('switch').par.index.val == 1
    world.tick(gong, 10)
    assert_gong_at_rest(gong)


def test_gong_fifth_strike_replaces_only_the_oldest(world, gong):
    for _ in range(4):
        hit(world, gong)
        world.tick(gong, 3)
    before = sorted(s[2] for s, _ in ringing(gong))
    hit(world, gong)
    after = sorted(s[2] for s, _ in ringing(gong))
    assert len(after) == 4
    assert after[0] == 0.0
    assert after[-1] < before[-1]


def test_gong_retrigger_ignore_strikes_nothing(world, gong):
    gong.par.Retrigger = 'ignore'
    hit(world, gong)
    world.tick(gong, 5)
    hit(world, gong)
    assert len(ringing(gong)) == 1


def test_gong_each_strike_keeps_its_strike_point(world, gong):
    gong.par.Strikex = 0.1
    gong.par.Strikey = 0.8
    hit(world, gong)
    gong.par.Strikex = 0.9
    world.tick(gong, 2)
    assert [s[:2] for s, _ in ringing(gong)] == [(0.1, 0.8)]
    hit(world, gong)
    assert sorted(s[0] for s, _ in ringing(gong)) == [0.1, 0.9]


def test_gong_damp_chokes_within_a_tenth_of_a_second(world, gong):
    hit(world, gong)
    world.tick(gong, 2)
    before = ringing(gong)[0][1][0]
    gong.par.Damp = 1.0
    world.tick(gong, 6)
    assert ringing(gong)[0][1][0] < before * 0.05
    world.tick(gong, 12)
    assert ringing(gong) == []


def test_gong_soft_damp_chokes_slower(world, gong):
    def after_damp(amount):
        hit(world, gong)
        gong.par.Damp = amount
        world.tick(gong, 6)
        level = ringing(gong)[0][1][0]
        gong.par.Damp = 0.0
        world.tick(gong, 300)
        return level

    assert after_damp(1.0) < after_damp(0.2)


def test_gong_look_controls_reach_the_shader(world, gong):
    gong.par.Dents = 0.2
    gong.par.Chroma = 1.0
    gong.par.Glint = 0.6
    gong.par.Nodelines = 0.5
    gong.par.Sheen = 0.3
    gong.par.Mix = 0.5
    hit(world, gong)
    assert uniforms(gong, GONG_RING, 8) == pytest.approx((0.2, 0.3, 0.6, 0.77))
    assert uniforms(gong, GONG_RING, 9)[:3] == pytest.approx((0.5, 7.0, 0.3))


def test_gong_starts_clean_after_returning_to_rest(world, gong):
    hit(world, gong)
    world.tick(gong, 300)
    hit(world, gong)
    assert len(ringing(gong)) == 1


def test_gong_panic_and_held_pad_does_not_refire(world, gong):
    gong.par.Trigger = 1.0
    world.tick(gong)
    world.panic = 1.0
    world.tick(gong)
    assert_gong_at_rest(gong)
    world.panic = 0.0
    world.tick(gong, 3)
    assert_gong_at_rest(gong)


def test_gong_reset_and_frame_gap(world, gong):
    hit(world, gong)
    gong.op('reset_watch').module.onPulse()
    assert_gong_at_rest(gong)
    world.tick(gong, 2)
    hit(world, gong)
    world.abs_time.frame += 31
    world.tick(gong)
    assert_gong_at_rest(gong)


def test_gong_max_hold_in_gate_mode(world, gong):
    gong.par.Mode = 'gate'
    gong.par.Maxhold = 0.1
    gong.par.Release = 0.1
    gong.par.Trigger = 1.0
    world.tick(gong, FPS * 2)
    assert_gong_at_rest(gong)


def line_opacity(block):
    return uniforms(block, GONG_RING, 9)[3]


def test_gong_node_lines_fade_in_after_the_hit(world, gong):
    gong.par.Nodelines = 0.4
    hit(world, gong)
    seen = [line_opacity(gong)]
    for _ in range(21):
        world.tick(gong)
        seen.append(line_opacity(gong))
    assert seen[0] == 0.0
    assert seen == sorted(seen)
    assert max(b - a for a, b in zip(seen, seen[1:])) < 0.06
    assert seen[-1] == pytest.approx(1.0)


def test_gong_node_lines_fade_out_before_the_ring_ends(world, gong):
    gong.par.Nodelines = 0.4
    hit(world, gong)
    world.tick(gong, 150)
    assert line_opacity(gong) == pytest.approx(1.0)
    seen = []
    for _ in range(118):
        world.tick(gong)
        seen.append(line_opacity(gong))
    assert seen == sorted(seen, reverse=True)
    assert max(a - b for a, b in zip(seen, seen[1:])) < 0.02
    assert seen[-1] < 0.02


def test_gong_new_strike_does_not_dip_the_node_lines(world, gong):
    gong.par.Nodelines = 0.4
    hit(world, gong)
    world.tick(gong, 30)
    before = line_opacity(gong)
    hit(world, gong)
    assert line_opacity(gong) == pytest.approx(before)


def test_gong_damp_fades_the_node_lines(world, gong):
    gong.par.Nodelines = 0.4
    hit(world, gong)
    world.tick(gong, 30)
    before = line_opacity(gong)
    gong.par.Damp = 1.0
    world.tick(gong)
    assert 0.0 < line_opacity(gong) < before


def test_gong_node_lines_knob_fades_up_from_zero(world, gong):
    gong.par.Nodelines = 0.05
    hit(world, gong)
    world.tick(gong, 30)
    assert line_opacity(gong) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Line Work
# ---------------------------------------------------------------------------

LINE_INK = 'ink'


@pytest.fixture
def linework(world):
    world.run_build(['line_work'])
    block = world.block('line_work')
    world.tick(block)
    return block


def waves(block):
    return [uniforms(block, LINE_INK, slot) for slot in range(4)]


def flying(block):
    return [wave for wave in waves(block) if wave[2] > 0.0]


def wave_colors(block):
    return [uniforms(block, LINE_INK, slot + 4) for slot in range(4)]


def press_line(world, block, velocity=1.0):
    block.par.Trigger = velocity
    world.tick(block)
    block.par.Trigger = 0.0


def assert_linework_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert waves(block) == [(0.0, 0.0, 0.0, 0.0)] * 4
    assert wave_colors(block) == [(0.0, 0.0, 0.0, 0.0)] * 4
    assert uniforms(block, LINE_INK, 9)[0] == 0.0


def test_linework_wiring(linework):
    block = linework
    assert block.op('backdrop').inputs[0] is block.op('in1')
    assert block.op('shape').inputs == {0: block.op('in1'), 1: block.op('backdrop')}
    assert block.op('flood0').inputs[0] is block.op('shape')
    assert block.op('distance').inputs[0] is block.op('flood10')
    assert block.op('still').inputs[0] is block.op('in1')
    assert block.op('ink').type == 'glslmultiTOP'
    assert block.op('ink').inputs[0] is block.op('in1')
    assert block.op('ink').inputs[1] is block.op('still')
    for slot in range(4):
        assert block.op('wave%d' % slot).inputs[0] is block.op('distance')
        assert block.op('ink').inputs[2 + slot] is block.op('wave%d' % slot)
    assert block.op('switch').inputs == {0: block.op('in1'), 1: block.op('ink')}
    assert block.op('out1').inputs[0] is block.op('switch')
    assert block.saved_to.endswith('line_work.tox')


def test_linework_flood_steps_halve_each_pass(linework):
    steps = [linework.op('flood%d' % i).par.vec0valuex.val for i in range(11)]
    assert steps == [2.0 ** (i + 1) for i in range(11)]


def test_linework_shader_uniforms_match_their_entries(linework):
    for node, dat in [('backdrop', 'backdrop_shader'), ('shape', 'shape_shader'),
                      ('flood0', 'flood_shader'), ('ink', 'ink_shader')]:
        declared = re.findall(r'uniform\s+vec4\s+(\w+)', linework.op(dat).text)
        par = linework.op(node).par
        assert [getattr(par, 'vec%dname' % i).val for i in range(len(declared))] == declared


def test_linework_caches_only_grab_on_a_hit(world, linework):
    still = linework.op('still')
    assert still.par.active.val is False and still.par.cachesize.val == 1
    world.tick(linework, 5)
    assert still.par.activepulse.pulses == 0
    press_line(world, linework)
    assert still.par.activepulse.pulses == 1
    assert linework.op('wave0').par.activepulse.pulses == 1
    assert linework.op('wave1').par.activepulse.pulses == 0
    world.tick(linework, 5)
    assert still.par.activepulse.pulses == 1


def test_linework_takes_over_on_the_very_first_frame(world, linework):
    press_line(world, linework)
    assert linework.op('switch').par.index.val == 1
    assert len(flying(linework)) == 1
    assert flying(linework)[0][0] == 0.0
    assert wave_colors(linework)[0][:3] == pytest.approx((0.1451, 0.4510, 1.0))


def test_linework_velocity_sets_the_line_count(world, linework):
    press_line(world, linework, 1.0)
    hard = flying(linework)[0][2]
    world.tick(linework, 200)
    press_line(world, linework, 0.1)
    soft = flying(linework)[0][2]
    assert soft >= 3
    assert hard > soft * 2


def test_linework_flight_runs_forward_to_the_release_length(world, linework):
    press_line(world, linework)
    seen = []
    for _ in range(115):
        world.tick(linework)
        seen.append(flying(linework)[0][0])
    assert seen == sorted(seen)
    assert seen[-1] > 0.9
    world.tick(linework, 10)
    assert_linework_at_rest(linework)


def test_linework_colors_cycle_and_never_repeat(world, linework):
    seen = []
    for slot in range(6):
        press_line(world, linework)
        seen.append(wave_colors(linework)[slot % 4])
        world.tick(linework, 3)
    assert all(a != b for a, b in zip(seen, seen[1:]))
    assert len(set(seen[:5])) == 5


def test_linework_cycle_carries_on_after_idle(world, linework):
    press_line(world, linework)
    first = wave_colors(linework)[0]
    world.tick(linework, 200)
    assert_linework_at_rest(linework)
    press_line(world, linework)
    assert wave_colors(linework)[0] != first


def test_linework_locked_color_sticks(world, linework):
    linework.par.Color = 'red'
    for _ in range(3):
        press_line(world, linework)
        world.tick(linework, 200)
    press_line(world, linework)
    assert wave_colors(linework)[0][:3] == pytest.approx((1.0, 0.2588, 0.3020))


def test_linework_black_background_gets_white_ink(world, linework):
    linework.par.Color = 'black'
    press_line(world, linework)
    assert wave_colors(linework)[0] == (0.0, 0.0, 0.0, 1.0)


def test_linework_fast_hits_stack_and_older_waves_keep_flying(world, linework):
    press_line(world, linework)
    world.tick(linework, 20)
    older = flying(linework)[0][0]
    press_line(world, linework)
    assert len(flying(linework)) == 2
    world.tick(linework, 3)
    assert waves(linework)[0][0] > older
    assert uniforms(linework, LINE_INK, 9)[2] == 1


def test_linework_fifth_hit_replaces_only_the_oldest(world, linework):
    for _ in range(5):
        press_line(world, linework)
        world.tick(linework, 3)
    assert len(flying(linework)) == 4
    assert uniforms(linework, LINE_INK, 9)[2] == 0
    assert linework.op('wave0').par.activepulse.pulses == 2
    assert linework.op('wave1').par.activepulse.pulses == 1


def test_linework_retrigger_ignore_launches_nothing(world, linework):
    linework.par.Retrigger = 'ignore'
    press_line(world, linework)
    world.tick(linework, 5)
    press_line(world, linework)
    assert len(flying(linework)) == 1
    assert linework.op('wave1').par.activepulse.pulses == 0


def test_linework_hold_waits_then_a_new_hit_releases_the_old_wave(world, linework):
    linework.par.Hold = 1.0
    press_line(world, linework)
    world.tick(linework, 10)
    assert flying(linework)[0][0] == 0.0
    press_line(world, linework)
    world.tick(linework, 10)
    assert waves(linework)[0][0] > 0.0
    assert waves(linework)[1][0] == 0.0


def test_linework_gate_pumps_while_held_then_flies(world, linework):
    linework.par.Mode = 'gate'
    linework.par.Trigger = 1.0
    world.tick(linework, 60)
    wave = flying(linework)[0]
    assert wave[0] == 0.0
    assert wave[1] > 0.0
    pumped = wave[1]
    linework.par.Trigger = 0.0
    world.tick(linework, 30)
    wave = flying(linework)[0]
    assert wave[0] > 0.0
    assert wave[1] == pytest.approx(pumped, abs=0.05)
    world.tick(linework, 150)
    assert_linework_at_rest(linework)


def test_linework_hit_mode_does_not_pump(world, linework):
    linework.par.Hold = 1.0
    press_line(world, linework)
    world.tick(linework, 20)
    assert flying(linework)[0][1] == 0.0


def test_linework_max_hold_releases_a_held_gate(world, linework):
    linework.par.Mode = 'gate'
    linework.par.Maxhold = 0.5
    linework.par.Trigger = 1.0
    world.tick(linework, 45)
    assert flying(linework)[0][0] > 0.0


def test_linework_knobs_reach_the_shaders(world, linework):
    linework.par.Spacing = 1.0
    linework.par.Weight = 1.0
    linework.par.Wobble = 0.8
    linework.par.Mix = 0.5
    linework.par.Halftone = 0.25
    linework.par.Threshold = 1.0
    linework.par.Simplify = 1.0
    press_line(world, linework)
    look = uniforms(linework, LINE_INK, 8)
    assert look[:3] == pytest.approx((0.2, 0.7, 0.8))
    assert uniforms(linework, LINE_INK, 9)[:2] == (0.5, 0.25)
    assert uniforms(linework, 'backdrop')[0] == pytest.approx(0.65)
    assert uniforms(linework, 'shape')[:2] == pytest.approx((0.65, 12.0))


def test_linework_boil_steps_and_can_be_stopped(world, linework):
    linework.par.Mode = 'gate'
    linework.par.Trigger = 1.0
    seen = set()
    for _ in range(30):
        world.tick(linework)
        seen.add(uniforms(linework, LINE_INK, 8)[3])
    assert 3 <= len(seen) <= 12
    linework.par.Boil = 0.0
    world.tick(linework)
    assert uniforms(linework, LINE_INK, 8)[3] == 0.0


def test_linework_panic_and_held_pad_does_not_refire(world, linework):
    linework.par.Mode = 'gate'
    linework.par.Trigger = 1.0
    world.tick(linework, 5)
    world.panic = 1.0
    world.tick(linework)
    assert_linework_at_rest(linework)
    world.panic = 0.0
    world.tick(linework, 5)
    assert_linework_at_rest(linework)


def test_linework_reset_clears_waves_and_caches(world, linework):
    press_line(world, linework)
    before = linework.op('wave0').par.resetpulse.pulses
    linework.op('engine').module.reset()
    assert_linework_at_rest(linework)
    assert linework.op('wave0').par.resetpulse.pulses == before + 1
    assert linework.op('still').par.resetpulse.pulses >= 1


def test_linework_frame_gap_resets(world, linework):
    press_line(world, linework)
    world.abs_time.frame += 1000
    world.tick(linework)
    assert_linework_at_rest(linework)


# ------------------------------------------------------------ Color Block Slam playing

SLAM_SLOTS = 24
SLAM_MOTION, SLAM_CUT, SLAM_LOOK = 0, 1, 2
SLAM_BLUE = 0.0
SLAM_CHROMATICS = {0.0, 1.0, 2.0, 3.0, 4.0}
SLAM_LANDED_FRAMES = 10


@pytest.fixture
def slam(world):
    return world.block('color_block_slam')


def slam_blocks(block):
    """Live slots as (rect, color index, showing)."""
    shaped = uniforms(block, 'slam', SLAM_CUT)[0]
    found = []
    for slot in range(SLAM_SLOTS):
        rect = uniforms(block, 'layout', slot)
        if rect != (0.0, 0.0, 0.0, 0.0):
            data = uniforms(block, 'layout', SLAM_SLOTS + slot)
            found.append((rect, data[0], 1.0 if shaped < data[1] else 0.0))
    return found


def rect_area(rect):
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


def rects_touch(a, b):
    if a[2] == b[0] or b[2] == a[0]:
        return min(a[3], b[3]) > max(a[1], b[1])
    if a[3] == b[1] or b[3] == a[1]:
        return min(a[2], b[2]) > max(a[0], b[0])
    return False


def assert_slam_layout_is_legal(block):
    blocks = slam_blocks(block)
    assert sum(rect_area(r) for r, _, _ in blocks) == pytest.approx(1.0)
    for index, (rect, color, _) in enumerate(blocks):
        assert color in SLAM_CHROMATICS
        for other, other_color, _ in blocks[index + 1:]:
            if rects_touch(rect, other):
                assert color != other_color


def assert_slam_at_rest(block):
    assert block.op('switch').par.index.val == 0
    for entry in range(SLAM_SLOTS * 2):
        assert uniforms(block, 'layout', entry) == (0.0, 0.0, 0.0, 0.0)
    for entry in range(SLAM_LOOK + 1):
        assert uniforms(block, 'slam', entry) == (0.0, 0.0, 0.0, 0.0)


def test_slam_wiring(slam):
    assert slam.op('field').inputs[0] is slam.op('in1')
    assert slam.op('slam').inputs == {0: slam.op('in1'), 1: slam.op('field'), 2: slam.op('layout')}
    assert slam.op('layout').inputs == {}, 'anything feeding the layout pass makes it cook every frame'
    assert slam.op('switch').inputs == {0: slam.op('in1'), 1: slam.op('slam')}
    assert slam.op('out1').inputs[0] is slam.op('switch')


@pytest.mark.parametrize('name, count', [('slam', SLAM_LOOK + 1), ('layout', SLAM_SLOTS * 2)])
def test_slam_shader_uniforms_match_their_entries(slam, name, count):
    node = slam.op(name)
    text = slam.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == count
    assert '@@' not in text
    for index in range(count):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec4 %s;' % uniform, text), uniform


def test_slam_palette_is_the_brand_chromatics_in_index_order(slam):
    text = slam.op('slam_shader').text
    found = re.findall(r'vec3\(([\d., ]+)\),?\s+// (blue|pink|yellow|red|cyan)', text)
    assert [name for _, name in found] == ['blue', 'pink', 'yellow', 'red', 'cyan']
    hexes = {'blue': '2573FF', 'pink': 'FF8DFF', 'yellow': 'F3DD3E', 'red': 'FF424D', 'cyan': '44F8FF'}
    for values, name in found:
        rgb = [round(float(v) * 255) for v in values.split(',')]
        assert '%02X%02X%02X' % tuple(rgb) == hexes[name]


def test_slam_field_size_matches_its_shader(slam):
    node = slam.op('field')
    text = slam.op(node.par.pixeldat.val).text
    assert 'vec2(1.0 / %d.0, 1.0 / %d.0)' % (node.par.resolutionw.val, node.par.resolutionh.val) in text


def test_slam_lands_on_the_very_first_frame(world, slam):
    hit(world, slam)
    assert slam.op('switch').par.index.val == 1
    blocks = slam_blocks(slam)
    assert len(blocks) == 20
    assert all(showing == 1.0 for _, _, showing in blocks)
    assert_slam_layout_is_legal(slam)


def test_slam_velocity_sets_the_block_count(world, slam):
    hit(world, slam, 1.0)
    assert len(slam_blocks(slam)) == 20
    world.tick(slam, 2 * FPS)
    hit(world, slam, 0.01)
    assert len(slam_blocks(slam)) == 3


def test_slam_most_blocks_knob_reaches_the_slot_limit(world, slam):
    slam.par.Blocks = 1.0
    hit(world, slam)
    assert len(slam_blocks(slam)) == SLAM_SLOTS


def test_slam_neighbor_rule_holds_on_every_frame_of_many_hits(world, slam):
    slam.par.Blocks = 1.0
    slam.par.Spring = 1.0
    for _ in range(150):
        hit(world, slam)
        assert_slam_layout_is_legal(slam)
        for _ in range(SLAM_LANDED_FRAMES):
            world.tick(slam)
            assert_slam_layout_is_legal(slam)


def test_slam_first_cut_is_vertical_for_the_wide_wall(world, slam):
    # Three blocks on a 3.3:1 wall: a full-width block would be a horizontal first cut.
    for _ in range(50):
        hit(world, slam, 0.01)
        assert all(r[2] - r[0] < 1.0 for r, _, _ in slam_blocks(slam))
        world.tick(slam, 2 * FPS)


def test_slam_biggest_block_is_blue(world, slam):
    for _ in range(40):
        hit(world, slam)
        world.tick(slam, SLAM_LANDED_FRAMES)
        biggest = max(slam_blocks(slam), key=lambda b: rect_area(b[0]))
        assert biggest[1] == SLAM_BLUE


def test_slam_every_hit_gets_a_new_layout(world, slam):
    seen = set()
    for _ in range(10):
        hit(world, slam)
        world.tick(slam, SLAM_LANDED_FRAMES)
        seen.add(tuple(r for r, _, _ in slam_blocks(slam)))
    assert len(seen) == 10


def color_at(blocks, x, y):
    for rect, color, _ in blocks:
        if rect[0] <= x < rect[2] and rect[1] <= y < rect[3]:
            return color
    return None


def test_slam_repeat_hits_change_most_of_the_wall(world, slam):
    probes = [((i + 0.5) / 24, (j + 0.5) / 8) for i in range(24) for j in range(8)]
    same = total = 0
    hit(world, slam)
    world.tick(slam, SLAM_LANDED_FRAMES)
    before = slam_blocks(slam)
    for _ in range(60):
        hit(world, slam)
        world.tick(slam, SLAM_LANDED_FRAMES)
        after = slam_blocks(slam)
        same += sum(color_at(before, *p) == color_at(after, *p) for p in probes)
        total += len(probes)
        before = after
    assert same / total < 0.12


def test_slam_layout_is_fixed_during_a_hit(world, slam):
    hit(world, slam)
    world.tick(slam, SLAM_LANDED_FRAMES)
    first = slam_blocks(slam)
    world.tick(slam, 1)
    assert slam_blocks(slam) == first


def test_slam_seams_spring_then_land_exactly(world, slam):
    slam.par.Spring = 1.0
    hit(world, slam)
    start = uniforms(slam, 'slam', SLAM_MOTION)
    assert abs(start[0]) == pytest.approx(0.9)
    assert abs(start[1]) == pytest.approx(0.9)
    signs = set()
    for _ in range(SLAM_LANDED_FRAMES):
        world.tick(slam)
        motion_now = uniforms(slam, 'slam', SLAM_MOTION)
        signs.add(motion_now[0] > 0)
        assert abs(motion_now[0]) < 1.0 and abs(motion_now[1]) < 1.0, 'seams would cross'
    assert signs == {True, False}, 'never swung past home'
    assert uniforms(slam, 'slam', SLAM_MOTION)[:2] == (0.0, 0.0)


def test_slam_spring_off_never_moves(world, slam):
    slam.par.Spring = 0.0
    hit(world, slam)
    for _ in range(SLAM_LANDED_FRAMES):
        assert uniforms(slam, 'slam', SLAM_MOTION)[:2] == (0.0, 0.0)
        world.tick(slam)


def test_slam_layout_is_only_drawn_once_per_hit(world, slam):
    layout = slam.op('layout')
    writes = []
    original = type(layout.par).__setattr__

    def counting(pars, name, value):
        if pars is layout.par:
            writes.append(name)
        original(pars, name, value)

    type(layout.par).__setattr__ = counting
    try:
        hit(world, slam)
        first_frame = len(writes)
        assert first_frame > 0
        slam.par.Hold = 0.0
        while slam.op('switch').par.index.val == 1 and uniforms(slam, 'slam', SLAM_CUT)[0] < 0.9:
            world.tick(slam)
        assert len(writes) == first_frame, 'landing and cut-outs must not redraw the layout'
    finally:
        type(layout.par).__setattr__ = original


def test_slam_blocks_cut_smallest_first_and_biggest_stands_alone(world, slam):
    slam.par.Hold = 0.0
    hit(world, slam)
    world.tick(slam, SLAM_LANDED_FRAMES)  # into the release, after landing
    alone_frames = 0
    last_seen = {}
    frame = 0
    while slam.op('switch').par.index.val == 1:
        blocks = slam_blocks(slam)
        assert all(showing in (0.0, 1.0) for _, _, showing in blocks)
        showing = [b for b in blocks if b[2] == 1.0]
        assert showing, 'the wall emptied before the block returned to idle'
        alone_frames += len(showing) == 1
        for rect, _, shown in blocks:
            if shown == 1.0:
                last_seen[rect] = frame
        frame += 1
        world.tick(slam)
    ordered = sorted(last_seen, key=rect_area)
    assert [last_seen[r] for r in ordered] == sorted(last_seen[r] for r in ordered)
    assert alone_frames >= 6


def test_slam_returns_exactly_to_rest(world, slam):
    hit(world, slam)
    world.tick(slam, 2 * FPS)
    assert_slam_at_rest(slam)


def test_slam_retrigger_restart_slams_a_fresh_full_wall(world, slam):
    slam.par.Hold = 0.0
    hit(world, slam)
    world.tick(slam, 20)
    old = [r for r, _, _ in slam_blocks(slam)]
    hit(world, slam)
    blocks = slam_blocks(slam)
    assert [r for r, _, _ in blocks] != old
    assert all(showing == 1.0 for _, _, showing in blocks)


def test_slam_retrigger_ignore_keeps_the_layout(world, slam):
    slam.par.Retrigger = 'ignore'
    hit(world, slam)
    world.tick(slam, 2)
    before = slam_blocks(slam)[0][1]
    seed = uniforms(slam, 'slam', SLAM_LOOK)[0]
    hit(world, slam)
    assert uniforms(slam, 'slam', SLAM_LOOK)[0] == seed
    assert slam_blocks(slam)[0][1] == before


def test_slam_look_controls_reach_the_shader(world, slam):
    slam.par.Scatter = 0.5
    slam.par.Ink = 1.0
    slam.par.Mix = 0.25
    hit(world, slam)
    assert uniforms(slam, 'slam', SLAM_LOOK)[1:] == pytest.approx((0.5, 0.2, 0.25))


def test_slam_scatter_defaults_to_one_continuous_picture(world, slam):
    hit(world, slam)
    assert uniforms(slam, 'slam', SLAM_LOOK)[1] == 0.0


def test_slam_gate_holds_and_max_hold_releases(world, slam):
    slam.par.Mode = 'gate'
    slam.par.Maxhold = 0.5
    slam.par.Trigger = 1.0
    world.tick(slam, 20)
    assert all(showing == 1.0 for _, _, showing in slam_blocks(slam))
    world.tick(slam, 2 * FPS)
    assert_slam_at_rest(slam)


def test_slam_panic_and_held_pad_does_not_refire(world, slam):
    slam.par.Mode = 'gate'
    slam.par.Trigger = 1.0
    world.tick(slam, 5)
    world.panic = 1.0
    world.tick(slam)
    assert_slam_at_rest(slam)
    world.panic = 0.0
    world.tick(slam, 5)
    assert_slam_at_rest(slam)


def test_slam_reset_clears_state(world, slam):
    hit(world, slam)
    slam.op('engine').module.reset()
    assert_slam_at_rest(slam)
    world.tick(slam)  # the pad comes up, so the next press is a new one
    hit(world, slam)
    assert len(slam_blocks(slam)) == 20


def test_slam_frame_gap_resets(world, slam):
    hit(world, slam)
    world.abs_time.frame += 1000
    world.tick(slam)
    assert_slam_at_rest(slam)


# ------------------------------------------------------------ Thunderclap playing

CLAP_SLOTS, CLAP_POINTS = 3, 15
CLAP_TRUNK_MAX = 9
CLAP_ASPECT = 6720 / 2048
CLAP_LOOK, CLAP_INK, CLAP_SLIP = 0, 1, 2
CLAP_FLIP, CLAP_DARK, CLAP_ZOOM, CLAP_MIX = 0, 1, 2, 3
CLAP_MAX_TURN = 125.0


@pytest.fixture
def clap(world):
    return world.block('thunderclap')


def clap_lines(block, slot):
    """The slot's polylines, each a list of (x, y, half width)."""
    lines = []
    for index in range(CLAP_POINTS):
        point = uniforms(block, 'bolts', slot * CLAP_POINTS + index)
        if point == (0.0, 0.0, 0.0, 0.0):
            continue
        if point[3] == 0.0:
            lines.append([])
        lines[-1].append(point[:3])
    return lines


def clap_bolt_count(block):
    return sum(1 for slot in range(CLAP_SLOTS) if clap_lines(block, slot))


def clap_look(block):
    return uniforms(block, 'clap', CLAP_LOOK)


def clap_trunk_width(block, slot=0):
    return max(point[2] for point in clap_lines(block, slot)[0])


def assert_clap_at_rest(block):
    assert block.op('switch').par.index.val == 0
    for entry in range(CLAP_SLOTS * CLAP_POINTS + 1):
        assert uniforms(block, 'bolts', entry) == (0.0, 0.0, 0.0, 0.0)
    for entry in range(CLAP_SLIP + 1):
        assert uniforms(block, 'clap', entry) == (0.0, 0.0, 0.0, 0.0)


def clap_run_to_idle(world, block):
    world.tick(block, 2 * FPS)


def test_clap_wiring(clap):
    assert clap.op('clap').inputs == {0: clap.op('in1'), 1: clap.op('bolts')}
    assert clap.op('bolts').inputs == {}, 'anything feeding the bolt pass makes it cook every frame'
    assert clap.op('switch').inputs == {0: clap.op('in1'), 1: clap.op('clap')}
    assert clap.op('out1').inputs[0] is clap.op('switch')


@pytest.mark.parametrize('name, count', [('clap', 3), ('bolts', CLAP_SLOTS * CLAP_POINTS + 1)])
def test_clap_shader_uniforms_match_their_entries(clap, name, count):
    node = clap.op(name)
    text = clap.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == count
    assert '@@' not in text
    for index in range(count):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec4 %s;' % uniform, text), uniform


def test_clap_yellow_is_the_brand_yellow(clap):
    values = re.search(r'YELLOW = vec3\(([\d., ]+)\)', clap.op('clap_shader').text).group(1)
    assert '%02X%02X%02X' % tuple(round(float(v) * 255) for v in values.split(',')) == 'F3DD3E'


def test_clap_lands_on_the_very_first_frame(world, clap):
    hit(world, clap)
    assert clap.op('switch').par.index.val == 1
    assert clap_bolt_count(clap) == 1
    look = clap_look(clap)
    assert look[CLAP_FLIP] == 0.0 and look[CLAP_DARK] == 0.0
    assert look[CLAP_MIX] == 1.0


def test_clap_timeline_flips_then_double_strikes_then_cuts(world, clap):
    hit(world, clap)
    states = []
    for _ in range(40):
        world.tick(clap)
        look = clap_look(clap)
        active = clap.op('switch').par.index.val == 1
        states.append(None if not active else 'flip' if look[CLAP_FLIP] > 0 else 'dark' if look[CLAP_DARK] else 'lit')
    collapsed = [s for i, s in enumerate(states) if i == 0 or s != states[i - 1]]
    assert collapsed == ['flip', 'lit', 'dark', 'lit', None]
    assert states.count('flip') == 2
    assert states.count('dark') == 2
    assert_clap_at_rest(clap)


def test_clap_trunk_crosses_the_whole_wall(world, clap):
    for _ in range(100):
        hit(world, clap)
        trunk = clap_lines(clap, 0)[0]
        assert min(p[0] for p in trunk) < 0.0
        assert max(p[0] for p in trunk) > CLAP_ASPECT
        assert all(0.09 <= p[1] <= 0.91 for p in trunk)
        assert 7 <= len(trunk) <= CLAP_TRUNK_MAX
        clap_run_to_idle(world, clap)


def turn_angles(line):
    for a, b, c in zip(line, line[1:], line[2:]):
        first = math.atan2(b[1] - a[1], b[0] - a[0])
        second = math.atan2(c[1] - b[1], c[0] - b[0])
        yield abs(math.degrees((second - first + math.pi) % (2 * math.pi) - math.pi))


@pytest.mark.parametrize('jag', [0.0, 0.6, 1.0])
def test_clap_corners_never_turn_hard_enough_to_spike(world, clap, jag):
    clap.par.Jag = jag
    for _ in range(150):
        hit(world, clap)
        for line in clap_lines(clap, 0):
            assert max(turn_angles(line), default=0.0) <= CLAP_MAX_TURN
        clap_run_to_idle(world, clap)


def test_clap_forks_start_on_the_trunk_and_leave_by_an_edge(world, clap):
    for _ in range(60):
        hit(world, clap)
        lines = clap_lines(clap, 0)
        assert len(lines) == 3
        trunk = {p[:2] for p in lines[0]}
        for fork in lines[1:]:
            assert fork[0][:2] in trunk
            assert fork[-1][1] < 0.0 or fork[-1][1] > 1.0
        clap_run_to_idle(world, clap)


@pytest.mark.parametrize('forks, count', [(0.0, 0), (0.5, 1), (1.0, 2)])
def test_clap_forks_knob(world, clap, forks, count):
    clap.par.Forks = forks
    hit(world, clap)
    assert len(clap_lines(clap, 0)) == 1 + count


def test_clap_velocity_sets_bolt_width(world, clap):
    hit(world, clap, 1.0)
    full = clap_trunk_width(clap)
    clap_run_to_idle(world, clap)
    hit(world, clap, 0.1)
    soft = clap_trunk_width(clap)
    assert full == pytest.approx(0.22)
    yellow, key, _, _ = uniforms(clap, 'clap', CLAP_INK)
    assert soft < yellow + key, 'a soft tap should be a yellow crack with no video in it'
    assert full > 2.0 * (yellow + key)


def test_clap_repeat_hits_never_repeat(world, clap):
    seen = set()
    for _ in range(30):
        hit(world, clap)
        seen.add(tuple(clap_lines(clap, 0)[0]))
        clap_run_to_idle(world, clap)
    assert len(seen) == 30


def test_clap_storm_stacks_newest_first_and_drops_the_oldest(world, clap):
    clap.par.Hold = 2.0
    trunks = []
    for count in (1, 2, 3):
        hit(world, clap)
        world.tick(clap)
        trunks.insert(0, clap_lines(clap, 0)[0])
        assert clap_bolt_count(clap) == count
        for slot, trunk in enumerate(trunks):
            assert clap_lines(clap, slot)[0] == trunk
    hit(world, clap)
    assert clap_bolt_count(clap) == 3
    assert clap_lines(clap, 1)[0] == trunks[0]
    assert clap_lines(clap, 2)[0] == trunks[1]


def test_clap_storm_all_cut_out_on_the_same_frame(world, clap):
    hit(world, clap)
    world.tick(clap, 3)
    hit(world, clap)
    world.tick(clap, 3)
    hit(world, clap)
    counts = []
    while clap.op('switch').par.index.val == 1:
        counts.append(clap_bolt_count(clap))
        world.tick(clap)
    assert set(counts) == {3}
    assert_clap_at_rest(clap)


def test_clap_storm_off_keeps_one_bolt(world, clap):
    clap.par.Storm = 0.0
    hit(world, clap)
    first = clap_lines(clap, 0)[0]
    world.tick(clap)
    hit(world, clap)
    assert clap_bolt_count(clap) == 1
    assert clap_lines(clap, 0)[0] != first


def test_clap_wall_flip_never_repeats_inside_the_guard(world, clap):
    flips = []
    for frame in range(3 * FPS):
        if frame % 6 == 0:
            clap.par.Trigger = 1.0
        else:
            clap.par.Trigger = 0.0
        world.tick(clap)
        if clap_look(clap)[CLAP_FLIP] > 0:
            flips.append(frame)
    starts = [f for i, f in enumerate(flips) if i == 0 or f != flips[i - 1] + 1]
    assert len(starts) >= 3, 'the storm should still flip sometimes'
    assert all(b - a >= 0.35 * FPS for a, b in zip(starts, starts[1:]))


def test_clap_flip_knob_off_never_flips(world, clap):
    clap.par.Flip = 0.0
    hit(world, clap)
    for _ in range(30):
        world.tick(clap)
        assert clap_look(clap)[CLAP_FLIP] == 0.0


def test_clap_second_strike_keeps_the_trunk_and_rerolls_forks(world, clap):
    hit(world, clap)
    world.tick(clap, 3)
    before = clap_lines(clap, 0)
    while clap_look(clap)[CLAP_DARK] == 0.0:
        world.tick(clap)
    after = clap_lines(clap, 0)
    assert after[0] == before[0]
    assert after[1:] != before[1:]
    world.tick(clap, 2)
    assert clap_lines(clap, 0) == after, 'the second strike draws only once'


def test_clap_yellow_slips_only_on_the_second_strike_and_settles(world, clap):
    hit(world, clap)
    for _ in range(4):
        world.tick(clap)
        assert uniforms(clap, 'clap', CLAP_SLIP) == (0.0, 0.0, 0.0, 0.0)
    while clap_look(clap)[CLAP_DARK] == 0.0:
        world.tick(clap)
    world.tick(clap, 2)
    sizes = []
    while clap.op('switch').par.index.val == 1:
        x, y = uniforms(clap, 'clap', CLAP_SLIP)[:2]
        sizes.append(math.hypot(x * CLAP_ASPECT, y))
        world.tick(clap)
    assert sizes[0] > 0
    assert sizes == sorted(sizes, reverse=True)


def test_clap_bolts_are_drawn_only_on_strikes(world, clap):
    node = clap.op('bolts')
    writes = []
    original = type(node.par).__setattr__

    def counting(pars, name, value):
        if pars is node.par:
            writes.append(absolute_frame(world))
        original(pars, name, value)

    def absolute_frame(w):
        return w.abs_time.frame

    type(node.par).__setattr__ = counting
    try:
        hit(world, clap)
        while clap.op('switch').par.index.val == 1 and clap_look(clap)[CLAP_DARK] == 0.0:
            world.tick(clap)
        while clap.op('switch').par.index.val == 1:
            world.tick(clap)
    finally:
        type(node.par).__setattr__ = original
    assert len(set(writes)) == 3, 'first strike, second strike, return to rest'


def test_clap_punch_and_mix_reach_the_shader(world, clap):
    clap.par.Punch = 1.0
    clap.par.Mix = 0.25
    hit(world, clap)
    look = clap_look(clap)
    assert look[CLAP_ZOOM] == pytest.approx(1.3)
    assert look[CLAP_MIX] == 0.25


def test_clap_returns_exactly_to_rest(world, clap):
    hit(world, clap)
    clap_run_to_idle(world, clap)
    assert_clap_at_rest(clap)


def test_clap_retrigger_ignore_keeps_the_bolt(world, clap):
    clap.par.Retrigger = 'ignore'
    hit(world, clap)
    first = clap_lines(clap, 0)[0]
    world.tick(clap)
    hit(world, clap)
    assert clap_bolt_count(clap) == 1
    assert clap_lines(clap, 0)[0] == first


def test_clap_gate_holds_and_max_hold_releases(world, clap):
    clap.par.Mode = 'gate'
    clap.par.Maxhold = 0.5
    clap.par.Trigger = 1.0
    world.tick(clap, 20)
    assert clap_bolt_count(clap) == 1
    world.tick(clap, 2 * FPS)
    assert_clap_at_rest(clap)


def test_clap_panic_and_held_pad_does_not_refire(world, clap):
    clap.par.Mode = 'gate'
    clap.par.Trigger = 1.0
    world.tick(clap, 5)
    world.panic = 1.0
    world.tick(clap)
    assert_clap_at_rest(clap)
    world.panic = 0.0
    world.tick(clap, 5)
    assert_clap_at_rest(clap)


def test_clap_reset_clears_state(world, clap):
    hit(world, clap)
    clap.op('engine').module.reset()
    assert_clap_at_rest(clap)
    world.tick(clap)  # the pad comes up, so the next press is a new one
    hit(world, clap)
    assert clap_bolt_count(clap) == 1


def test_clap_frame_gap_resets(world, clap):
    hit(world, clap)
    world.abs_time.frame += 1000
    world.tick(clap)
    assert_clap_at_rest(clap)


# ------------------------------------------------------------ Sunburst playing

@pytest.fixture
def sun(world):
    return world.block('sunburst')


def sun_sun(block):
    return uniforms(block, 'burst', 0)


def sun_rays(block):
    return uniforms(block, 'burst', 1)


def sun_shape(block):
    return uniforms(block, 'burst', 2)


def sun_press(world, block, velocity=1.0, frames=1):
    block.par.Trigger = velocity
    world.tick(block, frames)


def assert_sun_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert sun_sun(block) == (0.5, 0.5, 0.0, 0.0)
    assert sun_rays(block) == (3.0, 0.0, 0.0, 0.0)
    assert sun_shape(block) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'burst', 3) == (0.0, 0.0, 0.0, 0.0)


def test_sun_defaults_are_a_hold(sun):
    assert sun.par.Mode.val == 'gate'
    assert sun.par.Attack.val == 0.0
    assert sun.par.Spinmode.val == 'ratchet'


def test_sun_wiring(sun):
    assert sun.op('burst').inputs[0] is sun.op('in1')
    assert sun.op('switch').inputs == {0: sun.op('in1'), 1: sun.op('burst')}
    assert sun.op('out1').inputs[0] is sun.op('switch')


def test_sun_shader_uniforms_match_their_entries(sun):
    node = sun.op('burst')
    text = sun.op(node.par.pixeldat.val).text
    for index in range(node.seq.vec.numBlocks):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_sun_lands_on_the_very_first_frame(world, sun):
    sun_press(world, sun)
    assert sun.op('switch').par.index.val == 1
    assert sun_shape(sun)[:2] == (0.55, 1.0)
    assert sun_sun(sun)[2] > 0.0
    assert uniforms(sun, 'burst', 3)[0] == 1.0


def test_sun_rays_open_outward_past_the_corners(world, sun):
    sun_press(world, sun, frames=20)
    assert sun_shape(sun)[0] == pytest.approx(1.05)


def test_sun_press_pops_then_settles(world, sun):
    sun_press(world, sun)
    popped = sun_rays(sun)[2]
    world.tick(sun, 60)
    assert sun_rays(sun)[2] < popped
    assert sun_rays(sun)[2] == pytest.approx(1.25, abs=0.001)


def test_sun_velocity_sets_wedge_width(world, sun):
    sun_press(world, sun, 1.0, 60)
    hard = sun_rays(sun)[2]
    sun_press(world, sun, 0.0, 60)
    assert_sun_at_rest(sun)
    sun_press(world, sun, 0.2, 60)
    assert sun_rays(sun)[2] < hard


@pytest.mark.parametrize('knob,expected', [(0.0, 12), (0.33, 24), (1.0, 48), (0.5, 30)])
def test_sun_ray_count_is_always_a_multiple_of_three(world, sun, knob, expected):
    sun.par.Rays = knob
    sun_press(world, sun)
    assert sun_rays(sun)[0] == expected


def sun_shader(block):
    return block.op(block.op('burst').par.pixeldat.val).text


def test_sun_colors_match_the_reference_palette(sun):
    constants = re.findall(r'const vec3 ([A-Z]+) = vec3\(([\d., ]+)\) / 255\.0;', sun_shader(sun))
    actual = {
        name: ''.join('%02x' % int(float(c)) for c in channels.split(','))
        for name, channels in constants
    }
    assert actual == REFERENCE_PALETTE


def test_sun_palettes_match_the_menu_and_original_color_order(sun):
    text = sun_shader(sun)
    rows = re.findall(r'^\s+([A-Z]+(?:, [A-Z]+)+),?\s+// (\w+)$', text, re.M)
    counts = [int(n) for n in re.search(r'int\[PALETTES\]\(([\d, ]+)\)', text).group(1).split(',')]
    names = sun.par.Palette.menuNames
    assert [name for _, name in rows] == names
    assert len(counts) == len(names)
    expected = {
        'sun': ['YELLOW', 'BLUE', 'CYAN'],
        'hot': ['PINK', 'RED', 'YELLOW'],
        'hell': ['RED', 'YELLOW', 'PINK', 'BLUE'],
        'fire': ['RED', 'PINK', 'YELLOW', 'WHITE'],
        'disco': ['PINK', 'YELLOW', 'CYAN', 'BLUE', 'RED'],
        'cover': ['BLUE', 'CYAN', 'WHITE'],
        'ink': ['WHITE', 'YELLOW'],
    }
    for (row, name), count in zip(rows, counts):
        colors = row.split(', ')
        assert len(colors) == 5
        assert colors[:count] == expected[name]
        assert 'BLACK' not in colors  # rays print on black
    suns = re.search(r'SUN_COLORS\[SUNS\] = vec3\[SUNS\]\(([A-Z, ]+)\)', text).group(1).split(', ')
    assert [c.lower() for c in suns] == sun.par.Suncolor.menuNames
    assert re.search(r'const int SUNS = %d;' % len(suns), text)


@pytest.mark.parametrize('palette,size', [('sun', 3), ('hot', 3), ('hell', 4), ('fire', 4), ('disco', 5), ('cover', 3), ('ink', 2)])
@pytest.mark.parametrize('knob', [0.0, 0.33, 0.5, 1.0])
def test_sun_ray_count_is_a_multiple_of_the_palette(world, sun, palette, size, knob):
    sun.par.Palette = palette
    sun.par.Rays = knob
    sun_press(world, sun)
    count = sun_rays(sun)[0]
    assert count % size == 0
    assert count >= 2 * size
    assert 10 <= count <= 50


def test_sun_palette_and_sun_color_reach_the_shader(world, sun):
    sun_press(world, sun)
    assert uniforms(sun, 'burst', 3)[1:3] == (0.0, 0.0)  # Sunburst rays, yellow sun
    sun.par.Palette = 'disco'
    sun.par.Suncolor = 'white'
    world.tick(sun)
    assert uniforms(sun, 'burst', 3)[1:3] == (4.0, 5.0)


def test_sun_color_change_still_returns_to_rest(world, sun):
    sun.par.Palette = 'fire'
    sun.par.Suncolor = 'pink'
    sun_press(world, sun, frames=10)
    sun_press(world, sun, 0.0, FPS)
    assert_sun_at_rest(sun)


def test_sun_ratchet_rests_on_whole_rays(world, sun):
    sun.par.Spin = 0.25  # 2 rays per second: 30 frames a step, 4 frames to land
    sun_press(world, sun, frames=3)
    rests = []
    for _ in range(90):
        world.tick(sun)
        phase = sun_rays(sun)[1]
        if abs(phase - round(phase)) < 1e-9:
            rests.append(round(phase))
    assert len(rests) > 60
    assert len(set(rests)) >= 2
    assert set(rests) <= {value % 24 for value in range(0, 6)}


def test_sun_ratchet_overshoots_each_step(world, sun):
    sun.par.Spin = 0.25
    sun_press(world, sun, frames=2)
    seen = []
    for _ in range(40):
        world.tick(sun)
        seen.append(sun_rays(sun)[1])
    fractions = [value - round(value) for value in seen]
    assert max(fractions) > 0.0  # past the next ray before it settles


def test_sun_smooth_mode_keeps_moving(world, sun):
    sun.par.Spin = 0.25
    sun.par.Spinmode = 'smooth'
    sun_press(world, sun, frames=5)
    before = sun_rays(sun)[1]
    world.tick(sun)
    assert sun_rays(sun)[1] - before == pytest.approx(2.0 / FPS)


def test_sun_spin_zero_holds_still(world, sun):
    sun.par.Spin = 0.0
    sun_press(world, sun, frames=30)
    assert sun_rays(sun)[1] == 0.0


def test_sun_floor_never_reaches_zero(world, sun):
    sun.par.Floor = 0.0
    sun_press(world, sun)
    assert sun_shape(sun)[3] > 0.0


def test_sun_center_follows_while_held(world, sun):
    sun_press(world, sun, frames=3)
    sun.par.Centerx = 0.1
    sun.par.Centery = 0.8
    world.tick(sun)
    assert sun_sun(sun)[:2] == (0.1, 0.8)


def test_sun_shutter_closes_on_release(world, sun):
    sun_press(world, sun, frames=10)
    sun_press(world, sun, 0.0)
    levels = [sun_shape(sun)[1]]
    discs = [sun_sun(sun)[2]]
    for _ in range(int(0.2 * FPS) - 2):
        world.tick(sun)
        levels.append(sun_shape(sun)[1])
        discs.append(sun_sun(sun)[2])
    assert levels == sorted(levels, reverse=True)
    assert 0.0 < levels[-1] < 0.2
    assert discs == sorted(discs, reverse=True)


def test_sun_returns_exactly_to_rest(world, sun):
    sun_press(world, sun, frames=10)
    sun_press(world, sun, 0.0, FPS)
    assert_sun_at_rest(sun)


def test_sun_next_press_continues_the_rotation(world, sun):
    sun.par.Spinmode = 'smooth'
    sun_press(world, sun, frames=30)
    sun_press(world, sun, 0.0, FPS)
    assert_sun_at_rest(sun)
    sun_press(world, sun)
    assert sun_rays(sun)[1] > 0.0


def test_sun_press_during_release_reopens(world, sun):
    sun_press(world, sun, frames=10)
    sun_press(world, sun, 0.0, 5)
    assert sun_shape(sun)[1] < 1.0
    sun_press(world, sun)
    assert sun_shape(sun)[:2] == (0.55, 1.0)


def test_sun_max_hold_releases(world, sun):
    sun.par.Maxhold = 0.5
    sun_press(world, sun, frames=2 * FPS)
    assert_sun_at_rest(sun)


def test_sun_panic_and_held_pad_does_not_refire(world, sun):
    sun_press(world, sun, frames=5)
    world.panic = 1.0
    world.tick(sun)
    assert_sun_at_rest(sun)
    world.panic = 0.0
    world.tick(sun, 5)
    assert_sun_at_rest(sun)


def test_sun_reset_clears_state(world, sun):
    sun.par.Spinmode = 'smooth'
    sun_press(world, sun, frames=30)
    sun.op('engine').module.reset()
    assert_sun_at_rest(sun)
    world.tick(sun)
    sun_press(world, sun, 0.0)
    sun_press(world, sun)
    assert sun_rays(sun)[1] == 0.0


def test_sun_frame_gap_resets(world, sun):
    sun_press(world, sun, frames=5)
    world.abs_time.frame += 1000
    sun_press(world, sun, 0.0)
    assert_sun_at_rest(sun)


# ------------------------------------------------------------ Pixel Crush playing

@pytest.fixture
def crush(world):
    return world.block('pixel_crush')


def crush_level(block):
    return uniforms(block, 'crush', 0)[0]


def assert_crush_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert uniforms(block, 'crush', 0) == (1.0, 0.6, 0.0, 0.0)
    assert uniforms(block, 'crush', 1) == (0.5, 0.5, 0.0, 0.0)
    assert uniforms(block, 'crush', 2) == (8.0, 0.0, 0.0, 0.0)


def test_crush_wiring_and_mipmapped_input(crush):
    node = crush.op('crush')
    assert node.inputs[0] is crush.op('in1')
    assert node.par.inputfiltertype.val == 'mipmap'
    assert crush.op('switch').inputs == {0: crush.op('in1'), 1: node}
    assert crush.op('out1').inputs[0] is crush.op('switch')


def test_crush_shader_uniforms_match_their_entries(crush):
    node = crush.op('crush')
    text = crush.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 3
    for index in range(3):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_crush_lands_at_peak_on_the_very_first_frame(world, crush):
    hit(world, crush)
    assert crush.op('switch').par.index.val == 1
    assert crush_level(crush) == 10.0  # half of the 2048 wall
    assert uniforms(crush, 'crush', 2)[0] == 3.0
    assert uniforms(crush, 'crush', 2)[2] == 1.0


def test_crush_velocity_sets_the_block_size(world, crush):
    seen = []
    for velocity in (1.0, 0.5, 0.1):
        hit(world, crush, velocity)
        seen.append(crush_level(crush))
        world.tick(crush, FPS * 2)
    assert seen == [10.0, 7.0, 5.0]


def test_crush_size_knob_sets_the_peak(world, crush):
    crush.par.Size = 0.0
    hit(world, crush)
    assert crush_level(crush) == 3.0


def test_crush_rebuild_walks_down_every_whole_level(world, crush):
    hit(world, crush)
    seen = [crush_level(crush)]
    for _ in range(FPS):
        world.tick(crush)
        if crush.op('switch').par.index.val == 1:
            seen.append(crush_level(crush))
    assert all(level == int(level) for level in seen)
    assert seen == sorted(seen, reverse=True)
    assert sorted(set(seen)) == [float(n) for n in range(1, 11)]


def test_crush_low_steps_makes_fewer_stairs(world, crush):
    crush.par.Steps = 0.0
    hit(world, crush)
    seen = {crush_level(crush)}
    for _ in range(FPS):
        world.tick(crush)
        if crush.op('switch').par.index.val == 1:
            seen.add(crush_level(crush))
    assert seen == {10.0}


def test_crush_color_returns_as_the_blocks_shrink(world, crush):
    hit(world, crush)
    bits = [uniforms(crush, 'crush', 2)[0]]
    for _ in range(int(FPS * 0.6)):
        world.tick(crush)
        if crush.op('switch').par.index.val == 1:
            bits.append(uniforms(crush, 'crush', 2)[0])
    assert bits == sorted(bits)
    assert bits[0] == 3.0 and bits[-1] > 7.0


def test_crush_detail_zero_is_a_uniform_grid(world, crush):
    crush.par.Detail = 0.0
    hit(world, crush)
    assert uniforms(crush, 'crush', 0)[2] == 0.0
    world.tick(crush, FPS)
    crush.par.Detail = 1.0
    hit(world, crush)
    assert uniforms(crush, 'crush', 0)[1:3] == pytest.approx((0.04, 4.0))


def test_crush_look_controls_reach_the_shader(world, crush):
    crush.par.Focusx = 0.2
    crush.par.Focusy = 0.7
    crush.par.Focusamount = 0.9
    crush.par.Focusmode = 'hole'
    crush.par.Gap = 0.5
    crush.par.Mix = 0.25
    hit(world, crush)
    assert uniforms(crush, 'crush', 1) == pytest.approx((0.2, 0.7, 0.9, 1.0))
    assert uniforms(crush, 'crush', 2)[1:3] == pytest.approx((0.5, 0.25))


def test_crush_gate_holds_the_peak_then_rebuilds(world, crush):
    crush.par.Mode = 'gate'
    crush.par.Trigger = 1.0
    world.tick(crush, FPS)
    assert crush_level(crush) == 10.0
    crush.par.Trigger = 0.0
    world.tick(crush, 12)
    assert 1.0 <= crush_level(crush) < 10.0


def test_crush_retrigger_snaps_back_to_peak(world, crush):
    hit(world, crush)
    world.tick(crush, 20)
    assert crush_level(crush) < 10.0
    hit(world, crush)
    assert crush_level(crush) == 10.0


def test_crush_returns_exactly_to_rest(world, crush):
    hit(world, crush)
    world.tick(crush, FPS * 2)
    assert_crush_at_rest(crush)


def test_crush_panic_and_held_pad_does_not_refire(world, crush):
    crush.par.Trigger = 1.0
    world.tick(crush)
    world.panic = 1.0
    world.tick(crush)
    assert_crush_at_rest(crush)
    world.panic = 0.0
    world.tick(crush, 3)
    assert_crush_at_rest(crush)


def test_crush_reset_and_frame_gap(world, crush):
    hit(world, crush)
    crush.op('reset_watch').module.onPulse()
    assert_crush_at_rest(crush)
    world.tick(crush, 2)
    hit(world, crush)
    world.abs_time.frame += 31
    world.tick(crush)
    assert_crush_at_rest(crush)


def test_crush_max_hold_in_gate_mode(world, crush):
    crush.par.Mode = 'gate'
    crush.par.Maxhold = 0.1
    crush.par.Trigger = 1.0
    world.tick(crush, FPS * 2)
    assert_crush_at_rest(crush)


# ------------------------------------------------------------ Star Glint playing
@pytest.fixture
def glint(world):
    return world.block('star_glint')


def assert_glint_at_rest(block):
    assert block.op('switch').par.index.val == 0
    for index in range(4):
        assert uniforms(block, 'stars', index) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'extract', 0)[0] == 1.0


def test_glint_wiring(glint):
    assert glint.op('extract').inputs[0] is glint.op('in1')
    assert glint.op('blur_x').inputs[0] is glint.op('extract')
    assert glint.op('blur_y').inputs[0] is glint.op('blur_x')
    assert glint.op('stars').inputs == {0: glint.op('in1'), 1: glint.op('blur_y')}
    assert glint.op('switch').inputs == {0: glint.op('in1'), 1: glint.op('stars')}
    assert glint.op('out1').inputs[0] is glint.op('switch')


def test_glint_blur_passes_run_on_opposite_axes(glint):
    assert uniforms(glint, 'blur_x', 0)[:2] == (1.0, 0.0)
    assert uniforms(glint, 'blur_y', 0)[:2] == (0.0, 1.0)


def test_glint_shader_uniforms_match_their_entries(glint):
    for name in ('extract', 'blur_x', 'blur_y', 'stars'):
        node = glint.op(name)
        text = glint.op(node.par.pixeldat.val).text
        for index in range(node.seq.vec.numBlocks):
            uniform = getattr(node.par, 'vec%dname' % index).val
            assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_glint_lands_at_full_pop_on_the_very_first_frame(world, glint):
    glint.par.Trigger = 1.0
    world.tick(glint)
    assert glint.op('switch').par.index.val == 1
    phase, pop, seed, level = uniforms(glint, 'stars', 0)
    assert (phase, pop, level) == (0.0, 1.0, 1.0)
    assert seed > 0.0
    assert uniforms(glint, 'stars', 1)[0] > 0.0


def test_glint_defaults_are_gate_silver_with_a_little_rainbow(glint):
    assert glint.par.Mode.eval() == 'gate'
    assert glint.par.Rainbow.eval() == 0.25


def test_glint_keeps_sparkling_while_held(world, glint):
    glint.par.Trigger = 1.0
    world.tick(glint, FPS * 5)
    phase, pop, _, level = uniforms(glint, 'stars', 0)
    assert level == 1.0
    assert phase > 5.0
    assert pop < 0.001
    assert glint.op('switch').par.index.val == 1


def test_glint_gate_tails_out_after_release(world, glint):
    glint.par.Trigger = 1.0
    world.tick(glint, 10)
    glint.par.Trigger = 0.0
    world.tick(glint, 30)
    assert 0.0 < uniforms(glint, 'stars', 0)[3] < 1.0
    world.tick(glint, FPS * 2)
    assert_glint_at_rest(glint)


def test_glint_hit_mode_plays_once_without_a_held_key(world, glint):
    glint.par.Mode = 'hit'
    hit(world, glint)
    world.tick(glint, 30)
    assert 0.0 < uniforms(glint, 'stars', 0)[3] < 1.0
    world.tick(glint, FPS * 2)
    assert_glint_at_rest(glint)


def test_glint_velocity_scales_density_size_heroes_and_glow(world, glint):
    hit(world, glint, 1.0)
    hard = [uniforms(glint, 'stars', 1), uniforms(glint, 'stars', 2), uniforms(glint, 'stars', 3)]
    world.tick(glint, FPS * 3)
    hit(world, glint, 0.2)
    soft = [uniforms(glint, 'stars', 1), uniforms(glint, 'stars', 2), uniforms(glint, 'stars', 3)]
    assert soft[0][0] < hard[0][0]
    assert soft[0][1] < hard[0][1]
    assert soft[1][3] < hard[1][3]
    assert soft[2][0] < hard[2][0]


def test_glint_star_never_outgrows_the_grid_search(world, glint):
    glint.par.Starsize = 1.0
    hit(world, glint)
    assert uniforms(glint, 'stars', 1)[1] * 1.3 <= 1.05


def test_glint_points_menu_sets_spike_lines(world, glint):
    for name, lines in (('four', 2.0), ('six', 3.0), ('eight', 4.0)):
        glint.par.Points = name
        hit(world, glint)
        assert uniforms(glint, 'stars', 1)[2] == lines
        world.tick(glint, FPS * 3)


def test_glint_look_controls_reach_the_shader(world, glint):
    glint.par.Highlight = 0.7
    glint.par.Floor = 0.1
    glint.par.Rainbow = 0.9
    glint.par.Mix = 0.5
    hit(world, glint)
    assert uniforms(glint, 'stars', 2)[:3] == pytest.approx((0.7, 0.1, 0.9))
    assert uniforms(glint, 'stars', 3)[2] == 0.5
    assert uniforms(glint, 'extract', 0)[0] == pytest.approx(0.7)


def test_glint_moving_twinkle_never_jumps_the_phase(world, glint):
    glint.par.Trigger = 1.0
    world.tick(glint, 30)
    before = uniforms(glint, 'stars', 0)[0]
    glint.par.Twinkle = 1.0
    world.tick(glint)
    assert 0.0 < uniforms(glint, 'stars', 0)[0] - before < 0.05


def test_glint_each_press_gets_a_new_pattern_and_retrigger_restarts(world, glint):
    glint.par.Mode = 'hit'
    hit(world, glint)
    first = uniforms(glint, 'stars', 0)
    world.tick(glint, 10)
    assert uniforms(glint, 'stars', 0)[0] > 0.0
    hit(world, glint)
    second = uniforms(glint, 'stars', 0)
    assert second[2] != first[2]
    assert (second[0], second[1]) == (0.0, 1.0)


def test_glint_retrigger_ignore_keeps_the_pattern(world, glint):
    glint.par.Mode = 'hit'
    glint.par.Retrigger = 'ignore'
    hit(world, glint)
    seed = uniforms(glint, 'stars', 0)[2]
    world.tick(glint, 10)
    hit(world, glint)
    assert uniforms(glint, 'stars', 0)[2] == seed


def test_glint_panic_and_held_pad_does_not_refire(world, glint):
    glint.par.Trigger = 1.0
    world.tick(glint)
    world.panic = 1.0
    world.tick(glint)
    assert_glint_at_rest(glint)
    world.panic = 0.0
    world.tick(glint, 3)
    assert_glint_at_rest(glint)


def test_glint_reset_and_frame_gap(world, glint):
    glint.par.Trigger = 1.0
    world.tick(glint)
    glint.op('reset_watch').module.onPulse()
    assert_glint_at_rest(glint)
    glint.par.Trigger = 0.0
    world.tick(glint, 2)
    hit(world, glint)
    world.abs_time.frame += 31
    world.tick(glint)
    assert_glint_at_rest(glint)


def test_glint_max_hold_in_gate_mode(world, glint):
    glint.par.Maxhold = 0.1
    glint.par.Trigger = 1.0
    world.tick(glint, FPS * 2)
    assert_glint_at_rest(glint)


# ------------------------------------------------------------ Ring Pop playing

@pytest.fixture
def ringpop(world):
    return world.block('ring_pop')


def ring_target(block):
    return uniforms(block, 'rings', 0)


def ring_rings(block):
    return uniforms(block, 'rings', 1)


def ring_style(block):
    return uniforms(block, 'rings', 2)


def ring_press(world, block, velocity=1.0, frames=1):
    block.par.Trigger = velocity
    world.tick(block, frames)


def ring_lift(world, block, frames=FPS):
    block.par.Trigger = 0.0
    world.tick(block, frames)


def assert_ringpop_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert ring_target(block) == (0.5, 0.5, 0.0, 1.0)
    assert ring_rings(block) == (4.0, 0.0, 0.0, 1.0)
    assert ring_style(block) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'rings', 3) == (0.0, 0.0, 0.0, 0.0)


def test_ringpop_defaults_are_the_agreed_hold(ringpop):
    assert ringpop.par.Mode.val == 'gate'
    assert ringpop.par.Attack.val == 0.0
    assert ringpop.par.Palette.val == 'cover'
    assert ringpop.par.Targets.val == 'one'
    assert ringpop.par.Marchmode.val == 'ratchet'
    assert ringpop.par.March.val > 0.0


def test_ringpop_wiring(ringpop):
    assert ringpop.op('rings').inputs[0] is ringpop.op('in1')
    assert ringpop.op('switch').inputs == {0: ringpop.op('in1'), 1: ringpop.op('rings')}
    assert ringpop.op('out1').inputs[0] is ringpop.op('switch')


def test_ringpop_shader_uniforms_match_their_entries(ringpop):
    node = ringpop.op('rings')
    text = ringpop.op(node.par.pixeldat.val).text
    for index in range(node.seq.vec.numBlocks):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_ringpop_lands_on_the_very_first_frame(world, ringpop):
    ring_press(world, ringpop)
    assert ringpop.op('switch').par.index.val == 1
    assert ring_rings(ringpop)[2] == 0.5
    assert uniforms(ringpop, 'rings', 3)[0] == 1.0


def test_ringpop_slams_out_to_the_whole_wall(world, ringpop):
    ring_press(world, ringpop)
    world.tick(ringpop, 2)
    partial = ring_rings(ringpop)[2]
    assert 0.5 < partial < 1.0
    world.tick(ringpop, 6)
    assert ring_rings(ringpop)[2] == 1.0


def test_ringpop_velocity_sets_ring_count(world, ringpop):
    ring_press(world, ringpop, 0.01, 3)
    assert ring_rings(ringpop)[0] == 4.0
    ring_lift(world, ringpop)
    ring_press(world, ringpop, 1.0, 3)
    assert ring_rings(ringpop)[0] == 16.0


def test_ringpop_velocity_depth_off_always_uses_the_knob(world, ringpop):
    ringpop.par.Velocitydepth = 0.0
    ring_press(world, ringpop, 0.01, 3)
    assert ring_rings(ringpop)[0] == 16.0


def test_ringpop_each_press_moves_one_band_and_one_color(world, ringpop):
    ringpop.par.March = 0.0
    ringpop.par.Palette = 'candy'
    seen = []
    for _ in range(3):
        ring_press(world, ringpop)
        seen.append((ring_rings(ringpop)[1], ring_style(ringpop)[3]))
        ring_lift(world, ringpop)
    assert seen == [(1.0, 0.0), (2.0, 1.0), (3.0, 2.0)]


def test_ringpop_press_during_collapse_refires_and_steps(world, ringpop):
    ringpop.par.March = 0.0
    ring_press(world, ringpop, frames=20)
    ring_lift(world, ringpop, 3)
    assert ring_rings(ringpop)[2] < 1.0
    ring_press(world, ringpop)
    assert ring_rings(ringpop)[1] == 2.0
    assert ring_rings(ringpop)[2] == 0.5


def test_ringpop_marches_in_ratchet_steps_that_settle(world, ringpop):
    ringpop.par.March = 0.25  # 3 steps per second, 20 frames each
    ring_press(world, ringpop, frames=10)
    start = ring_rings(ringpop)[1]
    assert start == pytest.approx(1.0)
    world.tick(ringpop, 8)
    assert ring_rings(ringpop)[1] == pytest.approx(start)
    world.tick(ringpop, 12)
    landed = ring_rings(ringpop)[1]
    assert landed == pytest.approx(start + 0.5, abs=1e-6)
    world.tick(ringpop, 4)
    assert ring_rings(ringpop)[1] == pytest.approx(landed)


def test_ringpop_ratchet_overshoots_while_landing(world, ringpop):
    ringpop.par.March = 0.25
    ring_press(world, ringpop, frames=10)
    settled = ring_rings(ringpop)[1]
    peak = settled
    for _ in range(20):
        world.tick(ringpop)
        peak = max(peak, ring_rings(ringpop)[1])
    assert peak > settled + 0.5


def test_ringpop_march_at_zero_stands_still(world, ringpop):
    ringpop.par.March = 0.0
    ring_press(world, ringpop)
    start = ring_rings(ringpop)[1]
    world.tick(ringpop, FPS)
    assert ring_rings(ringpop)[1] == start


def test_ringpop_smooth_march_never_sits_still(world, ringpop):
    ringpop.par.Marchmode = 'smooth'
    ring_press(world, ringpop, frames=2)
    previous = ring_rings(ringpop)[1]
    for _ in range(5):
        world.tick(ringpop)
        assert ring_rings(ringpop)[1] > previous
        previous = ring_rings(ringpop)[1]


def test_ringpop_phase_stays_in_the_repeat_during_a_long_hold(world, ringpop):
    ringpop.par.March = 1.0
    ring_press(world, ringpop, frames=FPS * 10)
    assert 0.0 <= ring_rings(ringpop)[1] < 10.0


def test_ringpop_collapses_then_the_dot_swells_and_cuts(world, ringpop):
    ring_press(world, ringpop, frames=20)
    ringpop.par.Trigger = 0.0
    covers, dots = [], []
    while ringpop.op('switch').par.index.val == 1 and len(covers) < FPS:
        world.tick(ringpop)
        covers.append(ring_rings(ringpop)[2])
        dots.append(ring_style(ringpop)[2])
    assert_ringpop_at_rest(ringpop)
    live = [i for i, cover in enumerate(covers[:-1]) if cover > 0.0]
    assert covers[0] < 1.0
    assert all(covers[i] >= covers[i + 1] for i in range(len(covers) - 1))
    assert all(dots[i] == 0.0 for i in live)
    shown = [dot for dot in dots[:-1] if dot > 0.0]
    assert covers[len(live)] == 0.0
    swell = 0.45 * 1.3
    assert shown[-1] == pytest.approx(swell)
    swollen = sum(1 for dot in shown if dot == pytest.approx(swell))
    assert 2 <= swollen <= 3                     # about two frames at the default release
    closing = shown[:-swollen]
    assert all(a > b for a, b in zip(closing, closing[1:]))
    assert closing[-1] < swell                   # the dot closes, then swells


def test_ringpop_release_during_slam_keeps_what_was_printed(world, ringpop):
    ring_press(world, ringpop)
    ring_lift(world, ringpop, 1)
    assert ring_rings(ringpop)[2] < 0.5


def test_ringpop_attack_ramps_the_cover(world, ringpop):
    ringpop.par.Attack = 0.5
    ring_press(world, ringpop, frames=2)
    assert ring_rings(ringpop)[2] < 0.25


def test_ringpop_hit_mode_runs_its_course(world, ringpop):
    ringpop.par.Mode = 'hit'
    ringpop.par.Hold = 0.1
    ring_press(world, ringpop)
    ringpop.par.Trigger = 0.0
    world.tick(ringpop, 3)
    assert ringpop.op('switch').par.index.val == 1
    world.tick(ringpop, FPS)
    assert_ringpop_at_rest(ringpop)


@pytest.mark.parametrize('name,size', [('cover', 1.0), ('target', 2.0), ('candy', 5.0)])
def test_ringpop_palette_sets_the_color_count(world, ringpop, name, size):
    ringpop.par.Palette = name
    ring_press(world, ringpop)
    assert ring_rings(ringpop)[3] == size


def test_ringpop_shader_inks_are_the_brand_colors(ringpop):
    text = ringpop.op('rings_shader').text
    rows = re.findall(r'vec3\(([\d.]+), ([\d.]+), ([\d.]+)\),?\s+// (\w+)', text)
    hexes = {name: ''.join('%02X' % round(float(c) * 255) for c in rgb) for *rgb, name in rows}
    assert hexes == {'blue': '2573FF', 'yellow': 'F3DD3E', 'pink': 'FF8DFF',
                     'red': 'FF424D', 'cyan': '44F8FF'}


def test_ringpop_twin_and_position_reach_the_shader(world, ringpop):
    ringpop.par.Targets = 'two'
    ringpop.par.Centerx = 0.4
    ringpop.par.Centery = 0.6
    ringpop.par.Spread = 0.25
    ring_press(world, ringpop)
    assert ring_target(ringpop) == (0.4, 0.6, 0.25, 2.0)


def test_ringpop_ink_shadow_mix_reach_the_shader(world, ringpop):
    ringpop.par.Ink = 1.0
    ringpop.par.Shadow = 0.5
    ringpop.par.Mix = 0.25
    ring_press(world, ringpop)
    assert ring_style(ringpop)[:2] == (0.02, 0.03)
    assert uniforms(ringpop, 'rings', 3)[0] == 0.25


def test_ringpop_returns_exactly_to_rest(world, ringpop):
    ring_press(world, ringpop, frames=30)
    ring_lift(world, ringpop)
    assert_ringpop_at_rest(ringpop)


def test_ringpop_panic_and_held_pad_does_not_refire(world, ringpop):
    ring_press(world, ringpop)
    world.panic = 1.0
    world.tick(ringpop)
    assert_ringpop_at_rest(ringpop)
    world.panic = 0.0
    world.tick(ringpop, 3)
    assert_ringpop_at_rest(ringpop)


def test_ringpop_reset_and_frame_gap(world, ringpop):
    ring_press(world, ringpop)
    ringpop.op('reset_watch').module.onPulse()
    assert_ringpop_at_rest(ringpop)
    ring_lift(world, ringpop, 2)
    ring_press(world, ringpop)
    assert ring_rings(ringpop)[1] == pytest.approx(1.0, abs=0.01)  # reset cleared the steps
    world.abs_time.frame += 31
    world.tick(ringpop)
    assert_ringpop_at_rest(ringpop)


def test_ringpop_max_hold(world, ringpop):
    ringpop.par.Maxhold = 0.1
    ring_press(world, ringpop, frames=FPS * 2)
    assert_ringpop_at_rest(ringpop)


# ------------------------------------------------------------ Bulb Grid playing

@pytest.fixture
def bulbgrid(world):
    return world.block('bulb_grid')


def bulb_grid_values(block):
    return uniforms(block, 'bulbs', 0)


def bulb_pattern(block):
    return uniforms(block, 'bulbs', 1)


def bulb_color(block):
    return uniforms(block, 'bulbs', 4)


def bulb_press(world, block, velocity=1.0, frames=1):
    block.par.Trigger = velocity
    world.tick(block, frames)


def bulb_lift(world, block, frames=FPS):
    block.par.Trigger = 0.0
    world.tick(block, frames)


def assert_bulbgrid_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert bulb_grid_values(block) == (26.0, 8.0, 0.0, 0.0)
    assert bulb_pattern(block) == (0.0, 0.0, 2.0, 0.0)
    assert uniforms(block, 'bulbs', 2) == (0.5, 0.5, 0.0, 0.0)
    assert uniforms(block, 'bulbs', 3) == (0.0, 0.0, 0.0, 0.0)
    assert bulb_color(block) == (0.0, 0.0, 0.0, 0.0)


def test_bulbgrid_defaults_are_the_agreed_hold(bulbgrid):
    assert bulbgrid.par.Mode.val == 'gate'
    assert bulbgrid.par.Attack.val == 0.0
    assert bulbgrid.par.Pattern.val == 'chase'
    assert bulbgrid.par.Palette.val == 'yellow'
    assert bulbgrid.par.Columns.val == 26.0
    assert bulbgrid.par.Rows.val == 8.0


def test_bulbgrid_first_press_lands_on_its_first_frame(world, bulbgrid):
    bulb_press(world, bulbgrid)
    assert bulbgrid.op('switch').par.index.val == 1
    assert bulb_grid_values(bulbgrid)[3] == 1.0
    assert bulb_pattern(bulbgrid)[1] == 1.0


def test_bulbgrid_velocity_sets_bulb_size(world, bulbgrid):
    bulb_press(world, bulbgrid, 0.1, 10)
    soft = bulb_grid_values(bulbgrid)[2]
    bulb_lift(world, bulbgrid)
    bulb_press(world, bulbgrid, 1.0, 10)
    assert bulb_grid_values(bulbgrid)[2] > soft * 1.5


def test_bulbgrid_bulbs_pop_then_settle_and_fit_their_cells(world, bulbgrid):
    bulbgrid.par.Bulbsize = 1.0
    bulb_press(world, bulbgrid)
    popped = bulb_grid_values(bulbgrid)[2]
    assert popped == 1.0
    world.tick(bulbgrid, 10)
    assert bulb_grid_values(bulbgrid)[2] == pytest.approx(0.95)


def test_bulbgrid_each_press_steps_and_speed_zero_stands_still(world, bulbgrid):
    bulbgrid.par.Speed = 0.0
    for expected in (1.0, 2.0, 3.0):
        bulb_press(world, bulbgrid, frames=20)
        assert bulb_pattern(bulbgrid)[1] == expected
        bulb_lift(world, bulbgrid)


def test_bulbgrid_steps_are_whole_at_speed(world, bulbgrid):
    bulbgrid.par.Speed = 0.25  # 4 steps per second
    bulb_press(world, bulbgrid, frames=FPS + 1)
    tick = bulb_pattern(bulbgrid)[1]
    assert tick == int(tick)
    assert tick == pytest.approx(5.0, abs=1.0)


def test_bulbgrid_reverse_runs_the_steps_backward(world, bulbgrid):
    bulbgrid.par.Speed = 0.0
    bulbgrid.par.Direction = 'reverse'
    bulb_press(world, bulbgrid)
    assert bulb_pattern(bulbgrid)[1] == 720719.0


def test_bulbgrid_cycle_walks_the_patterns_from_the_selected_one(world, bulbgrid):
    bulbgrid.par.Pattern = 'sweep'
    bulbgrid.par.Cycle = 'press'
    seen = []
    for _ in range(4):
        bulb_press(world, bulbgrid)
        seen.append(bulb_pattern(bulbgrid)[0])
        bulb_lift(world, bulbgrid)
    assert seen == [4.0, 5.0, 6.0, 0.0]


@pytest.mark.parametrize('name,index', [
    ('chase', 0), ('checker', 1), ('rings', 2), ('diamonds', 3), ('sweep', 4), ('wave', 5), ('sparkle', 6),
])
def test_bulbgrid_every_pattern_reaches_the_shader(world, bulbgrid, name, index):
    bulbgrid.par.Pattern = name
    bulb_press(world, bulbgrid)
    assert bulb_pattern(bulbgrid)[0] == index


def test_bulbgrid_grid_counts_are_whole_numbers(world, bulbgrid):
    bulbgrid.par.Columns = 12.4
    bulbgrid.par.Rows = 3.6
    bulb_press(world, bulbgrid)
    assert bulb_grid_values(bulbgrid)[:2] == (12.0, 4.0)


@pytest.mark.parametrize('density,period', [(0.0, 8.0), (1.0, 2.0)])
def test_bulbgrid_density_sets_the_period(world, bulbgrid, density, period):
    bulbgrid.par.Density = density
    bulb_press(world, bulbgrid)
    assert bulb_pattern(bulbgrid)[2:] == (period, density)


@pytest.mark.parametrize('palette,tone', [
    ('yellow', 0.0), ('blue', 1.0), ('pink', 2.0), ('red', 3.0), ('cyan', 4.0), ('white', 5.0),
])
def test_bulbgrid_single_colors_pick_their_tone(world, bulbgrid, palette, tone):
    bulbgrid.par.Palette = palette
    bulb_press(world, bulbgrid)
    assert bulb_color(bulbgrid)[:2] == (tone, 0.0)


def test_bulbgrid_candy_shifts_each_press_and_press_palette_rotates(world, bulbgrid):
    bulbgrid.par.Palette = 'candy'
    bulb_press(world, bulbgrid)
    assert bulb_color(bulbgrid)[:2] == (-1.0, 0.0)
    bulb_lift(world, bulbgrid)
    bulb_press(world, bulbgrid)
    assert bulb_color(bulbgrid)[:2] == (-1.0, 1.0)
    bulb_lift(world, bulbgrid)
    bulbgrid.par.Palette = 'press'
    bulb_press(world, bulbgrid)
    assert bulb_color(bulbgrid)[:2] == (2.0, 0.0)


def test_bulbgrid_shader_inks_are_the_brand_colors(bulbgrid):
    text = bulbgrid.op('bulbs_shader').text
    rows = re.findall(r'vec3\(([\d.]+), ([\d.]+), ([\d.]+)\),?\s+// (\w+)', text)
    hexes = {name: ''.join('%02X' % round(float(c) * 255) for c in rgb) for *rgb, name in rows}
    assert hexes == {'blue': '2573FF', 'yellow': 'F3DD3E', 'pink': 'FF8DFF',
                     'red': 'FF424D', 'cyan': '44F8FF'}
    assert 'vec3(1.0)                       // white' in text


def test_bulbgrid_aim_ink_shadow_mix_reach_the_shader(world, bulbgrid):
    bulbgrid.par.Centerx = 0.25
    bulbgrid.par.Centery = 0.75
    bulbgrid.par.Axis = 'down'
    bulbgrid.par.Alternate = 'on'
    bulbgrid.par.Ink = 1.0
    bulbgrid.par.Shadow = 0.5
    bulbgrid.par.Mix = 0.25
    bulb_press(world, bulbgrid)
    assert uniforms(bulbgrid, 'bulbs', 2) == (0.25, 0.75, 1.0, 1.0)
    assert uniforms(bulbgrid, 'bulbs', 3)[:3] == (0.12, 0.1, 0.25)


def test_bulbgrid_release_burns_out_then_rests_exactly(world, bulbgrid):
    bulb_press(world, bulbgrid, frames=20)
    bulbgrid.par.Trigger = 0.0
    covers = []
    while bulbgrid.op('switch').par.index.val == 1 and len(covers) < FPS * 2:
        world.tick(bulbgrid)
        covers.append(bulb_grid_values(bulbgrid)[3])
    assert_bulbgrid_at_rest(bulbgrid)
    assert covers[0] < 1.0
    assert all(a >= b for a, b in zip(covers, covers[1:]))


def test_bulbgrid_attack_ramps_the_cover(world, bulbgrid):
    bulbgrid.par.Attack = 0.5
    bulb_press(world, bulbgrid, frames=2)
    assert bulb_grid_values(bulbgrid)[3] < 0.25


def test_bulbgrid_hit_mode_runs_its_course(world, bulbgrid):
    bulbgrid.par.Mode = 'hit'
    bulbgrid.par.Hold = 0.1
    bulb_press(world, bulbgrid)
    bulbgrid.par.Trigger = 0.0
    world.tick(bulbgrid, 3)
    assert bulbgrid.op('switch').par.index.val == 1
    world.tick(bulbgrid, FPS)
    assert_bulbgrid_at_rest(bulbgrid)


def test_bulbgrid_panic_and_held_pad_does_not_refire(world, bulbgrid):
    bulb_press(world, bulbgrid)
    world.panic = 1.0
    world.tick(bulbgrid)
    assert_bulbgrid_at_rest(bulbgrid)
    world.panic = 0.0
    world.tick(bulbgrid, 3)
    assert_bulbgrid_at_rest(bulbgrid)


def test_bulbgrid_reset_and_frame_gap(world, bulbgrid):
    bulbgrid.par.Speed = 0.0
    bulb_press(world, bulbgrid)
    bulbgrid.op('reset_watch').module.onPulse()
    assert_bulbgrid_at_rest(bulbgrid)
    bulb_lift(world, bulbgrid, 2)
    bulb_press(world, bulbgrid)
    assert bulb_pattern(bulbgrid)[1] == 1.0  # reset cleared the steps
    world.abs_time.frame += 31
    world.tick(bulbgrid)
    assert_bulbgrid_at_rest(bulbgrid)


def test_bulbgrid_max_hold(world, bulbgrid):
    bulbgrid.par.Maxhold = 0.1
    bulb_press(world, bulbgrid, frames=FPS * 2)
    assert_bulbgrid_at_rest(bulbgrid)


# ------------------------------------------------------------ Fault Line playing

@pytest.fixture
def fault(world):
    return world.block('fault_line')


def fault_shape(block):
    return uniforms(block, 'fault', 0)


def fault_motion(block):
    return uniforms(block, 'fault', 1)


def fault_gap(block):
    return uniforms(block, 'fault', 2)


def assert_fault_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert fault_shape(block) == (0.5, 0.0, 0.0, 0.0)
    assert fault_motion(block) == (0.0, 0.0, 0.5, 0.0)
    assert fault_gap(block) == (0.0, 0.0, 0.0, 0.0)


def fault_gaps(world, block):
    """Gap per frame from a hard hit until the block is back at rest."""
    hit(world, block)
    gaps = [fault_motion(block)[0]]
    while block.op('switch').par.index.val == 1:
        world.tick(block)
        gaps.append(fault_motion(block)[0])
    return gaps


def test_fault_wiring(fault):
    assert fault.op('fault').inputs[0] is fault.op('in1')
    assert fault.op('switch').inputs == {0: fault.op('in1'), 1: fault.op('fault')}
    assert fault.op('out1').inputs[0] is fault.op('switch')


def test_fault_shader_uniforms_match_their_entries(fault):
    node = fault.op('fault')
    text = fault.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 3
    for index in range(3):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_fault_lands_full_on_the_very_first_frame(world, fault):
    hit(world, fault)
    assert fault.op('switch').par.index.val == 1
    assert fault_motion(fault)[0] == pytest.approx(0.15)
    assert abs(fault_motion(fault)[1]) == pytest.approx(0.15 * 0.35)
    assert fault_motion(fault)[3] == pytest.approx(0.014)
    assert fault_shape(fault)[2:] == pytest.approx((0.6, 0.6))
    assert fault_gap(fault)[3] == 1.0


def test_fault_velocity_scales_gap_ink_and_forks(world, fault):
    hit(world, fault, 1.0)
    hard_motion, hard_shape = fault_motion(fault), fault_shape(fault)
    world.tick(fault, FPS)
    hit(world, fault, 0.1)
    assert fault_motion(fault)[0] == pytest.approx(0.15 * 0.325)
    assert fault_motion(fault)[3] < hard_motion[3]
    assert fault_shape(fault)[3] == pytest.approx(hard_shape[3] * 0.1)


def test_fault_aftershocks_reopen_and_shrink(world, fault):
    gaps = fault_gaps(world, fault)
    peaks = [gaps[i] for i in range(1, len(gaps) - 1) if gaps[i - 1] < gaps[i] >= gaps[i + 1] and gaps[i] > 0.02]
    assert len(peaks) == 2
    assert peaks[0] > peaks[1] > 0
    assert gaps[0] > peaks[0]


def test_fault_zero_aftershock_closes_once(world, fault):
    fault.par.Aftershock = 0.0
    gaps = fault_gaps(world, fault)
    assert all(a >= b for a, b in zip(gaps, gaps[1:]))


def test_fault_shear_flips_on_each_jolt(world, fault):
    hit(world, fault)
    signs = [math.copysign(1.0, fault_motion(fault)[1])]
    for _ in range(int(FPS * 0.4)):
        world.tick(fault)
        if fault_motion(fault)[0] > 0.005:
            signs.append(math.copysign(1.0, fault_motion(fault)[1]))
    assert len(set(signs)) == 2


def test_fault_settles_inside_a_beat_at_116(world, fault):
    assert (len(fault_gaps(world, fault)) - 1) / FPS < 60.0 / 116


def test_fault_every_hit_gets_a_new_path_and_the_other_side_drops(world, fault):
    seen, shares = set(), []
    for _ in range(6):
        hit(world, fault)
        seen.add(fault_shape(fault)[1])
        shares.append(fault_motion(fault)[2])
        world.tick(fault, FPS)
    assert len(seen) == 6
    assert shares == [0.25, 0.75] * 3


def test_fault_path_stays_put_during_a_hit(world, fault):
    hit(world, fault)
    seed, height = fault_shape(fault)[1], fault_shape(fault)[0]
    for _ in range(10):
        world.tick(fault)
        assert fault_shape(fault)[:2] == (height, seed)


def test_fault_height_knob_moves_the_crack_within_its_drift(world, fault):
    for knob, center in ((0.0, 0.2), (1.0, 0.8)):
        fault.par.Height = knob
        hit(world, fault)
        assert fault_shape(fault)[0] == pytest.approx(center, abs=0.0601)
        world.tick(fault, FPS)


def test_fault_hot_gap_cycles_from_the_first_press(world, fault):
    colors = []
    for _ in range(4):
        hit(world, fault)
        colors.append(fault_gap(fault)[:3])
        world.tick(fault, FPS)
    assert colors[0] == pytest.approx((1.0, 0.5529, 1.0))
    assert colors[1] == pytest.approx((1.0, 0.2588, 0.3020))
    assert colors[2] == pytest.approx((0.9529, 0.8667, 0.2431))
    assert colors[3] == colors[0]


def test_fault_fixed_gap_color(world, fault):
    fault.par.Gapcolor = 'cyan'
    hit(world, fault)
    assert fault_gap(fault)[:3] == pytest.approx((0.2667, 0.9725, 1.0))


def test_fault_retrigger_restarts_with_a_new_path(world, fault):
    hit(world, fault)
    world.tick(fault, 8)
    seed = fault_shape(fault)[1]
    hit(world, fault)
    assert fault_shape(fault)[1] != seed
    assert fault_motion(fault)[0] == pytest.approx(0.15)


def test_fault_retrigger_ignore_keeps_the_path(world, fault):
    fault.par.Retrigger = 'ignore'
    hit(world, fault)
    world.tick(fault, 3)
    seed = fault_shape(fault)[1]
    hit(world, fault)
    assert fault_shape(fault)[1] == seed


def test_fault_returns_exactly_to_rest(world, fault):
    fault_gaps(world, fault)
    assert_fault_at_rest(fault)


def test_fault_panic_and_held_pad_does_not_refire(world, fault):
    fault.par.Trigger = 1.0
    world.tick(fault)
    world.panic = 1.0
    world.tick(fault)
    assert_fault_at_rest(fault)
    world.panic = 0.0
    world.tick(fault, 3)
    assert_fault_at_rest(fault)


def test_fault_reset_and_frame_gap(world, fault):
    hit(world, fault)
    fault.op('reset_watch').module.onPulse()
    assert_fault_at_rest(fault)
    world.tick(fault, 2)
    hit(world, fault)
    assert fault_motion(fault)[2] == 0.25  # reset restarted the alternation
    world.abs_time.frame += 31
    world.tick(fault)
    assert_fault_at_rest(fault)


def test_fault_gate_holds_the_crack_open(world, fault):
    fault.par.Mode = 'gate'
    fault.par.Trigger = 1.0
    world.tick(fault, FPS)
    assert fault_motion(fault)[0] == pytest.approx(0.15)


def test_fault_max_hold_in_gate_mode(world, fault):
    fault.par.Mode = 'gate'
    fault.par.Maxhold = 0.1
    fault.par.Trigger = 1.0
    world.tick(fault, FPS * 2)
    assert_fault_at_rest(fault)


# ------------------------------------------------------------ Fault Plates

@pytest.fixture
def plates(world):
    return world.block('fault_plates')


def plates_layout(block):
    return uniforms(block, 'layout', 0)


def plates_look(block):
    return uniforms(block, 'plates', 0)


def plates_motion(block):
    """Spread (of a plate), tilt, and ink, gathered from the two passes."""
    spread, tilt = uniforms(block, 'layout', 1)[:2]
    return (spread, tilt, plates_look(block)[1], 0.0)


def plates_origin(block):
    return uniforms(block, 'layout', 2)


def plates_gap(block):
    return uniforms(block, 'plates', 1)


def assert_plates_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert plates_layout(block) == (0.25, 0.0, 0.0, 0.0)
    assert uniforms(block, 'layout', 1) == (0.0, 0.0, 0.0, 0.0)
    assert plates_origin(block) == (0.5, 0.5, 0.0, 0.0)
    assert plates_look(block) == (0.0, 0.0, 0.0, 0.0)
    assert plates_gap(block) == (0.0, 0.0, 0.0, 0.0)


def plates_spreads(world, block):
    """Spread per frame from a hard hit until the block is back at rest."""
    hit(world, block)
    spreads = [plates_motion(block)[0]]
    while block.op('switch').par.index.val == 1:
        world.tick(block)
        spreads.append(plates_motion(block)[0])
    return spreads


def test_plates_wiring(plates):
    assert plates.op('layout').inputs == {0: plates.op('in1')}
    assert plates.op('plates').inputs == {0: plates.op('in1'), 1: plates.op('layout')}
    assert plates.op('switch').inputs == {0: plates.op('in1'), 1: plates.op('plates')}
    assert plates.op('out1').inputs[0] is plates.op('switch')


@pytest.mark.parametrize('name, count', [('layout', 3), ('plates', 2)])
def test_plates_shader_uniforms_match_their_entries(plates, name, count):
    node = plates.op(name)
    text = plates.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == count
    for index in range(count):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_plates_detail_sets_only_the_layout_resolution(plates):
    layout = plates.op('layout')
    assert layout.par.format.val == 'rgba16float'
    assert layout.par.outputresolution.eval() == 'quarter'
    plates.par.Detail = 'half'
    assert layout.par.outputresolution.eval() == 'half'
    assert plates.op('plates').par.outputresolution.eval() == 'useinput'
    assert plates.op('plates').par.format.val == 'useinput'


def test_plates_composite_gets_spread_in_wall_heights(world, plates):
    hit(world, plates)
    assert plates_look(plates)[0] == pytest.approx(0.15 / 3.7)


def test_plates_lands_full_on_the_very_first_frame(world, plates):
    hit(world, plates)
    assert plates.op('switch').par.index.val == 1
    size, reach, _, jag = plates_layout(plates)
    assert size == pytest.approx(1.0 / 3.7)
    assert reach == pytest.approx(2.57)
    assert jag == pytest.approx(0.6)
    spread, tilt, ink, _ = plates_motion(plates)
    assert spread == pytest.approx(0.15)
    assert abs(tilt) == pytest.approx(0.06)
    assert ink == pytest.approx(0.009)
    assert plates_gap(plates)[3] == 1.0


def test_plates_velocity_scales_spread_reach_and_ink(world, plates):
    hit(world, plates, 1.0)
    hard_ink = plates_motion(plates)[2]
    world.tick(plates, FPS)
    hit(world, plates, 0.1)
    assert plates_motion(plates)[0] == pytest.approx(0.15 * 0.325)
    assert plates_layout(plates)[1] == pytest.approx(2.57 * 0.415)
    assert plates_motion(plates)[2] < hard_ink


def test_plates_aftershocks_reopen_and_shrink(world, plates):
    spreads = plates_spreads(world, plates)
    peaks = [spreads[i] for i in range(1, len(spreads) - 1)
             if spreads[i - 1] < spreads[i] >= spreads[i + 1] and spreads[i] > 0.02]
    assert len(peaks) == 2
    assert peaks[0] > peaks[1] > 0
    assert spreads[0] > peaks[0]


def test_plates_zero_aftershock_closes_once(world, plates):
    plates.par.Aftershock = 0.0
    spreads = plates_spreads(world, plates)
    assert all(a >= b for a, b in zip(spreads, spreads[1:]))


def test_plates_rock_the_other_way_on_each_jolt(world, plates):
    hit(world, plates)
    signs = [math.copysign(1.0, plates_motion(plates)[1])]
    for _ in range(int(FPS * 0.4)):
        world.tick(plates)
        if plates_motion(plates)[0] > 0.005:
            signs.append(math.copysign(1.0, plates_motion(plates)[1]))
    assert len(set(signs)) == 2


def test_plates_settle_inside_a_beat_at_116(world, plates):
    assert (len(plates_spreads(world, plates)) - 1) / FPS < 60.0 / 116


def test_plates_every_hit_gets_a_new_layout(world, plates):
    seeds = set()
    for _ in range(6):
        hit(world, plates)
        seeds.add(plates_layout(plates)[2])
        world.tick(plates, FPS)
    assert len(seeds) == 6


def test_plates_layout_stays_put_during_a_hit(world, plates):
    hit(world, plates)
    layout, origin = plates_layout(plates)[:3], plates_origin(plates)
    plates.par.Chunks = 1.0
    plates.par.Reach = 0.0
    plates.par.Origin = 0.0
    for _ in range(10):
        world.tick(plates)
        assert plates_layout(plates)[:3] == layout
        assert plates_origin(plates) == origin


def test_plates_chunks_knob_sets_plate_size(world, plates):
    for knob, rows in ((0.0, 1.5), (1.0, 7.0)):
        plates.par.Chunks = knob
        hit(world, plates)
        assert plates_layout(plates)[0] == pytest.approx(1.0 / rows)
        world.tick(plates, FPS)


def test_plates_origin_knob_moves_the_break_within_its_drift(world, plates):
    for knob in (0.0, 0.3, 1.0):
        plates.par.Origin = knob
        hit(world, plates)
        x, y = plates_origin(plates)[:2]
        assert 0.0 <= x <= 1.0
        assert x == pytest.approx(knob, abs=0.0801)
        assert y == pytest.approx(0.5, abs=0.1501)
        world.tick(plates, FPS)


def test_plates_hot_gap_cycles_from_the_first_press(world, plates):
    colors = []
    for _ in range(4):
        hit(world, plates)
        colors.append(plates_gap(plates)[:3])
        world.tick(plates, FPS)
    assert colors[0] == pytest.approx((1.0, 0.5529, 1.0))
    assert colors[1] == pytest.approx((1.0, 0.2588, 0.3020))
    assert colors[2] == pytest.approx((0.9529, 0.8667, 0.2431))
    assert colors[3] == colors[0]


def test_plates_fixed_gap_color(world, plates):
    plates.par.Gapcolor = 'cyan'
    hit(world, plates)
    assert plates_gap(plates)[:3] == pytest.approx((0.2667, 0.9725, 1.0))


def test_plates_ink_thins_as_they_close(world, plates):
    plates.par.Mode = 'gate'
    plates.par.Trigger = 1.0
    world.tick(plates)
    plates.par.Slip = 0.1  # spread 0.03, under the full-ink spread
    world.tick(plates)
    spread, _, ink, _ = plates_motion(plates)
    assert ink == pytest.approx(0.009 * spread / 0.05)


def test_plates_retrigger_restarts_with_a_new_layout(world, plates):
    hit(world, plates)
    world.tick(plates, 8)
    seed = plates_layout(plates)[2]
    hit(world, plates)
    assert plates_layout(plates)[2] != seed
    assert plates_motion(plates)[0] == pytest.approx(0.15)


def test_plates_retrigger_ignore_keeps_the_layout(world, plates):
    plates.par.Retrigger = 'ignore'
    hit(world, plates)
    world.tick(plates, 3)
    seed = plates_layout(plates)[2]
    hit(world, plates)
    assert plates_layout(plates)[2] == seed


def test_plates_returns_exactly_to_rest(world, plates):
    plates_spreads(world, plates)
    assert_plates_at_rest(plates)


def test_plates_panic_and_held_pad_does_not_refire(world, plates):
    plates.par.Trigger = 1.0
    world.tick(plates)
    world.panic = 1.0
    world.tick(plates)
    assert_plates_at_rest(plates)
    world.panic = 0.0
    world.tick(plates, 3)
    assert_plates_at_rest(plates)


def test_plates_reset_and_frame_gap(world, plates):
    hit(world, plates)
    plates.op('reset_watch').module.onPulse()
    assert_plates_at_rest(plates)
    world.tick(plates, 2)
    hit(world, plates)
    assert plates_gap(plates)[:3] == pytest.approx((1.0, 0.5529, 1.0))  # reset restarted the hot cycle
    world.abs_time.frame += 31
    world.tick(plates)
    assert_plates_at_rest(plates)


def test_plates_gate_holds_them_open(world, plates):
    plates.par.Mode = 'gate'
    plates.par.Trigger = 1.0
    world.tick(plates, FPS)
    assert plates_motion(plates)[0] == pytest.approx(0.15)


def test_plates_max_hold_in_gate_mode(world, plates):
    plates.par.Mode = 'gate'
    plates.par.Maxhold = 0.1
    plates.par.Trigger = 1.0
    world.tick(plates, FPS * 2)
    assert_plates_at_rest(plates)


# ------------------------------------------------------------ Confetti Cannon

CONFETTI_KINDS = {'rain': 0.0, 'burst': 1.0, 'corners': 2.0, 'blast': 3.0}


@pytest.fixture
def confetti(world):
    return world.block('confetti_cannon')


def confetti_shot(block, slot=0):
    return uniforms(block, 'confetti', 6 + slot)


def confetti_kinds(block):
    return uniforms(block, 'confetti', 4)


def confetti_live(block):
    return sum(1 for kind in confetti_kinds(block) if kind >= 0)


def assert_confetti_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert confetti_kinds(block) == (-1.0, -1.0, -1.0, -1.0)
    assert uniforms(block, 'confetti', 5) == (0.0, 0.0, 0.0, 0.0)
    for slot in range(4):
        assert confetti_shot(block, slot) == (0.0, 0.0, 0.0, 0.0)


def test_confetti_wiring(confetti):
    assert confetti.op('confetti').inputs[0] is confetti.op('in1')
    assert confetti.op('switch').inputs == {0: confetti.op('in1'), 1: confetti.op('confetti')}
    assert confetti.op('out1').inputs[0] is confetti.op('switch')


def test_confetti_shader_uniforms_match_their_entries(confetti):
    node = confetti.op('confetti')
    text = confetti.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 10
    for index in range(10):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_confetti_first_press_is_airborne_on_its_first_frame(world, confetti):
    hit(world, confetti)
    assert confetti.op('switch').par.index.val == 1
    age, _, velocity, level = confetti_shot(confetti)
    assert age > 0.0
    assert velocity == 1.0
    assert level == 1.0
    assert confetti_kinds(confetti) == (1.0, -1.0, -1.0, -1.0)


@pytest.mark.parametrize('motion', sorted(CONFETTI_KINDS))
def test_confetti_motion_menu_sets_the_shot(world, confetti, motion):
    confetti.par.Motion = motion
    hit(world, confetti)
    assert confetti_kinds(confetti)[0] == CONFETTI_KINDS[motion]


def test_confetti_motion_change_leaves_a_shot_in_the_air_alone(world, confetti):
    confetti.par.Motion = 'rain'
    hit(world, confetti)
    world.tick(confetti, 5)
    confetti.par.Motion = 'blast'
    hit(world, confetti)
    assert confetti_kinds(confetti)[:2] == (0.0, 3.0)


def test_confetti_velocity_reaches_the_shader(world, confetti):
    hit(world, confetti, 0.5)
    assert confetti_shot(confetti)[2] == pytest.approx(0.5)


def test_confetti_knobs_map_to_physical_units(world, confetti):
    hit(world, confetti)
    count, radius, ink, shadow = uniforms(confetti, 'confetti', 0)
    speed, cone, gravity, drag = uniforms(confetti, 'confetti', 1)
    assert (count, ink, shadow) == pytest.approx((264.0, 0.5, 0.5))
    assert radius == pytest.approx(0.02)
    assert speed == pytest.approx(3.0)
    assert cone == pytest.approx(math.radians(47.0))
    assert (gravity, drag) == pytest.approx((1.5, 2.6))
    assert uniforms(confetti, 'confetti', 3)[:2] == (3.0, 1.0)


def test_confetti_tail_runs_hold_plus_release_then_rests(world, confetti):
    hit(world, confetti)
    world.tick(confetti, int(FPS * 1.9))
    assert confetti.op('switch').par.index.val == 1
    assert confetti_shot(confetti)[3] < 1.0
    world.tick(confetti, int(FPS * 0.2))
    assert_confetti_at_rest(confetti)


def test_confetti_age_advances_with_the_clock(world, confetti):
    hit(world, confetti)
    first = confetti_shot(confetti)[0]
    world.tick(confetti, FPS // 2)
    assert confetti_shot(confetti)[0] == pytest.approx(first + 0.5, abs=0.001)


def test_confetti_each_hit_is_a_new_seed(world, confetti):
    hit(world, confetti)
    first = confetti_shot(confetti)[1]
    world.tick(confetti, 5)
    hit(world, confetti)
    assert confetti_shot(confetti, 1)[1] != first
    assert confetti_shot(confetti, 0)[1] == first


def test_confetti_rapid_hits_pile_up_and_cap_at_four(world, confetti):
    seeds = []
    for _ in range(5):
        hit(world, confetti)
        seeds.append(confetti_shot(confetti, confetti_live(confetti) - 1)[1])
        world.tick(confetti, 6)
    assert confetti_live(confetti) == 4
    assert [confetti_shot(confetti, slot)[1] for slot in range(4)] == seeds[1:]


def test_confetti_older_shot_dies_on_its_own_clock(world, confetti):
    hit(world, confetti)
    world.tick(confetti, FPS)
    hit(world, confetti)
    assert confetti_live(confetti) == 2
    world.tick(confetti, int(FPS * 1.1))
    assert confetti_live(confetti) == 1
    assert confetti_shot(confetti)[3] == 1.0 or confetti.op('switch').par.index.val == 1


def test_confetti_retrigger_ignore_adds_no_shot(world, confetti):
    confetti.par.Retrigger = 'ignore'
    hit(world, confetti)
    world.tick(confetti, 5)
    hit(world, confetti)
    assert confetti_live(confetti) == 1


def test_confetti_rain_pour_follows_hold_in_hit_mode(world, confetti):
    confetti.par.Motion = 'rain'
    hit(world, confetti)
    assert uniforms(confetti, 'confetti', 5)[0] == pytest.approx(1.0)


def test_confetti_rain_keeps_pouring_while_gate_is_held(world, confetti):
    confetti.par.Motion = 'rain'
    confetti.par.Mode = 'gate'
    confetti.par.Trigger = 1.0
    world.tick(confetti, FPS)
    held = uniforms(confetti, 'confetti', 5)[0]
    assert held > 1.0
    world.tick(confetti, FPS)
    assert uniforms(confetti, 'confetti', 5)[0] > held
    confetti.par.Trigger = 0.0
    world.tick(confetti)
    stopped = uniforms(confetti, 'confetti', 5)[0]
    world.tick(confetti, 10)
    assert uniforms(confetti, 'confetti', 5)[0] == stopped


def test_confetti_cannons_knob_rounds_and_clamps(world, confetti):
    confetti.par.Cannons = 2.6
    hit(world, confetti)
    assert uniforms(confetti, 'confetti', 3)[0] == 3.0
    confetti.par.Cannons = 99
    world.tick(confetti)
    assert uniforms(confetti, 'confetti', 3)[0] == 5.0


def test_confetti_returns_exactly_to_rest(world, confetti):
    hit(world, confetti)
    world.tick(confetti, FPS * 3)
    assert_confetti_at_rest(confetti)


def test_confetti_panic_and_held_pad_does_not_refire(world, confetti):
    confetti.par.Trigger = 1.0
    world.tick(confetti)
    world.panic = 1.0
    world.tick(confetti)
    assert_confetti_at_rest(confetti)
    world.panic = 0.0
    world.tick(confetti, 3)
    assert_confetti_at_rest(confetti)


def test_confetti_reset_and_frame_gap(world, confetti):
    hit(world, confetti)
    confetti.op('reset_watch').module.onPulse()
    assert_confetti_at_rest(confetti)
    world.tick(confetti, 2)
    hit(world, confetti)
    assert confetti_live(confetti) == 1
    world.abs_time.frame += 31
    world.tick(confetti)
    assert_confetti_at_rest(confetti)


def test_confetti_max_hold_in_gate_mode(world, confetti):
    confetti.par.Mode = 'gate'
    confetti.par.Maxhold = 0.1
    confetti.par.Trigger = 1.0
    world.tick(confetti, FPS * 3)
    assert_confetti_at_rest(confetti)


# ------------------------------------------------------------ Twinkle Cannon

@pytest.fixture
def twinkle(world):
    return world.block('twinkle_cannon')


def twinkle_shot(block, slot=0):
    return uniforms(block, 'twinkle', 7 + slot)


def twinkle_kinds(block):
    return uniforms(block, 'twinkle', 5)


def twinkle_live(block):
    return sum(1 for kind in twinkle_kinds(block) if kind >= 0)


def assert_twinkle_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert twinkle_kinds(block) == (-1.0, -1.0, -1.0, -1.0)
    assert uniforms(block, 'twinkle', 6) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'twinkle', 3)[3] == 0.0
    for slot in range(4):
        assert twinkle_shot(block, slot) == (0.0, 0.0, 0.0, 0.0)


def test_twinkle_wiring(twinkle):
    assert twinkle.op('twinkle').inputs[0] is twinkle.op('in1')
    assert twinkle.op('switch').inputs == {0: twinkle.op('in1'), 1: twinkle.op('twinkle')}
    assert twinkle.op('out1').inputs[0] is twinkle.op('switch')


def test_twinkle_shader_uniforms_match_their_entries(twinkle):
    node = twinkle.op('twinkle')
    text = twinkle.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 11
    for index in range(11):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_twinkle_first_press_is_airborne_on_its_first_frame(world, twinkle):
    hit(world, twinkle)
    assert twinkle.op('switch').par.index.val == 1
    age, _, velocity, level = twinkle_shot(twinkle)
    assert age > 0.0
    assert (velocity, level) == (1.0, 1.0)
    assert twinkle_kinds(twinkle) == (1.0, -1.0, -1.0, -1.0)


@pytest.mark.parametrize('motion', sorted(CONFETTI_KINDS))
def test_twinkle_motion_menu_sets_the_shot(world, twinkle, motion):
    twinkle.par.Motion = motion
    hit(world, twinkle)
    assert twinkle_kinds(twinkle)[0] == CONFETTI_KINDS[motion]


@pytest.mark.parametrize('points,lines', [('four', 2.0), ('six', 3.0), ('eight', 4.0)])
def test_twinkle_points_set_the_spike_lines(world, twinkle, points, lines):
    twinkle.par.Points = points
    hit(world, twinkle)
    assert uniforms(twinkle, 'twinkle', 4)[0] == lines


def test_twinkle_knobs_map_to_physical_units(world, twinkle):
    hit(world, twinkle)
    count, radius, glow, softness = uniforms(twinkle, 'twinkle', 0)
    speed, cone, gravity, drag = uniforms(twinkle, 'twinkle', 1)
    assert (count, radius, glow, softness) == pytest.approx((134.0, 0.075, 0.5, 0.5))
    assert (speed, gravity, drag) == pytest.approx((3.0, 1.5, 2.6))
    assert cone == pytest.approx(math.radians(47.0))
    assert uniforms(twinkle, 'twinkle', 4)[1] == 0.25


def test_twinkle_velocity_reaches_the_shader(world, twinkle):
    hit(world, twinkle, 0.5)
    assert twinkle_shot(twinkle)[2] == pytest.approx(0.5)


def test_twinkle_knob_changes_pace_without_a_jump(world, twinkle):
    twinkle.par.Trigger = 1.0
    twinkle.par.Mode = 'gate'
    world.tick(twinkle, 30)
    before = uniforms(twinkle, 'twinkle', 3)[3]
    twinkle.par.Twinkle = 1.0
    world.tick(twinkle)
    step = uniforms(twinkle, 'twinkle', 3)[3] - before
    assert 0.0 < step == pytest.approx(2.6 / FPS)


def test_twinkle_spikes_turn_with_spin(world, twinkle):
    twinkle.par.Spin = 1.0
    hit(world, twinkle)
    first = uniforms(twinkle, 'twinkle', 2)[1]
    world.tick(twinkle, FPS // 2)
    assert uniforms(twinkle, 'twinkle', 2)[1] == pytest.approx(first + 0.6, abs=0.001)


def test_twinkle_tail_runs_hold_plus_release_then_rests(world, twinkle):
    hit(world, twinkle)
    world.tick(twinkle, int(FPS * 2.3))
    assert twinkle.op('switch').par.index.val == 1
    assert twinkle_shot(twinkle)[3] < 1.0
    world.tick(twinkle, int(FPS * 0.2))
    assert_twinkle_at_rest(twinkle)


def test_twinkle_rapid_hits_pile_up_and_cap_at_four(world, twinkle):
    seeds = []
    for _ in range(5):
        hit(world, twinkle)
        seeds.append(twinkle_shot(twinkle, twinkle_live(twinkle) - 1)[1])
        world.tick(twinkle, 6)
    assert twinkle_live(twinkle) == 4
    assert [twinkle_shot(twinkle, slot)[1] for slot in range(4)] == seeds[1:]


def test_twinkle_older_shot_dies_on_its_own_clock(world, twinkle):
    hit(world, twinkle)
    world.tick(twinkle, FPS)
    hit(world, twinkle)
    assert twinkle_live(twinkle) == 2
    world.tick(twinkle, int(FPS * 1.3))
    assert twinkle_live(twinkle) == 1


def test_twinkle_retrigger_ignore_adds_no_shot(world, twinkle):
    twinkle.par.Retrigger = 'ignore'
    hit(world, twinkle)
    world.tick(twinkle, 5)
    hit(world, twinkle)
    assert twinkle_live(twinkle) == 1


def test_twinkle_rain_keeps_pouring_while_gate_is_held(world, twinkle):
    twinkle.par.Motion = 'rain'
    twinkle.par.Mode = 'gate'
    twinkle.par.Trigger = 1.0
    world.tick(twinkle, FPS)
    held = uniforms(twinkle, 'twinkle', 6)[0]
    world.tick(twinkle, FPS)
    assert uniforms(twinkle, 'twinkle', 6)[0] > held
    twinkle.par.Trigger = 0.0
    world.tick(twinkle)
    stopped = uniforms(twinkle, 'twinkle', 6)[0]
    world.tick(twinkle, 10)
    assert uniforms(twinkle, 'twinkle', 6)[0] == stopped


def test_twinkle_panic_and_held_pad_does_not_refire(world, twinkle):
    twinkle.par.Trigger = 1.0
    world.tick(twinkle)
    world.panic = 1.0
    world.tick(twinkle)
    assert_twinkle_at_rest(twinkle)
    world.panic = 0.0
    world.tick(twinkle, 3)
    assert_twinkle_at_rest(twinkle)


def test_twinkle_reset_and_frame_gap(world, twinkle):
    hit(world, twinkle)
    twinkle.op('reset_watch').module.onPulse()
    assert_twinkle_at_rest(twinkle)
    world.tick(twinkle, 2)
    hit(world, twinkle)
    assert twinkle_live(twinkle) == 1
    world.abs_time.frame += 31
    world.tick(twinkle)
    assert_twinkle_at_rest(twinkle)


def test_twinkle_max_hold_in_gate_mode(world, twinkle):
    twinkle.par.Mode = 'gate'
    twinkle.par.Maxhold = 0.1
    twinkle.par.Trigger = 1.0
    world.tick(twinkle, FPS * 4)
    assert_twinkle_at_rest(twinkle)


# ------------------------------------------------------------ Road Rush

@pytest.fixture
def road(world):
    return world.block('road_rush')


def road_view(block):
    return uniforms(block, 'road', 0)


def road_shape(block):
    return uniforms(block, 'road', 1)


def road_dash(block):
    return uniforms(block, 'road', 2)


def road_misc(block):
    return uniforms(block, 'road', 5)


def road_press(world, block, velocity=1.0, frames=1):
    block.par.Trigger = velocity
    world.tick(block, frames)


def road_crash_frames(block):
    return int(block.par.Release.val * FPS)


def assert_road_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert road_view(block) == (0.004, 1.0, 0.0, 0.0)
    for index in range(1, 6):
        assert uniforms(block, 'road', index) == (0.0, 0.0, 0.0, 0.0)


def road_distance_over(world, block, velocity, frames=30):
    """Road travelled in a short window, unwrapped frame by frame."""
    road_press(world, block, velocity, 20)
    total, last = 0.0, road_shape(block)[2]
    for _ in range(frames):
        world.tick(block)
        now = road_shape(block)[2]
        total += (now - last) % 2.4
        last = now
    return total


def test_road_defaults_are_a_hold_with_a_crash(road):
    assert road.par.Mode.val == 'gate'
    assert road.par.Attack.val == 0.0
    assert road.par.Release.val > 0.0
    assert road.par.Velocitydepth.val == 1.0


def test_road_wiring(road):
    assert road.op('road').inputs[0] is road.op('in1')
    assert road.op('switch').inputs == {0: road.op('in1'), 1: road.op('road')}
    assert road.op('out1').inputs[0] is road.op('switch')


def test_road_shader_uniforms_match_their_entries(road):
    node = road.op('road')
    text = road.op(node.par.pixeldat.val).text
    for index in range(node.seq.vec.numBlocks):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_road_drop_is_moving_on_the_very_first_frame(world, road):
    road_press(world, road)
    assert road.op('switch').par.index.val == 1
    assert road_view(road)[3] == pytest.approx(0.35)
    assert road_misc(road)[0] == 1.0


def test_road_lands_bounces_and_settles_at_the_horizon(world, road):
    road_press(world, road)
    drops = []
    for _ in range(30):
        world.tick(road)
        drops.append(road_view(road)[3])
    assert max(drops) > 1.0
    assert drops[-1] == pytest.approx(1.0)
    road.par.Wander = 0.0
    world.tick(road)
    assert 1.0 / road_view(road)[0] == pytest.approx(0.615)


def test_road_velocity_sets_speed(world, road):
    fast = road_distance_over(world, road, 1.0)
    road_press(world, road, 0.0, 60)
    assert_road_at_rest(road)
    soft = road_distance_over(world, road, 0.2)
    assert soft < fast * 0.6


def test_road_distance_wraps_without_a_visible_jump(world, road):
    road_press(world, road, 1.0, 20)
    for _ in range(600):
        world.tick(road)
        assert 0.0 <= road_shape(road)[2] < 2.4
        assert 0.0 <= road_shape(road)[3] < 2.0


def test_road_steer_moves_the_road_and_leans_the_wall(world, road):
    road.par.Wander = 0.0
    road_press(world, road, 1.0, 20)
    assert road_shape(road)[0] == 0.0
    road.par.Steer = 1.0
    world.tick(road)
    right = road_shape(road)[0]
    right_lean = road_view(road)[2]
    road.par.Steer = 0.0
    world.tick(road)
    assert right > 0.0
    assert road_shape(road)[0] == pytest.approx(-right)
    assert road_view(road)[2] == pytest.approx(-right_lean)


def test_road_hill_moves_the_horizon_and_bends_depth(world, road):
    road.par.Wander = 0.0
    road_press(world, road, 1.0, 20)
    flat = road_view(road)
    road.par.Hill = 1.0
    world.tick(road)
    crest = road_view(road)
    assert flat[1] == pytest.approx(1.0)
    assert 1.0 / crest[0] > 1.0 / flat[0]
    assert crest[1] > 1.0


def test_road_wander_drifts_with_steer_centered(world, road):
    road.par.Wander = 1.0
    road_press(world, road, 1.0, 20)
    seen = set()
    for _ in range(60):
        world.tick(road)
        seen.add(round(road_shape(road)[0], 4))
    assert len(seen) > 30


def test_road_boost_fires_on_its_key_and_dies_away(world, road):
    road_press(world, road, 1.0, 20)
    assert road_dash(road)[0] == pytest.approx(0.45)
    road.par.Boost = 1.0
    world.tick(road)
    assert road_dash(road)[0] == pytest.approx(1.0)
    world.tick(road, 180)
    assert road_dash(road)[0] == pytest.approx(0.45, abs=0.01)


def test_road_held_boost_key_does_not_refire(world, road):
    road_press(world, road, 1.0, 20)
    road.par.Boost = 1.0
    world.tick(road)
    peak = road_dash(road)[0]
    world.tick(road, 20)
    assert road_dash(road)[0] < peak
    road.par.Boost = 0.0
    world.tick(road)
    road.par.Boost = 1.0
    world.tick(road)
    assert road_dash(road)[0] == pytest.approx(peak)


def test_road_boost_raises_speed(world, road):
    road.par.Wander = 0.0
    road_press(world, road, 1.0, 20)
    last = road_shape(road)[2]
    world.tick(road)
    cruise = (road_shape(road)[2] - last) % 2.4
    road.par.Boost = 1.0
    last = road_shape(road)[2]
    world.tick(road)
    assert (road_shape(road)[2] - last) % 2.4 > cruise * 1.3


def test_road_sky_menu_sets_the_colors(world, road):
    road_press(world, road, 1.0, 5)
    blue = uniforms(road, 'road', 3)
    road.par.Sky = 'red'
    world.tick(road)
    assert blue[:3] == pytest.approx((0.1451, 0.4510, 1.0))
    assert uniforms(road, 'road', 3)[:3] == pytest.approx((1.0, 0.2588, 0.3020))


def test_road_crash_flips_up_flat_then_rattles_and_returns(world, road):
    road_press(world, road, 1.0, 30)
    road_press(world, road, 0.0, 1)
    drops, shakes = [], []
    for _ in range(road_crash_frames(road) - 2):
        world.tick(road)
        drops.append(road_view(road)[3])
        shakes.append(max(abs(v) for v in road_misc(road)[1:3]))
    assert drops == sorted(drops, reverse=True)
    assert drops[-1] == 0.0
    assert shakes[0] == 0.0
    assert max(shakes) > 0.0
    world.tick(road, 5)
    assert_road_at_rest(road)


def test_road_crash_knob_zero_has_no_rattle(world, road):
    road.par.Crash = 0.0
    road_press(world, road, 1.0, 30)
    road_press(world, road, 0.0, 1)
    for _ in range(road_crash_frames(road) - 2):
        world.tick(road)
        assert road_misc(road)[1:3] == (0.0, 0.0)


def test_road_press_during_the_crash_climbs_back_from_where_it_was(world, road):
    road_press(world, road, 1.0, 30)
    road_press(world, road, 0.0, 10)
    mid = road_view(road)[3]
    assert 0.0 < mid < 1.0
    road_press(world, road, 1.0, 1)
    assert road_view(road)[3] > mid
    assert road_misc(road)[1:3] == (0.0, 0.0)
    world.tick(road, 30)
    assert road_view(road)[3] == pytest.approx(1.0)


def test_road_first_frame_after_rest_starts_flat_again(world, road):
    road_press(world, road, 1.0, 30)
    road_press(world, road, 0.0, 60)
    assert_road_at_rest(road)
    road_press(world, road)
    assert road_view(road)[3] == pytest.approx(0.35)


def test_road_panic_and_held_pad_does_not_refire(world, road):
    road_press(world, road, 1.0, 10)
    world.panic = 1.0
    world.tick(road)
    assert_road_at_rest(road)
    world.panic = 0.0
    world.tick(road, 10)
    assert_road_at_rest(road)


def test_road_reset_and_frame_gap(world, road):
    road_press(world, road, 1.0, 10)
    road.op('engine').module.reset()
    assert_road_at_rest(road)
    road_press(world, road, 0.0, 1)
    road_press(world, road, 1.0, 10)
    world.abs_time.frame += 100
    world.tick(road)
    assert_road_at_rest(road)


def test_road_max_hold_crashes_a_held_pad(world, road):
    road.par.Maxhold = 0.25
    road_press(world, road, 1.0, 30)
    world.tick(road, road_crash_frames(road))
    assert_road_at_rest(road)


# ------------------------------------------------------------ Lens Flare playing

@pytest.fixture
def flare(world):
    return world.block('lens_flare')


def flare_source(block):
    return uniforms(block, 'flare', 0)


def assert_flare_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert uniforms(block, 'flare', 0) == (0.5, 0.5, 0.0, 1.0)
    assert uniforms(block, 'flare', 1) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'flare', 2) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'composite', 0) == (0.0, 0.0, 0.0, 0.0)


def flare_sweep(world, block, velocity=1.0):
    """Fire one sweep and return the source's (u, v, brightness) and the glare on every frame."""
    hit(world, block, velocity)
    frames = [(flare_source(block)[:3], uniforms(block, 'composite', 0)[0])]
    while block.op('switch').par.index.val == 1:
        world.tick(block)
        frames.append((flare_source(block)[:3], uniforms(block, 'composite', 0)[0]))
    return frames


def test_flare_wiring(flare):
    assert flare.op('extract').inputs[0] is flare.op('in1')
    assert flare.op('blur_x').inputs[0] is flare.op('extract')
    assert flare.op('blur_y').inputs[0] is flare.op('blur_x')
    assert flare.op('flare').inputs == {0: flare.op('in1'), 1: flare.op('blur_y')}
    assert flare.op('composite').inputs == {0: flare.op('in1'), 1: flare.op('flare')}
    assert flare.op('switch').inputs == {0: flare.op('in1'), 1: flare.op('composite')}
    assert flare.op('out1').inputs[0] is flare.op('switch')


def test_flare_passes_and_resolutions(flare):
    assert flare.op('extract').par.outputresolution.eval() == 'eighth'
    assert flare.op('blur_x').par.outputresolution.eval() == 'useinput'
    assert uniforms(flare, 'blur_x', 0)[:2] == (1.0, 0.0)
    assert uniforms(flare, 'blur_y', 0)[:2] == (0.0, 1.0)
    assert flare.op('flare').par.format.eval() == 'rgba16float'
    assert flare.op('composite').par.format.eval() == 'useinput'
    assert flare.op('flare').par.outputresolution.eval() == 'quarter'
    flare.par.Detail = 'half'
    assert flare.op('flare').par.outputresolution.eval() == 'half'


def test_flare_shader_uniforms_match_their_entries(flare):
    for name in ('extract', 'blur_x', 'blur_y', 'flare', 'composite'):
        node = flare.op(name)
        text = flare.op(node.par.pixeldat.val).text
        for index in range(node.seq.vec.numBlocks):
            uniform = getattr(node.par, 'vec%dname' % index).val
            assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_flare_defaults_are_a_warm_sweeping_hit(flare):
    assert flare.par.Mode.eval() == 'hit'
    assert flare.par.Attack.eval() == 0.0
    assert flare.par.Release.eval() == 1.6
    assert flare.par.Warmth.eval() == 0.8
    assert flare.par.Direction.eval() == 'alternate'


def test_flare_lands_on_the_first_frame(world, flare):
    world.tick(flare, 3)
    hit(world, flare)
    assert flare.op('switch').par.index.val == 1
    u, v, bright, size = flare_source(flare)
    assert u < 0.0, 'the source starts off the left edge'
    assert bright > 0.0
    assert uniforms(flare, 'flare', 1)[0] > 0.0, 'streak'
    assert uniforms(flare, 'composite', 0)[2] == 1.0, 'mix'


def test_flare_sweeps_across_the_wall_and_returns_to_rest(world, flare):
    frames = flare_sweep(world, flare)
    xs = [f[0][0] for f in frames[:-1]]
    assert xs[0] < 0.0 and max(xs) > 1.0
    assert xs == sorted(xs), 'left to right, never backing up'
    # Release is 1.6 s at 60 fps
    assert abs(len(frames) - 1.6 * FPS) <= 3
    assert_flare_at_rest(flare)


def test_flare_brightest_and_glariest_crossing_the_center(world, flare):
    frames = flare_sweep(world, flare)[:-1]
    peak = max(frames, key=lambda f: f[0][2])
    assert 0.35 < peak[0][0] < 0.65
    glare_peak = max(frames, key=lambda f: f[1])
    assert 0.4 < glare_peak[0][0] < 0.6
    assert frames[0][1] == 0.0, 'no glare while the source is off the wall'
    assert frames[-1][0][2] < 0.05, 'fades out before it ends'


def test_flare_eases_in_and_out(world, flare):
    xs = [f[0][0] for f in flare_sweep(world, flare)[:-1]]
    steps = [b - a for a, b in zip(xs, xs[1:])]
    middle = steps[len(steps) // 2]
    assert steps[1] < middle and steps[-1] < middle


def test_flare_direction_alternates_by_default(world, flare):
    first = flare_sweep(world, flare)
    second = flare_sweep(world, flare)
    assert first[0][0][0] < 0.0 < 1.0 < first[-2][0][0]
    assert second[0][0][0] > 1.0 > 0.0 > second[-2][0][0]


@pytest.mark.parametrize('direction, starts_left', [('right', True), ('left', False)])
def test_flare_fixed_directions(world, flare, direction, starts_left):
    flare.par.Direction = direction
    for _ in range(2):
        frames = flare_sweep(world, flare)
        assert (frames[0][0][0] < 0.0) == starts_left


def test_flare_path_follows_height_and_angle(world, flare):
    flare.par.Height = 0.2
    flare.par.Angle = 0.5
    frames = flare_sweep(world, flare)[:-1]
    ys = [f[0][1] for f in frames]
    assert max(ys) - min(ys) < 1e-9
    assert abs(ys[0] - 0.2) <= 0.04 + 1e-9
    flare.par.Angle = 1.0
    flare.par.Direction = 'right'
    frames = flare_sweep(world, flare)[:-1]
    assert frames[-1][0][1] > frames[0][0][1], 'tilted upward going right'


def test_flare_velocity_scales_brightness_streak_and_glare(world, flare):
    hard = flare_sweep(world, flare, 1.0)
    hit(world, flare, 1.0)
    hard_streak = uniforms(flare, 'flare', 1)[0]
    world.tick(flare, FPS * 3)
    soft = flare_sweep(world, flare, 0.2)
    hit(world, flare, 0.2)
    soft_streak = uniforms(flare, 'flare', 1)[0]
    assert max(f[0][2] for f in soft) < max(f[0][2] for f in hard)
    assert max(f[1] for f in soft) < max(f[1] for f in hard)
    assert soft_streak < hard_streak


def test_flare_retrigger_restarts_the_sweep(world, flare):
    flare.par.Direction = 'right'
    hit(world, flare)
    world.tick(flare, 50)
    assert flare_source(flare)[0] > 0.5
    hit(world, flare)
    assert flare_source(flare)[0] < 0.0


def test_flare_retrigger_ignore_finishes_the_sweep(world, flare):
    flare.par.Retrigger = 'ignore'
    flare.par.Direction = 'right'
    hit(world, flare)
    world.tick(flare, 50)
    before = flare_source(flare)[0]
    hit(world, flare)
    assert flare_source(flare)[0] >= before


def test_flare_attack_fades_the_source_in_at_the_edge(world, flare):
    flare.par.Attack = 0.5
    flare.par.Trigger = 1.0
    world.tick(flare)
    world.tick(flare, 5)
    early = flare_source(flare)
    world.tick(flare, 20)
    later = flare_source(flare)
    assert early[0] == later[0] < 0.0
    assert 0.0 < early[2] < later[2]


def test_flare_look_controls_reach_the_shaders(world, flare):
    flare.par.Ghosts = 0.3
    flare.par.Imageghosts = 0.9
    flare.par.Dirt = 0.7
    flare.par.Starburst = 0.2
    flare.par.Warmth = 0.1
    flare.par.Mix = 0.5
    flare.par.Size = 1.0
    flare.par.Threshold = 1.0
    hit(world, flare)
    assert uniforms(flare, 'flare', 1)[1:] == (0.3, 0.9, 0.7)
    warmth, _, star, seed = uniforms(flare, 'flare', 2)
    assert (warmth, star) == (0.1, 0.2)
    assert flare_source(flare)[3] == pytest.approx(1.6)
    assert uniforms(flare, 'composite', 0)[1:3] == (0.1, 0.5)
    assert uniforms(flare, 'extract', 0)[:2] == (pytest.approx(0.95), 0.15)


def test_flare_streak_off_at_zero(world, flare):
    flare.par.Streak = 0.0
    hit(world, flare)
    assert uniforms(flare, 'flare', 1)[0] == 0.0


def test_flare_panic_and_held_pad_does_not_refire(world, flare):
    flare.par.Trigger = 1.0
    world.tick(flare, 10)
    world.panic = 1.0
    world.tick(flare)
    assert_flare_at_rest(flare)
    world.panic = 0.0
    world.tick(flare, 10)
    assert_flare_at_rest(flare)


def test_flare_reset_and_frame_gap(world, flare):
    hit(world, flare)
    world.tick(flare, 10)
    flare.op('engine').module.reset()
    assert_flare_at_rest(flare)
    hit(world, flare)
    world.tick(flare, 10)
    world.abs_time.frame += 100
    world.tick(flare)
    assert_flare_at_rest(flare)


def test_flare_first_press_after_load_and_after_reset_both_sweep(world, flare):
    first = flare_sweep(world, flare)
    flare.op('engine').module.reset()
    again = flare_sweep(world, flare)
    assert first[0][0][0] < 0.0 and again[0][0][0] < 0.0, 'reset puts Alternate back to left-to-right'


# ------------------------------------------------------------ Window Shades playing

@pytest.fixture
def shades(world):
    return world.block('window_shades')


def assert_shades_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert uniforms(block, 'shades', 3) == (0.0, 0.0, 0.0, 0.0)
    assert uniforms(block, 'shades', 4)[3] == 0.0, 'mix'
    assert uniforms(block, 'shades', 1)[1] == -1.0


def shades_press(world, block, velocity=1.0, frames=1):
    block.par.Trigger = velocity
    world.tick(block, frames)


def test_shades_wiring(shades):
    assert shades.op('shades').inputs[0] is shades.op('in1')
    assert shades.op('switch').inputs == {0: shades.op('in1'), 1: shades.op('shades')}
    assert shades.op('out1').inputs[0] is shades.op('switch')
    assert shades.op('shades').par.outputresolution.eval() == 'useinput'


def test_shades_shader_uniforms_match_their_entries(shades):
    node = shades.op('shades')
    text = shades.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 10
    for index in range(node.seq.vec.numBlocks):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec4 %s;' % uniform, text), uniform


def test_shades_menus_match_the_shader_indices(shades):
    effect = shades.op('effect').module
    assert effect.PATTERNS == ['sweepright', 'sweepleft', 'centerout', 'edgesin', 'diagonal',
                               'checker', 'rows', 'rain', 'spiral', 'random']
    assert effect.DIRECTIONS == ['down', 'up', 'right', 'left', 'curtains', 'alternate']
    assert effect.EXITS == ['drop', 'rollup', 'cut']
    assert effect.FILLS == ['flat', 'duotone', 'halftone']
    assert effect.LAYOUTS == ['random', 'columns', 'rows', 'diagonal', 'checker']


def test_shades_defaults_are_a_held_drop_through(shades):
    assert shades.par.Mode.eval() == 'gate'
    assert shades.par.Exit.eval() == 'drop'
    assert shades.par.Fill.eval() == 'flat'
    assert shades.par.Colorset.eval() == 'full'


def test_shades_press_lands_on_the_first_frame(world, shades):
    world.tick(shades, 3)
    shades_press(world, shades)
    assert shades.op('switch').par.index.val == 1
    columns, rows, seed, count = uniforms(shades, 'shades', 0)
    assert columns >= 2 and rows >= 1 and count == 5
    held, leaving, drop, spread = uniforms(shades, 'shades', 1)
    assert held == 0.0 and leaving == -1.0 and drop > 0.0
    assert uniforms(shades, 'shades', 3)[3] == 1.0, 'full coverage at full velocity'
    assert uniforms(shades, 'shades', 4)[3] == 1.0, 'mix'


def test_shades_square_cells_follow_the_wall(world, shades):
    shades.par.Columns = 1.0
    shades_press(world, shades)
    assert uniforms(shades, 'shades', 0)[:2] == (48, round(48 / (6720 / 2048)))


def test_shades_free_rows(world, shades):
    shades.par.Cells = 'free'
    shades.par.Columns = 0.0
    shades.par.Rows = 1.0
    shades_press(world, shades)
    assert uniforms(shades, 'shades', 0)[:2] == (2, 24)


def test_shades_hold_while_pressed_and_clock_runs(world, shades):
    shades_press(world, shades, 1.0, FPS * 3)
    held = uniforms(shades, 'shades', 1)[0]
    assert held == pytest.approx(3.0 - 1.0 / FPS)
    assert uniforms(shades, 'shades', 1)[1] == -1.0
    assert uniforms(shades, 'shades', 4)[0] == 1.0, 'frames fully in'
    assert shades.op('switch').par.index.val == 1


def test_shades_frames_grow_in_with_the_first_drop(world, shades):
    shades_press(world, shades)
    assert uniforms(shades, 'shades', 4)[0] == 0.0
    world.tick(shades, 5)
    assert 0.0 < uniforms(shades, 'shades', 4)[0] < 1.0


def test_shades_exit_runs_over_release_then_rests(world, shades):
    shades_press(world, shades, 1.0, 30)
    shades_press(world, shades, 0.0, 1)
    leaving = uniforms(shades, 'shades', 1)[1]
    assert 0.0 <= leaving < 0.1
    world.tick(shades, int(0.35 * FPS))
    assert 0.4 < uniforms(shades, 'shades', 1)[1] < 0.6
    assert uniforms(shades, 'shades', 2)[2] == 0, 'drop through'
    world.tick(shades, FPS)
    assert_shades_at_rest(shades)


def test_shades_frames_thin_at_the_end_of_the_exit(world, shades):
    shades_press(world, shades, 1.0, 30)
    shades_press(world, shades, 0.0, int(0.65 * FPS))
    assert uniforms(shades, 'shades', 4)[0] < 0.2


def test_shades_roll_up_exit(world, shades):
    shades.par.Exit = 'rollup'
    shades_press(world, shades, 1.0, 30)
    shades_press(world, shades, 0.0, 5)
    assert uniforms(shades, 'shades', 2)[2] == 1
    world.tick(shades, FPS)
    assert_shades_at_rest(shades)


def test_shades_hard_cut_exit_is_instant(world, shades):
    shades.par.Exit = 'cut'
    shades_press(world, shades, 1.0, 30)
    shades_press(world, shades, 0.0, 1)
    assert_shades_at_rest(shades)


def test_shades_hit_mode_holds_then_exits(world, shades):
    shades.par.Mode = 'hit'
    hit(world, shades)
    world.tick(shades, int(0.5 * FPS))
    assert uniforms(shades, 'shades', 1)[1] == -1.0
    world.tick(shades, int(0.2 * FPS))
    assert uniforms(shades, 'shades', 1)[1] >= 0.0
    world.tick(shades, FPS)
    assert_shades_at_rest(shades)


def test_shades_velocity_scales_coverage_and_bounce(world, shades):
    shades_press(world, shades, 1.0)
    hard = uniforms(shades, 'shades', 3)
    shades_press(world, shades, 0.0, FPS * 2)
    shades_press(world, shades, 0.2)
    soft = uniforms(shades, 'shades', 3)
    assert soft[3] < hard[3]
    assert soft[2] < hard[2]


def test_shades_layout_holds_for_the_press(world, shades):
    shades_press(world, shades, 1.0, 5)
    before = uniforms(shades, 'shades', 0), uniforms(shades, 'shades', 2)
    shades.par.Columns = 1.0
    shades.par.Pattern = 'spiral'
    shades.par.Direction = 'curtains'
    world.tick(shades, 5)
    assert (uniforms(shades, 'shades', 0), uniforms(shades, 'shades', 2)[:2]) == (before[0], before[1][:2])


def test_shades_menus_reach_the_shader(world, shades):
    shades.par.Pattern = 'spiral'
    shades.par.Direction = 'curtains'
    shades.par.Fill = 'halftone'
    shades.par.Colorlayout = 'checker'
    shades.par.Colorset = 'pinkyellow'
    shades_press(world, shades)
    assert uniforms(shades, 'shades', 2) == (8, 4, 0, 2)
    assert uniforms(shades, 'shades', 4)[1] == 4
    assert uniforms(shades, 'shades', 0)[3] == 2
    colors = {uniforms(shades, 'shades', i)[:3] for i in range(5, 10)}
    assert colors == {(1.0, 0.5529, 1.0), (0.9529, 0.8667, 0.2431)}


def test_shades_fill_follows_the_knob_live(world, shades):
    shades_press(world, shades, 1.0, 3)
    shades.par.Fill = 'duotone'
    world.tick(shades)
    assert uniforms(shades, 'shades', 2)[3] == 1


def test_shades_colors_shift_between_presses(world, shades):
    firsts = set()
    for _ in range(12):
        shades_press(world, shades, 1.0, 2)
        firsts.add(uniforms(shades, 'shades', 5)[:3])
        shades.par.Exit = 'cut'
        shades_press(world, shades, 0.0, 2)
    assert len(firsts) > 1


def test_shades_look_controls_scale(world, shades):
    shades.par.Frame = 1.0
    shades.par.Shadow = 0.5
    shades.par.Spread = 1.0
    shades.par.Droptime = 0.0
    shades_press(world, shades)
    frame, shadow, _, _ = uniforms(shades, 'shades', 3)
    assert frame == pytest.approx(0.12) and shadow == pytest.approx(0.075)
    assert uniforms(shades, 'shades', 1)[2:] == (pytest.approx(0.08), pytest.approx(1.5))


def test_shades_retrigger_restarts_the_clock(world, shades):
    shades.par.Mode = 'hit'
    hit(world, shades)
    world.tick(shades, 20)
    hit(world, shades)
    assert uniforms(shades, 'shades', 1)[0] == 0.0


def test_shades_panic_and_held_pad_does_not_refire(world, shades):
    shades_press(world, shades, 1.0, 10)
    world.panic = 1.0
    world.tick(shades)
    assert_shades_at_rest(shades)
    world.panic = 0.0
    world.tick(shades, 10)
    assert_shades_at_rest(shades)


def test_shades_reset_and_frame_gap(world, shades):
    shades_press(world, shades, 1.0, 10)
    shades.op('engine').module.reset()
    assert_shades_at_rest(shades)
    shades_press(world, shades, 0.0, 1)
    shades_press(world, shades, 1.0, 10)
    world.abs_time.frame += 100
    world.tick(shades)
    assert_shades_at_rest(shades)


def test_shades_max_hold(world, shades):
    shades.par.Maxhold = 0.25
    shades_press(world, shades, 1.0, FPS * 2)
    assert_shades_at_rest(shades)


def test_shades_shuffle_picks_a_new_pattern_each_press(world, shades):
    shades.par.Pattern = 'shuffle'
    shades.par.Exit = 'cut'
    picked = []
    for _ in range(40):
        shades_press(world, shades, 1.0, 2)
        picked.append(uniforms(shades, 'shades', 2)[0])
        shades_press(world, shades, 0.0, 2)
    assert all(0 <= p < 10 for p in picked)
    assert all(a != b for a, b in zip(picked, picked[1:])), 'never the same pattern twice in a row'
    assert len(set(picked)) >= 6


def test_shades_shuffle_holds_its_pick_for_the_press(world, shades):
    shades.par.Pattern = 'shuffle'
    shades_press(world, shades, 1.0, 2)
    first = uniforms(shades, 'shades', 2)[0]
    world.tick(shades, 30)
    assert uniforms(shades, 'shades', 2)[0] == first


# ------------------------------------------------------------ Polaroid playing

POLAROID_SLOTS = ['photo_a', 'photo_b', 'photo_c']
POLAROID_PHOTOS = ['a.png', 'b.jpg', 'c.png', 'd.PNG', 'e.jpeg']


@pytest.fixture
def photo_folder(tmp_path):
    folder = tmp_path / 'photos'
    folder.mkdir()
    for name in POLAROID_PHOTOS + ['notes.txt', '.hidden.png']:
        (folder / name).write_text('x')
    return folder


@pytest.fixture
def polaroid(world, photo_folder):
    block = world.block('polaroid')
    block.par.Folder = str(photo_folder)
    world.tick(block)
    return block


def polaroid_card(block, index):
    """(card, pose, fall) uniforms of the current (0) or outgoing (1) card."""
    base = 1 + 3 * index
    return tuple(uniforms(block, 'polaroid', base + i) for i in range(3))


def polaroid_shown(block, index):
    slot = int(polaroid_card(block, index)[0][0])
    return None if slot == 0 else block.op(POLAROID_SLOTS[slot - 1]).par.file.val


def polaroid_press(world, block, velocity=1.0, frames=1):
    block.par.Trigger = velocity
    world.tick(block, frames)


def polaroid_tap(world, block):
    polaroid_press(world, block, 1.0, 2)
    polaroid_press(world, block, 0.0, 2)


def assert_polaroid_at_rest(block):
    assert block.op('switch').par.index.val == 0
    for index in range(2):
        assert polaroid_card(block, index) == ((0.0,) * 4,) * 3
    assert uniforms(block, 'polaroid', 8)[0] == 0.0, 'spill'


def test_polaroid_wiring(polaroid):
    node = polaroid.op('polaroid')
    assert node.type == 'glslmultiTOP'
    assert node.inputs == {0: polaroid.op('in1'), 1: polaroid.op('photo_a'),
                           2: polaroid.op('photo_b'), 3: polaroid.op('photo_c')}
    assert polaroid.op('switch').inputs == {0: polaroid.op('in1'), 1: node}
    assert polaroid.op('out1').inputs[0] is polaroid.op('switch')
    assert node.par.outputresolution.eval() == 'useinput'
    for name in POLAROID_SLOTS:
        assert polaroid.op(name).type == 'moviefileinTOP'
        assert polaroid.op(name).par.playmode.val == 'specify'


def test_polaroid_shader_uniforms_match_their_entries(polaroid):
    node = polaroid.op('polaroid')
    declared = re.findall(r'uniform\s+vec4\s+(\w+)', polaroid.op('polaroid_shader').text)
    assert node.seq.vec.numBlocks == len(declared) == 9
    assert [getattr(node.par, 'vec%dname' % i).val for i in range(9)] == declared


def test_polaroid_defaults(world):
    block = world.block('polaroid')
    assert block.par.Mode.eval() == 'hit'
    assert block.par.Hold.eval() == 10.0
    assert block.par.Region.eval() == 'left'
    assert block.par.Clip.eval() is True
    assert block.par.Folder.eval() == ''
    effect = block.op('effect').module
    assert effect._folder_path(block) == ''
    block.par.Folder = 'photos'
    assert effect._folder_path(block) == str(Path(world.project.folder) / 'photos')


def test_polaroid_regions_use_the_configured_side_width(polaroid):
    effect = polaroid.op('effect').module
    share = 1440 / 6720
    assert effect.region(polaroid) == pytest.approx((0.0, 0.0, share, 1.0))
    polaroid.par.Region = 'right'
    assert effect.region(polaroid) == pytest.approx((1.0 - share, 0.0, share, 1.0))
    polaroid.par.Sidewidth = 0.25
    assert effect.region(polaroid) == (0.75, 0.0, 0.25, 1.0)
    polaroid.par.Region = 'custom'
    polaroid.par.Posx, polaroid.par.Posy, polaroid.par.Width, polaroid.par.Height = 0.4, 0.1, 0.2, 0.5
    assert effect.region(polaroid) == (0.4, 0.1, 0.2, 0.5)


def test_polaroid_preloads_before_the_first_press(polaroid, photo_folder):
    slot = polaroid.op('photo_a')
    assert slot.preloads == [slot.par.file.val]
    assert Path(slot.par.file.val).name in POLAROID_PHOTOS
    assert polaroid.op('switch').par.index.val == 0


def test_polaroid_lands_on_the_first_frame(world, polaroid):
    preloaded = polaroid.op('photo_a').par.file.val
    polaroid_press(world, polaroid)
    assert polaroid.op('switch').par.index.val == 1
    card, pose, fall = polaroid_card(polaroid, 0)
    assert card == (1.0, 0.75, 0.0, 1.0), 'slot 1, fallback aspect, undeveloped, full flash'
    assert pose[3] == 0.0
    assert polaroid_shown(polaroid, 0) == preloaded
    assert uniforms(polaroid, 'polaroid', 8)[0] == pytest.approx(0.25 * 0.6)
    following = polaroid.op('photo_b').par.file.val
    assert following and following != preloaded, 'the next photo preloads while this one shows'
    assert polaroid.op('photo_b').preloads == [following]


def test_polaroid_velocity_scales_the_flash(world, polaroid):
    polaroid_press(world, polaroid, 0.5)
    assert polaroid_card(polaroid, 0)[0][3] == pytest.approx(0.4 + 0.6 * 0.5)


def test_polaroid_flash_fades_and_film_develops(world, polaroid):
    polaroid_press(world, polaroid)
    world.tick(polaroid, FPS)
    card = polaroid_card(polaroid, 0)[0]
    assert card[3] < 0.01
    assert uniforms(polaroid, 'polaroid', 8)[0] < 0.01
    assert card[2] == pytest.approx(0.25, abs=0.01)
    world.tick(polaroid, FPS * 4)
    assert polaroid_card(polaroid, 0)[0][2] == 1.0


def test_polaroid_aspect_comes_from_the_file_once_open(world, polaroid):
    polaroid_press(world, polaroid)
    slot = polaroid.op('photo_a')
    slot.isOpen, slot.fileWidth, slot.fileHeight = True, 1000, 1500
    world.tick(polaroid)
    assert polaroid_card(polaroid, 0)[0][1] == pytest.approx(2 / 3)


def test_polaroid_holds_then_falls_then_rests(world, polaroid):
    polaroid_press(world, polaroid)
    polaroid.par.Trigger = 0.0
    world.tick(polaroid, int(FPS * 9.9))
    assert polaroid_card(polaroid, 0)[1][3] == 0.0
    world.tick(polaroid, int(FPS * 0.5))
    falling = polaroid_card(polaroid, 0)[1][3]
    assert 0.0 < falling < 1.0
    world.tick(polaroid, FPS * 2)
    assert_polaroid_at_rest(polaroid)


def test_polaroid_new_press_drops_the_old_card(world, polaroid):
    polaroid_tap(world, polaroid)
    world.tick(polaroid, FPS)
    first = polaroid_shown(polaroid, 0)
    polaroid_press(world, polaroid)
    assert polaroid_shown(polaroid, 1) == first
    assert polaroid_shown(polaroid, 0) != first
    assert polaroid_card(polaroid, 0)[0][2:] == (0.0, 1.0), 'new card starts fresh'
    assert polaroid_card(polaroid, 1)[0][2] > 0.0, 'old card keeps its development'
    world.tick(polaroid, int(FPS * 0.5))
    assert 0.0 < polaroid_card(polaroid, 1)[1][3] < 1.0
    world.tick(polaroid, FPS)
    assert polaroid_card(polaroid, 1)[0][0] == 0.0, 'gone after Release seconds'
    assert polaroid_card(polaroid, 0)[1][3] == 0.0, 'the new card stays up'


def test_polaroid_fast_presses_never_share_a_slot(world, polaroid):
    for _ in range(12):
        polaroid_tap(world, polaroid)
        current = int(polaroid_card(polaroid, 0)[0][0])
        outgoing = int(polaroid_card(polaroid, 1)[0][0])
        effect = polaroid.op('effect').module
        used = {current, outgoing, effect._next_slot + 1}
        assert len(used) == 3


def test_polaroid_shuffle_plays_every_photo_before_repeating(world, polaroid):
    shown = []
    for _ in range(30):
        polaroid_tap(world, polaroid)
        shown.append(Path(polaroid_shown(polaroid, 0)).name)
    assert sorted(shown[:5]) == sorted(POLAROID_PHOTOS)
    assert sorted(shown[5:10]) == sorted(POLAROID_PHOTOS)
    assert all(a != b for a, b in zip(shown, shown[1:]))


def test_polaroid_in_order(world, photo_folder):
    block = world.block('polaroid')
    block.par.Folder = str(photo_folder)
    block.par.Order = 'inorder'
    world.tick(block)
    shown = []
    for _ in range(6):
        polaroid_tap(world, block)
        shown.append(Path(polaroid_shown(block, 0)).name)
    assert shown == sorted(POLAROID_PHOTOS) + [sorted(POLAROID_PHOTOS)[0]]


def test_polaroid_fixed_photo_overrides_the_preload(world, polaroid):
    polaroid.par.Photo = 2
    for _ in range(3):
        polaroid_tap(world, polaroid)
        assert Path(polaroid_shown(polaroid, 0)).name == sorted(POLAROID_PHOTOS)[1]


def test_polaroid_empty_folder_passes_through(world, polaroid, tmp_path):
    (tmp_path / 'empty').mkdir()
    polaroid.par.Folder = str(tmp_path / 'empty')
    polaroid_press(world, polaroid, 1.0, 5)
    assert polaroid.op('switch').par.index.val == 0


def test_polaroid_folder_change_is_picked_up(world, polaroid, tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'z.png').write_text('x')
    polaroid.par.Folder = str(other)
    polaroid_tap(world, polaroid)
    assert Path(polaroid_shown(polaroid, 0)).name == 'z.png'


def test_polaroid_look_controls_reach_the_shader(world, polaroid):
    polaroid.par.Size, polaroid.par.Tone, polaroid.par.Mottle, polaroid.par.Shadow = 0.5, 0.1, 0.2, 0.3
    polaroid.par.Clip = False
    polaroid.par.Mix = 0.7
    polaroid_press(world, polaroid)
    assert uniforms(polaroid, 'polaroid', 7) == (0.5, 0.1, 0.2, 0.3)
    assert uniforms(polaroid, 'polaroid', 8)[1:3] == (0.0, 0.7)


def test_polaroid_tilt_and_scatter_off_sit_square(world, polaroid):
    polaroid.par.Tilt = 0.0
    polaroid.par.Scatter = 0.0
    polaroid_press(world, polaroid)
    assert polaroid_card(polaroid, 0)[1][:3] == (0.0, 0.0, 0.0)


def test_polaroid_gate_holds_while_pressed_then_falls(world, polaroid):
    polaroid.par.Mode = 'gate'
    polaroid_press(world, polaroid, 1.0, FPS * 15)
    assert polaroid_card(polaroid, 0)[1][3] == 0.0
    polaroid_press(world, polaroid, 0.0, int(FPS * 0.5))
    assert polaroid_card(polaroid, 0)[1][3] > 0.0
    world.tick(polaroid, FPS)
    assert_polaroid_at_rest(polaroid)


def test_polaroid_max_hold(world, polaroid):
    polaroid.par.Mode = 'gate'
    polaroid.par.Maxhold = 0.25
    polaroid_press(world, polaroid, 1.0, FPS * 2)
    assert_polaroid_at_rest(polaroid)


def test_polaroid_panic_and_held_pad_does_not_refire(world, polaroid):
    polaroid_press(world, polaroid, 1.0, 10)
    world.panic = 1.0
    world.tick(polaroid)
    assert_polaroid_at_rest(polaroid)
    world.panic = 0.0
    world.tick(polaroid, 10)
    assert_polaroid_at_rest(polaroid)


def test_polaroid_reset_and_frame_gap(world, polaroid):
    polaroid_press(world, polaroid, 1.0, 10)
    polaroid.op('engine').module.reset()
    assert_polaroid_at_rest(polaroid)
    polaroid_press(world, polaroid, 0.0, 1)
    polaroid_press(world, polaroid, 1.0, 10)
    world.abs_time.frame += 100
    world.tick(polaroid)
    assert_polaroid_at_rest(polaroid)
    polaroid_press(world, polaroid, 0.0, 1)
    polaroid_press(world, polaroid, 1.0, 1)
    assert polaroid.op('switch').par.index.val == 1, 'plays again after a reset'


# ------------------------------------------------------------ Wave Warp playing

@pytest.fixture
def warp(world):
    return world.block('wave_warp')


def wave(block):
    return uniforms(block, 'warp', 0)


def warp_motion(block):
    return uniforms(block, 'warp', 1)


def warp_look(block):
    return uniforms(block, 'warp', 2)


def assert_warp_at_rest(block):
    assert block.op('switch').par.index.val == 0
    assert wave(block) == (0.0, 1.0, 0.0, 0.0)
    assert warp_motion(block) == (0.0, 0.0, 0.0, 0.0)
    assert warp_look(block) == (0.0, 0.0, 0.0, 0.0)


def test_warp_wiring(warp):
    assert warp.op('warp').inputs[0] is warp.op('in1')
    assert warp.op('switch').inputs == {0: warp.op('in1'), 1: warp.op('warp')}
    assert warp.op('out1').inputs[0] is warp.op('switch')


def test_warp_shader_uniforms_match_their_entries(warp):
    node = warp.op('warp')
    text = warp.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 3
    for index in range(3):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_warp_lands_on_the_very_first_frame(world, warp):
    hit(world, warp)
    assert warp.op('switch').par.index.val == 1
    assert warp_motion(warp)[0] == pytest.approx(0.35 * 0.12)
    assert warp_motion(warp)[2] == 0.0, 'the pluck starts at its widest swing'
    assert wave(warp)[1] == pytest.approx(0.02 * 100 ** 0.5)
    assert warp_look(warp)[1] == 1.0


def test_warp_velocity_scales_amplitude(world, warp):
    hit(world, warp, 1.0)
    hard = warp_motion(warp)[0]
    world.tick(warp, FPS * 2)
    hit(world, warp, 0.25)
    assert warp_motion(warp)[0] == pytest.approx(hard * 0.25)


def test_warp_settles_and_stretches_while_releasing(world, warp):
    hit(world, warp)
    amps, lengths = [], []
    for _ in range(30):
        world.tick(warp)
        amps.append(warp_motion(warp)[0])
        lengths.append(wave(warp)[1])
    assert amps == sorted(amps, reverse=True) and amps[-1] < amps[0]
    assert lengths == sorted(lengths) and lengths[-1] > lengths[0]


def test_warp_relax_off_keeps_the_wavelength(world, warp):
    warp.par.Relax = 0.0
    hit(world, warp)
    first = wave(warp)[1]
    world.tick(warp, 20)
    assert wave(warp)[1] == pytest.approx(first)


def test_warp_speed_is_cycles_per_second(world, warp):
    warp.par.Mode = 'gate'
    warp.par.Speed = 0.5
    warp.par.Trigger = 1.0
    world.tick(warp)
    start = wave(warp)[2]
    world.tick(warp, 15)
    assert wave(warp)[2] - start == pytest.approx(math.tau * 0.5 * 0.25)
    warp.par.Speed = -1.0
    before = wave(warp)[2]
    world.tick(warp, 6)
    assert (wave(warp)[2] - before) % math.tau == pytest.approx(math.tau - math.tau * 0.1)


def test_warp_each_hit_replucks_the_standing_wave(world, warp):
    warp.par.Standing = 1.0
    hit(world, warp)
    world.tick(warp, 10)
    assert warp_motion(warp)[2] > 0.0
    hit(world, warp)
    assert warp_motion(warp)[2] == 0.0


def test_warp_controls_reach_the_shader(world, warp):
    warp.par.Angle = 0.5
    warp.par.Direction = 1.0
    warp.par.Standing = 0.4
    warp.par.Shape = 0.7
    warp.par.Spread = 0.3
    warp.par.Mix = 0.6
    hit(world, warp)
    assert wave(warp)[0] == pytest.approx(math.pi / 2)
    assert wave(warp)[3] == 1.0
    assert warp_motion(warp)[1] == pytest.approx(0.4)
    assert warp_motion(warp)[3] == pytest.approx(0.7)
    assert warp_look(warp) == pytest.approx((0.3, 0.6, 0.0, 0.0))


def test_warp_gate_holds_full_while_pressed(world, warp):
    warp.par.Mode = 'gate'
    warp.par.Trigger = 1.0
    world.tick(warp, FPS)
    assert warp_motion(warp)[0] == pytest.approx(0.35 * 0.12)
    warp.par.Trigger = 0.0
    world.tick(warp, 5)
    assert 0.0 < warp_motion(warp)[0] < 0.35 * 0.12


def test_warp_returns_exactly_to_rest(world, warp):
    hit(world, warp)
    world.tick(warp, FPS)
    assert_warp_at_rest(warp)


def test_warp_retrigger_ignore(world, warp):
    warp.par.Retrigger = 'ignore'
    hit(world, warp)
    world.tick(warp, 10)
    before = warp_motion(warp)[0]
    hit(world, warp)
    assert warp_motion(warp)[0] < before


def test_warp_panic_and_held_pad_does_not_refire(world, warp):
    warp.par.Trigger = 1.0
    world.tick(warp)
    world.panic = 1.0
    world.tick(warp)
    assert_warp_at_rest(warp)
    world.panic = 0.0
    world.tick(warp, 3)
    assert_warp_at_rest(warp)


def test_warp_reset_and_frame_gap(world, warp):
    hit(world, warp)
    warp.op('reset_watch').module.onPulse()
    assert_warp_at_rest(warp)
    world.tick(warp, 2)
    hit(world, warp)
    world.abs_time.frame += 31
    world.tick(warp)
    assert_warp_at_rest(warp)
    hit(world, warp)
    assert warp.op('switch').par.index.val == 1, 'plays again after a reset'


def test_warp_max_hold_in_gate_mode(world, warp):
    warp.par.Mode = 'gate'
    warp.par.Maxhold = 0.1
    warp.par.Trigger = 1.0
    world.tick(warp, FPS * 2)
    assert_warp_at_rest(warp)


# ------------------------------------------------------------ Block Shuffle playing

SHUFFLE_SLOTS = 24
SHUFFLE_MOTION, SHUFFLE_CUT, SHUFFLE_LOOK = 0, 1, 2
SHUFFLE_UNTOUCHED = (1.0, 1.0, 0.0, 0.0)
SHUFFLE_LANDED_FRAMES = 10


@pytest.fixture
def shuffle(world):
    return world.block('block_shuffle')


def press(world, block, velocity=1.0, frames=1):
    block.par.Trigger = velocity
    world.tick(block, frames)


def lift(world, block, frames=1):
    block.par.Trigger = 0.0
    world.tick(block, frames)


def shuffle_blocks(block):
    """Live slots as (rect, map, showing)."""
    shaped = uniforms(block, 'shuffle', SHUFFLE_CUT)[0]
    found = []
    for slot in range(SHUFFLE_SLOTS):
        rect = uniforms(block, 'layout', slot)
        if rect != (0.0, 0.0, 0.0, 0.0):
            cut = uniforms(block, 'layout', SHUFFLE_SLOTS + slot)[0]
            found.append((rect, uniforms(block, 'table', slot), 1.0 if shaped < cut else 0.0))
    return found


def center(rect):
    return ((rect[0] + rect[2]) * 0.5, (rect[1] + rect[3]) * 0.5)


def mapped(point, entry):
    return (point[0] * entry[0] + entry[2], point[1] * entry[1] + entry[3])


def shown_from(blocks, index):
    """Which block's piece of the picture block index is showing, by where its center reads."""
    rect, entry, _ = blocks[index]
    x, y = mapped(center(rect), entry)
    for other, (other_rect, _, _) in enumerate(blocks):
        if center(other_rect) == pytest.approx((x, y)):
            return other
    raise AssertionError('block %d reads from no block center' % index)


def assert_shuffle_at_rest(block):
    assert block.op('switch').par.index.val == 0
    for entry in range(SHUFFLE_SLOTS * 2):
        assert uniforms(block, 'layout', entry) == (0.0, 0.0, 0.0, 0.0)
    for entry in range(SHUFFLE_SLOTS):
        assert uniforms(block, 'table', entry) == SHUFFLE_UNTOUCHED
    for entry in range(SHUFFLE_LOOK + 1):
        assert uniforms(block, 'shuffle', entry) == (0.0, 0.0, 0.0, 0.0)


def test_shuffle_wiring(shuffle):
    assert shuffle.op('shuffle').inputs == {0: shuffle.op('in1'), 1: shuffle.op('layout'), 2: shuffle.op('table')}
    assert shuffle.op('layout').inputs == {}, 'anything feeding the layout pass makes it cook every frame'
    assert shuffle.op('table').inputs == {}, 'anything feeding the table pass makes it cook every frame'
    assert shuffle.op('switch').inputs == {0: shuffle.op('in1'), 1: shuffle.op('shuffle')}
    assert shuffle.op('out1').inputs[0] is shuffle.op('switch')


def test_shuffle_is_a_hold_by_default(shuffle):
    assert shuffle.par.Mode.val == 'gate'


def test_shuffle_table_is_one_pixel_per_slot(shuffle):
    table = shuffle.op('table')
    assert (table.par.resolutionw.val, table.par.resolutionh.val) == (SHUFFLE_SLOTS, 1)


@pytest.mark.parametrize('name, count', [('shuffle', SHUFFLE_LOOK + 1), ('layout', SHUFFLE_SLOTS * 2),
                                         ('table', SHUFFLE_SLOTS)])
def test_shuffle_shader_uniforms_match_their_entries(shuffle, name, count):
    node = shuffle.op(name)
    text = shuffle.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == count
    assert '@@' not in text
    for index in range(count):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec4 %s;' % uniform, text), uniform


def test_shuffle_lands_on_the_very_first_frame(world, shuffle):
    press(world, shuffle)
    assert shuffle.op('switch').par.index.val == 1
    blocks = shuffle_blocks(shuffle)
    assert len(blocks) == 20
    assert sum(rect_area(r) for r, _, _ in blocks) == pytest.approx(1.0)
    assert all(showing == 1.0 for _, _, showing in blocks)
    assert sum(entry != SHUFFLE_UNTOUCHED for _, entry, _ in blocks) >= 2


def test_shuffle_velocity_sets_the_block_count(world, shuffle):
    press(world, shuffle, 1.0)
    assert len(shuffle_blocks(shuffle)) == 20
    lift(world, shuffle, 2 * FPS)
    press(world, shuffle, 0.01)
    assert len(shuffle_blocks(shuffle)) == 3


def test_shuffle_most_blocks_knob_reaches_the_slot_limit(world, shuffle):
    shuffle.par.Blocks = 1.0
    press(world, shuffle)
    assert len(shuffle_blocks(shuffle)) == SHUFFLE_SLOTS


def test_shuffle_full_scatter_swaps_every_piece_and_none_stays_home(world, shuffle):
    shuffle.par.Scatter = 1.0
    shuffle.par.Flip = 0.0
    shuffle.par.Blocks = 1.0
    for _ in range(40):
        press(world, shuffle)
        blocks = shuffle_blocks(shuffle)
        sources = [shown_from(blocks, i) for i in range(len(blocks))]
        assert all(source != index for index, source in enumerate(sources))
        assert sorted(sources) == list(range(len(blocks))), 'every piece shows up exactly once'
        lift(world, shuffle, 2 * FPS)


def test_shuffle_scatter_off_leaves_the_picture_in_place(world, shuffle):
    shuffle.par.Scatter = 0.0
    shuffle.par.Flip = 0.0
    press(world, shuffle, frames=SHUFFLE_LANDED_FRAMES)
    assert all(entry == SHUFFLE_UNTOUCHED for _, entry, _ in shuffle_blocks(shuffle))


def test_shuffle_soft_press_swaps_fewer_pieces(world, shuffle):
    shuffle.par.Scatter = 1.0
    shuffle.par.Flip = 0.0
    shuffle.par.Blocks = 1.0
    shuffle.par.Velocitydepth = 0.0  # same block count, so only the swap share differs
    press(world, shuffle, 1.0)
    hard = sum(entry != SHUFFLE_UNTOUCHED for _, entry, _ in shuffle_blocks(shuffle))
    lift(world, shuffle, 2 * FPS)
    shuffle.par.Velocitydepth = 1.0
    shuffle.par.Blocks = 1.0
    press(world, shuffle, 0.01)
    soft = sum(entry != SHUFFLE_UNTOUCHED for _, entry, _ in shuffle_blocks(shuffle))
    assert hard == SHUFFLE_SLOTS
    assert soft == 2, 'a feather-light press on three blocks still trades two'


def test_shuffle_pieces_are_cropped_never_stretched(world, shuffle):
    shuffle.par.Scatter = 1.0
    shuffle.par.Flip = 1.0
    shuffle.par.Blocks = 1.0
    for _ in range(30):
        press(world, shuffle)
        for _, entry, _ in shuffle_blocks(shuffle):
            assert abs(entry[0]) == pytest.approx(abs(entry[1]))
            assert 0.5 <= abs(entry[0]) <= 2.0
        lift(world, shuffle, 2 * FPS)


def test_shuffle_unclamped_crop_stays_inside_its_source(world, shuffle):
    shuffle.par.Scatter = 1.0
    shuffle.par.Flip = 0.0
    shuffle.par.Blocks = 1.0
    checked = 0
    for _ in range(30):
        press(world, shuffle)
        blocks = shuffle_blocks(shuffle)
        for index, (rect, entry, _) in enumerate(blocks):
            if not 0.5 < entry[0] < 2.0:
                continue
            source = blocks[shown_from(blocks, index)][0]
            for corner in ((rect[0], rect[1]), (rect[2], rect[3])):
                x, y = mapped(corner, entry)
                assert source[0] - 1e-9 <= x <= source[2] + 1e-9
                assert source[1] - 1e-9 <= y <= source[3] + 1e-9
            checked += 1
        lift(world, shuffle, 2 * FPS)
    assert checked > 100


def test_shuffle_flip_mirrors_pieces_about_their_own_center(world, shuffle):
    shuffle.par.Scatter = 0.0
    shuffle.par.Flip = 1.0
    press(world, shuffle)
    for rect, entry, _ in shuffle_blocks(shuffle):
        assert entry[0] < 0.0 or entry[1] < 0.0
        assert mapped(center(rect), entry) == pytest.approx(center(rect))


def test_shuffle_flip_off_never_mirrors(world, shuffle):
    shuffle.par.Scatter = 1.0
    shuffle.par.Flip = 0.0
    press(world, shuffle)
    assert all(entry[0] > 0.0 and entry[1] > 0.0 for _, entry, _ in shuffle_blocks(shuffle))


def test_shuffle_scatter_and_flip_play_live_without_redrawing_the_layout(world, shuffle):
    layout = shuffle.op('layout')
    writes = []
    original = type(layout.par).__setattr__

    def counting(pars, name, value):
        if pars is layout.par:
            writes.append(name)
        original(pars, name, value)

    shuffle.par.Scatter = 0.0
    shuffle.par.Flip = 0.0
    type(layout.par).__setattr__ = counting
    try:
        press(world, shuffle, frames=SHUFFLE_LANDED_FRAMES)
        drawn = len(writes)
        rects = [r for r, _, _ in shuffle_blocks(shuffle)]
        assert all(e == SHUFFLE_UNTOUCHED for _, e, _ in shuffle_blocks(shuffle))
        shuffle.par.Scatter = 1.0
        world.tick(shuffle)
        assert all(e != SHUFFLE_UNTOUCHED for _, e, _ in shuffle_blocks(shuffle))
        shuffle.par.Flip = 1.0
        world.tick(shuffle)
        assert all(e[0] < 0.0 or e[1] < 0.0 for _, e, _ in shuffle_blocks(shuffle))
        shuffle.par.Scatter = 0.0
        shuffle.par.Flip = 0.0
        world.tick(shuffle)
        assert all(e == SHUFFLE_UNTOUCHED for _, e, _ in shuffle_blocks(shuffle))
        assert [r for r, _, _ in shuffle_blocks(shuffle)] == rects
        assert len(writes) == drawn, 'knob moves must not redraw the layout'
    finally:
        type(layout.par).__setattr__ = original


def test_shuffle_turning_scatter_up_only_moves_a_few_pieces_at_a_time(world, shuffle):
    shuffle.par.Blocks = 1.0
    shuffle.par.Flip = 0.0
    shuffle.par.Scatter = 0.5
    press(world, shuffle)
    before = [e for _, e, _ in shuffle_blocks(shuffle)]
    shuffle.par.Scatter = 0.5 + 1.0 / SHUFFLE_SLOTS
    world.tick(shuffle)
    after = [e for _, e, _ in shuffle_blocks(shuffle)]
    assert sum(a != b for a, b in zip(before, after)) == 2


def test_shuffle_every_press_gets_a_new_layout(world, shuffle):
    seen = set()
    for _ in range(10):
        press(world, shuffle)
        seen.add(tuple(r for r, _, _ in shuffle_blocks(shuffle)))
        lift(world, shuffle, 2 * FPS)
    assert len(seen) == 10


def test_shuffle_seams_spring_then_land_exactly(world, shuffle):
    shuffle.par.Spring = 1.0
    press(world, shuffle)
    start = uniforms(shuffle, 'shuffle', SHUFFLE_MOTION)
    assert abs(start[0]) == pytest.approx(0.9)
    signs = set()
    for _ in range(SHUFFLE_LANDED_FRAMES):
        world.tick(shuffle)
        now = uniforms(shuffle, 'shuffle', SHUFFLE_MOTION)
        signs.add(now[0] > 0)
        assert abs(now[0]) < 1.0 and abs(now[1]) < 1.0, 'seams would cross'
    assert signs == {True, False}, 'never swung past home'
    assert uniforms(shuffle, 'shuffle', SHUFFLE_MOTION)[:2] == (0.0, 0.0)


def test_shuffle_holds_while_pressed(world, shuffle):
    press(world, shuffle, frames=3 * FPS)
    blocks = shuffle_blocks(shuffle)
    assert shuffle.op('switch').par.index.val == 1
    assert all(showing == 1.0 for _, _, showing in blocks)
    assert uniforms(shuffle, 'shuffle', SHUFFLE_CUT)[0] == 0.0


def test_shuffle_release_cuts_smallest_first_and_biggest_stands_alone(world, shuffle):
    press(world, shuffle, frames=SHUFFLE_LANDED_FRAMES)
    shuffle.par.Trigger = 0.0
    alone_frames = 0
    last_seen = {}
    frame = 0
    while True:
        world.tick(shuffle)
        if shuffle.op('switch').par.index.val == 0:
            break
        blocks = shuffle_blocks(shuffle)
        showing = [b for b in blocks if b[2] == 1.0]
        assert showing, 'the wall emptied before the block returned to idle'
        alone_frames += len(showing) == 1
        for rect, _, shown in blocks:
            if shown == 1.0:
                last_seen[rect] = frame
        frame += 1
    ordered = sorted(last_seen, key=rect_area)
    assert [last_seen[r] for r in ordered] == sorted(last_seen[r] for r in ordered)
    assert alone_frames >= 6
    assert_shuffle_at_rest(shuffle)


def test_shuffle_returns_exactly_to_rest(world, shuffle):
    shuffle.par.Scatter = 1.0
    shuffle.par.Flip = 1.0
    press(world, shuffle, frames=20)
    lift(world, shuffle, 2 * FPS)
    assert_shuffle_at_rest(shuffle)


def test_shuffle_press_during_release_brings_a_fresh_full_wall(world, shuffle):
    press(world, shuffle, frames=5)
    lift(world, shuffle, 20)
    old = [r for r, _, _ in shuffle_blocks(shuffle)]
    assert any(showing == 0.0 for _, _, showing in shuffle_blocks(shuffle))
    press(world, shuffle)
    blocks = shuffle_blocks(shuffle)
    assert [r for r, _, _ in blocks] != old
    assert all(showing == 1.0 for _, _, showing in blocks)


def test_shuffle_hit_mode_runs_hold_then_release(world, shuffle):
    shuffle.par.Mode = 'hit'
    shuffle.par.Hold = 0.2
    press(world, shuffle)
    lift(world, shuffle, int(0.2 * FPS) - 1)
    assert all(showing == 1.0 for _, _, showing in shuffle_blocks(shuffle))
    lift(world, shuffle, 2 * FPS)
    assert_shuffle_at_rest(shuffle)


def test_shuffle_look_controls_reach_the_shader(world, shuffle):
    shuffle.par.Ink = 0.5
    shuffle.par.Mix = 0.25
    press(world, shuffle)
    assert uniforms(shuffle, 'shuffle', SHUFFLE_LOOK) == pytest.approx((0.004, 0.25, 0.0, 0.0))


def test_shuffle_max_hold_releases(world, shuffle):
    shuffle.par.Maxhold = 0.5
    press(world, shuffle, frames=20)
    assert shuffle.op('switch').par.index.val == 1
    press(world, shuffle, frames=2 * FPS)
    assert_shuffle_at_rest(shuffle)


def test_shuffle_panic_and_held_pad_does_not_refire(world, shuffle):
    press(world, shuffle, frames=5)
    world.panic = 1.0
    world.tick(shuffle)
    assert_shuffle_at_rest(shuffle)
    world.panic = 0.0
    world.tick(shuffle, 5)
    assert_shuffle_at_rest(shuffle)


def test_shuffle_reset_clears_state(world, shuffle):
    press(world, shuffle)
    shuffle.op('engine').module.reset()
    assert_shuffle_at_rest(shuffle)
    lift(world, shuffle)  # the pad comes up, so the next press is a new one
    press(world, shuffle)
    assert len(shuffle_blocks(shuffle)) == 20


def test_shuffle_frame_gap_resets(world, shuffle):
    press(world, shuffle)
    world.abs_time.frame += 1000
    shuffle.par.Trigger = 0.0
    world.tick(shuffle)
    assert_shuffle_at_rest(shuffle)


# ------------------------------------------------------------ Transition playing

TRANSITION_ATTACK_FRAMES = 21  # default Attack 0.35 s
TRANSITION_RELEASE_FRAMES = 36  # default Release 0.6 s


@pytest.fixture
def trans(world):
    return world.block('transition')


def trans_move(block):
    return uniforms(block, 'transition', 0)


def hidden(block):
    return trans_move(block)[0]


def trans_ease(block):
    return block.op('effect').module.ease


def assert_trans_at_rest(block):
    assert block.op('switch').par.index.val == 0
    for index in range(4):
        assert uniforms(block, 'transition', index) == (0.0, 0.0, 0.0, 0.0)


def trans_hidden_run(world, block, frames):
    values = []
    for _ in range(frames):
        world.tick(block)
        values.append(hidden(block))
    return values


def test_trans_wiring(trans):
    assert trans.op('transition').inputs[0] is trans.op('in1')
    assert trans.op('switch').inputs == {0: trans.op('in1'), 1: trans.op('transition')}
    assert trans.op('out1').inputs[0] is trans.op('switch')


def test_trans_shader_uniforms_match_their_entries(trans):
    node = trans.op('transition')
    text = trans.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 4
    for index in range(4):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_trans_every_style_has_a_branch(trans):
    styles = trans.op('effect').module.STYLES
    assert trans.par.Style.menuNames == styles
    text = trans.op('transition_shader').text
    assert 'style == %d' % (len(styles) - 2) in text, 'second-to-last style is drawn'


def test_trans_is_a_hold_by_default(trans):
    assert trans.par.Mode.val == 'gate'
    assert trans.par.Velocitydepth.val == 0.0


def test_trans_cut_lands_on_the_very_first_frame(world, trans):
    trans.par.Attack = 0.0
    press(world, trans)
    assert trans.op('switch').par.index.val == 1
    assert hidden(trans) == 1.0


def test_trans_hides_over_attack_and_stays_gone(world, trans):
    trans.par.Trigger = 1.0
    values = trans_hidden_run(world, trans, TRANSITION_ATTACK_FRAMES + 2)
    assert values[0] == 0.0, 'the first frame starts from the untouched picture'
    assert values == sorted(values)
    assert values[3] < 0.05, 'ease in leaves slowly'
    assert values[-1] == 1.0
    press(world, trans, frames=FPS * 3)
    assert hidden(trans) == 1.0
    assert trans.op('switch').par.index.val == 1


def test_trans_reveal_overshoots_and_returns_exactly_to_rest(world, trans):
    trans.par.Attack = 0.0
    press(world, trans, frames=5)
    trans.par.Trigger = 0.0
    values = trans_hidden_run(world, trans, TRANSITION_RELEASE_FRAMES - 1)
    assert 0.8 < values[0] < 1.0, 'the reveal starts from fully gone, on the move already'
    assert min(values) < 0.0, 'back easing carries the picture past home'
    world.tick(trans, 3)
    assert_trans_at_rest(trans)


def test_trans_easings_start_and_end_on_the_mark(trans):
    ease = trans_ease(trans)
    for name in trans.par.Ineasing.menuNames:
        for overshoot in (0.0, 0.5, 1.0):
            assert ease(name, 0.0, overshoot) == pytest.approx(0.0, abs=1e-9), name
            assert ease(name, 1.0, overshoot) == 1.0, name
            assert ease(name, 0.999, overshoot) == pytest.approx(1.0, abs=0.02), name
    samples = [i / 200.0 for i in range(201)]
    assert max(ease('back', t, 0.5) for t in samples) > 1.05
    assert max(ease('elastic', t, 1.0) for t in samples) > 1.05
    assert max(ease('back', t, 0.0) for t in samples) <= 1.0, 'no overshoot at Overshoot 0'
    bounce = [ease('bounce', t, 1.0) for t in samples]
    first_landing = next(i for i, v in enumerate(bounce) if v >= 0.999)
    assert min(bounce[first_landing:-1]) < 0.9, 'it comes back up off the landing'
    assert max(bounce) <= 1.0 + 1e-9


def test_trans_letting_go_mid_hide_turns_back_without_a_jump(world, trans):
    trans.par.Trigger = 1.0
    values = trans_hidden_run(world, trans, 12)
    trans.par.Trigger = 0.0
    values += trans_hidden_run(world, trans, 10)
    steps = [abs(b - a) for a, b in zip(values, values[1:])]
    assert max(steps) < 0.12
    assert values[11] > values[-1], 'it heads back toward the picture'


def test_trans_pressing_mid_reveal_hides_again_from_where_it_is(world, trans):
    trans.par.Attack = 0.0
    press(world, trans, frames=3)
    trans.par.Trigger = 0.0
    values = trans_hidden_run(world, trans, 8)
    trans.par.Attack = 0.35
    trans.par.Trigger = 1.0
    values += trans_hidden_run(world, trans, 6)
    steps = [abs(b - a) for a, b in zip(values, values[1:])]
    assert max(steps) < 0.25
    assert values[-1] > values[8], 'the new press is hiding it again'


def test_trans_velocity_scales_how_far_it_hides(world, trans):
    trans.par.Attack = 0.0
    trans.par.Velocitydepth = 1.0
    press(world, trans, velocity=0.5, frames=3)
    assert hidden(trans) == pytest.approx(0.5)
    lift(world, trans, FPS)
    press(world, trans, velocity=1.0, frames=3)
    assert hidden(trans) == pytest.approx(1.0)


def test_trans_leaves_back_the_way_it_came_by_default(world, trans):
    press(world, trans, frames=5)
    assert trans_move(trans)[2:] == (-1.0, 0.0)


def test_trans_through_carries_on_and_comes_back_from_the_start(world, trans):
    trans.par.Exit = 'through'
    trans.par.Attack = 0.0
    press(world, trans, frames=3)
    assert trans_move(trans)[2:] == (1.0, 0.0), 'leaves off the far side'
    assert uniforms(trans, 'transition', 3)[0] == -1.0
    lift(world, trans)
    assert trans_move(trans)[2:] == (-1.0, 0.0), 'comes back in from the left'
    assert uniforms(trans, 'transition', 3)[0] == 1.0


def test_trans_through_let_go_early_turns_back_on_the_same_side(world, trans):
    trans.par.Exit = 'through'
    press(world, trans, frames=8)
    assert 0.0 < hidden(trans) < 1.0
    lift(world, trans)
    assert trans_move(trans)[2:] == (1.0, 0.0), 'switching sides now would jump'


def test_trans_direction_sets_the_side(world, trans):
    for name, side in [('up', (0.0, 1.0)), ('downright', (1.0, -1.0)), ('upleft', (-1.0, 1.0))]:
        trans.par.Direction = name
        press(world, trans, frames=2)
        assert trans_move(trans)[2:] == side, name
        lift(world, trans, FPS * 2)


def test_trans_controls_reach_the_shader(world, trans):
    trans.par.Style = 'spin'
    trans.par.Direction = 'right'
    trans.par.Softness = 0.5
    trans.par.Count = 1.0
    trans.par.Spin = 1.5
    trans.par.Centerx = 0.25
    trans.par.Centery = 0.75
    trans.par.Mix = 0.6
    press(world, trans)
    assert trans_move(trans)[1] == trans.op('effect').module.STYLES.index('spin')
    assert uniforms(trans, 'transition', 1) == pytest.approx((0.0015 + 0.35 * 0.25, 48, 0.0, 1.5))
    assert uniforms(trans, 'transition', 2)[0:2] == pytest.approx((0.25, 0.75))
    assert uniforms(trans, 'transition', 2)[3] == pytest.approx(0.6)
    trans.par.Direction = 'left'
    world.tick(trans)
    assert uniforms(trans, 'transition', 1)[3] == pytest.approx(-1.5), 'the other side spins the other way'


def test_trans_zoom_means_shrink_or_grow_by_style(world, trans):
    trans.par.Zoom = 1.0
    trans.par.Style = 'zoomaway'
    press(world, trans)
    assert uniforms(trans, 'transition', 1)[2] == pytest.approx(0.95)
    trans.par.Style = 'zoomthrough'
    world.tick(trans)
    assert uniforms(trans, 'transition', 1)[2] == pytest.approx(4.0)


def test_trans_each_hide_gets_a_new_seed_held_for_the_press(world, trans):
    trans.par.Attack = 0.0
    press(world, trans, frames=2)
    first = uniforms(trans, 'transition', 2)[2]
    world.tick(trans, 10)
    assert uniforms(trans, 'transition', 2)[2] == first
    lift(world, trans, FPS * 2)
    press(world, trans, frames=2)
    assert uniforms(trans, 'transition', 2)[2] != first


def test_trans_hit_mode_hides_waits_and_comes_back(world, trans):
    trans.par.Mode = 'hit'
    trans.par.Attack = 0.0
    trans.par.Hold = 0.25
    hit(world, trans)
    assert hidden(trans) == 1.0
    world.tick(trans, 14)
    assert hidden(trans) == 1.0
    world.tick(trans, 4)
    assert hidden(trans) < 1.0
    world.tick(trans, FPS)
    assert_trans_at_rest(trans)


def test_trans_panic_and_held_pad_does_not_refire(world, trans):
    press(world, trans, frames=5)
    world.panic = 1.0
    world.tick(trans)
    assert_trans_at_rest(trans)
    world.panic = 0.0
    world.tick(trans, 5)
    assert_trans_at_rest(trans)


def test_trans_reset_and_frame_gap(world, trans):
    trans.par.Attack = 0.0
    press(world, trans, frames=3)
    trans.op('reset_watch').module.onPulse()
    assert_trans_at_rest(trans)
    lift(world, trans)
    press(world, trans)
    world.abs_time.frame += 31
    world.tick(trans)
    assert_trans_at_rest(trans)
    lift(world, trans)
    press(world, trans)
    assert hidden(trans) == 1.0, 'plays again after a reset'


def test_trans_max_hold_brings_the_picture_back(world, trans):
    trans.par.Maxhold = 0.5
    press(world, trans, frames=FPS * 3)
    assert_trans_at_rest(trans)


# ------------------------------------------------------------ Shape Wipe playing

WIPE_ATTACK_FRAMES = 18   # default Attack 0.3 s
WIPE_HOLD_FRAMES = 15     # default Hold 0.25 s
WIPE_RELEASE_FRAMES = 30  # default Release 0.5 s
WIPE_A, WIPE_B, WIPE_LOOK = 0, 3, 6


@pytest.fixture
def swipe(world):
    return world.block('shape_wipe')


def wipe_shape(block, layer=WIPE_A):
    return uniforms(block, 'wipe', layer)


def wipe_place(block, layer=WIPE_A):
    return uniforms(block, 'wipe', layer + 1)


def wipe_color(block, layer=WIPE_A):
    return uniforms(block, 'wipe', layer + 2)


def wipe_module(block):
    return block.op('effect').module


def assert_wipe_at_rest(block):
    assert block.op('switch').par.index.val == 0
    for index in range(7):
        assert uniforms(block, 'wipe', index) == (0.0, 0.0, 0.0, 0.0)


def wipe_hit(world, block, velocity=1.0, frames=1):
    block.par.Trigger = velocity
    world.tick(block)
    block.par.Trigger = 0.0
    world.tick(block, frames - 1)


def test_wipe_wiring(swipe):
    assert swipe.op('wipe').inputs[0] is swipe.op('in1')
    assert swipe.op('switch').inputs == {0: swipe.op('in1'), 1: swipe.op('wipe')}
    assert swipe.op('out1').inputs[0] is swipe.op('switch')


def test_wipe_shader_uniforms_match_their_entries(swipe):
    node = swipe.op('wipe')
    text = swipe.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 7
    for index in range(7):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec\d %s;' % uniform, text), uniform


def test_wipe_menus_match_the_effect(swipe):
    module = wipe_module(swipe)
    assert swipe.par.Shape.menuNames == module.SHAPES + ['shuffle']
    assert swipe.par.Exit.menuNames == module.EXITS
    assert swipe.par.Color.menuNames == ['cycle'] + list(module.COLORS)
    text = swipe.op('wipe_shader').text
    assert 'const int BAR = %d;' % module.SHAPES.index('bar') in text
    for index, name in enumerate(['fade', 'punch', 'halftone', 'retreat', 'wipeoff']):
        assert 'const int EXIT_%s = %d;' % (name.upper(), index) in text
        assert module.EXITS.index(name) == index


def test_wipe_is_a_hit_that_punches_through_by_default(swipe):
    assert swipe.par.Mode.val == 'hit'
    assert swipe.par.Exit.val == 'punch'


def test_wipe_cut_in_lands_on_the_very_first_frame(world, swipe):
    swipe.par.Attack = 0.0
    wipe_hit(world, swipe)
    assert swipe.op('switch').par.index.val == 1
    assert wipe_shape(swipe)[1] == 1.0
    assert wipe_shape(swipe)[3] == 1.0


def test_wipe_grows_holds_clears_and_returns_exactly_to_rest(world, swipe):
    wipe_hit(world, swipe)
    assert swipe.op('switch').par.index.val == 1
    grown = []
    for _ in range(WIPE_ATTACK_FRAMES + 1):
        world.tick(swipe)
        grown.append(wipe_shape(swipe)[1])
    assert grown == sorted(grown) and grown[-1] == 1.0
    world.tick(swipe, WIPE_HOLD_FRAMES - 2)
    assert wipe_shape(swipe)[1] == 1.0
    assert wipe_place(swipe)[3] == 0.0, 'still holding the color'
    leaving = []
    for _ in range(WIPE_RELEASE_FRAMES - 2):
        world.tick(swipe)
        leaving.append(wipe_place(swipe)[3])
    assert leaving == sorted(leaving) and 0.0 < leaving[-1] < 1.0
    assert wipe_place(swipe)[2] == wipe_module(swipe).EXITS.index('punch')
    world.tick(swipe, 10)
    assert_wipe_at_rest(swipe)


def test_wipe_velocity_scales_how_far_it_grows(world, swipe):
    swipe.par.Attack = 0.0
    wipe_hit(world, swipe, velocity=0.5)
    assert wipe_shape(swipe)[1] == pytest.approx(0.35 + 0.65 * 0.5)


def test_wipe_back_easing_overshoots_then_settles(world, swipe):
    swipe.par.Easing = 'back'
    wipe_hit(world, swipe)
    grown = []
    for _ in range(WIPE_ATTACK_FRAMES + 2):
        world.tick(swipe)
        grown.append(wipe_shape(swipe)[1])
    assert max(grown) > 1.0
    assert grown[-1] == 1.0


def test_wipe_cycle_never_repeats_a_color(world, swipe):
    swipe.par.Attack = 0.0
    seen = []
    for _ in range(30):
        wipe_hit(world, swipe)
        seen.append(wipe_color(swipe)[:3])
        world.tick(swipe, FPS * 2)
    palette = [wipe_module(swipe).COLORS[name] for name in wipe_module(swipe).CYCLE]
    assert all(color in palette for color in seen)
    assert all(a != b for a, b in zip(seen, seen[1:]))
    assert len(set(seen)) == len(palette)


def test_wipe_picked_color_is_used_every_time(world, swipe):
    swipe.par.Color = 'yellow'
    for _ in range(3):
        wipe_hit(world, swipe)
        assert wipe_color(swipe)[:3] == wipe_module(swipe).YELLOW
        world.tick(swipe, FPS * 2)


def test_wipe_shuffle_picks_a_new_shape_each_press(world, swipe):
    swipe.par.Shape = 'shuffle'
    shapes = []
    for _ in range(12):
        wipe_hit(world, swipe)
        shapes.append(wipe_shape(swipe)[0])
        world.tick(swipe, FPS * 2)
    assert all(a != b for a, b in zip(shapes, shapes[1:]))


def test_wipe_star_points_reach_the_shader(world, swipe):
    wipe_hit(world, swipe)
    assert wipe_color(swipe)[3] == 5, 'a five-point star by default'
    world.tick(swipe, FPS * 2)
    swipe.par.Points = 1.0
    wipe_hit(world, swipe)
    assert wipe_color(swipe)[3] == 12


def test_wipe_origin_choices(world, swipe):
    wipe_hit(world, swipe)
    assert wipe_place(swipe)[:2] == (0.5, 0.5)
    world.tick(swipe, FPS * 2)
    swipe.par.Origin = 'pad'
    swipe.par.Centerx = 0.1
    swipe.par.Centery = 0.9
    wipe_hit(world, swipe)
    assert wipe_place(swipe)[:2] == pytest.approx((0.1, 0.9))
    world.tick(swipe, FPS * 2)
    swipe.par.Origin = 'random'
    origins = set()
    for _ in range(5):
        wipe_hit(world, swipe)
        x, y = wipe_place(swipe)[:2]
        assert 0.2 <= x <= 0.8 and 0.2 <= y <= 0.8
        origins.add((x, y))
        world.tick(swipe, FPS * 2)
    assert len(origins) == 5


def test_wipe_spins_in_and_lands_on_the_angle(world, swipe):
    swipe.par.Angle = 0.25
    swipe.par.Spin = 1.0
    wipe_hit(world, swipe)
    assert wipe_shape(swipe)[2] == pytest.approx(math.tau * 1.25)
    world.tick(swipe, WIPE_ATTACK_FRAMES + 2)
    assert wipe_shape(swipe)[2] == pytest.approx(math.tau * 0.25)


def test_wipe_look_controls_reach_the_shader(world, swipe):
    swipe.par.Softness = 0.5
    swipe.par.Ink = 1.0
    swipe.par.Shadow = 0.5
    swipe.par.Mix = 0.4
    wipe_hit(world, swipe)
    assert uniforms(swipe, 'wipe', WIPE_LOOK) == pytest.approx((0.125, 0.03, 0.03, 0.4))


@pytest.mark.parametrize('exit_style', ['fade', 'halftone', 'retreat', 'wipeoff'])
def test_wipe_every_exit_plays_out(world, swipe, exit_style):
    swipe.par.Exit = exit_style
    swipe.par.Attack = 0.0
    wipe_hit(world, swipe, frames=WIPE_HOLD_FRAMES + 10)
    assert wipe_place(swipe)[2] == wipe_module(swipe).EXITS.index(exit_style)
    assert 0.0 < wipe_place(swipe)[3] < 1.0
    world.tick(swipe, WIPE_RELEASE_FRAMES)
    assert_wipe_at_rest(swipe)


def test_wipe_hard_cut_clears_at_once_and_stays_clear(world, swipe):
    swipe.par.Exit = 'cut'
    swipe.par.Attack = 0.0
    wipe_hit(world, swipe, frames=WIPE_HOLD_FRAMES + 2)
    assert swipe.op('switch').par.index.val == 0
    assert wipe_shape(swipe) == (0.0, 0.0, 0.0, 0.0)
    world.tick(swipe, 10)
    assert swipe.op('switch').par.index.val == 0, 'the release frames never grow a new shape'
    world.tick(swipe, WIPE_RELEASE_FRAMES)
    assert_wipe_at_rest(swipe)


def test_wipe_press_during_exit_grows_over_the_clearing_shape(world, swipe):
    swipe.par.Attack = 0.0
    wipe_hit(world, swipe, frames=WIPE_HOLD_FRAMES + 10)
    old_color = wipe_color(swipe)
    old_leave = wipe_place(swipe)[3]
    wipe_hit(world, swipe)
    assert wipe_color(swipe, WIPE_B) == old_color, 'the old shape moved underneath'
    assert wipe_place(swipe, WIPE_B)[3] == pytest.approx(old_leave), 'and kept its place in the exit'
    assert wipe_color(swipe)[:3] != old_color[:3], 'the new one is a different color'
    assert wipe_place(swipe)[3] == 0.0
    world.tick(swipe, 3)
    assert wipe_place(swipe, WIPE_B)[3] > old_leave
    world.tick(swipe, WIPE_RELEASE_FRAMES)
    assert wipe_shape(swipe, WIPE_B) == (0.0, 0.0, 0.0, 0.0), 'the old shape is gone once cleared'
    assert wipe_shape(swipe)[3] == 1.0


def test_wipe_press_during_hold_clears_the_old_shape_underneath(world, swipe):
    swipe.par.Attack = 0.0
    wipe_hit(world, swipe, frames=5)
    wipe_hit(world, swipe)
    assert wipe_shape(swipe, WIPE_B)[3] == 1.0
    assert wipe_place(swipe, WIPE_B)[2] == wipe_module(swipe).EXITS.index('punch')


def test_wipe_retrigger_ignore(world, swipe):
    swipe.par.Retrigger = 'ignore'
    swipe.par.Attack = 0.0
    wipe_hit(world, swipe, frames=5)
    color = wipe_color(swipe)
    wipe_hit(world, swipe)
    assert wipe_color(swipe) == color
    assert wipe_shape(swipe, WIPE_B) == (0.0, 0.0, 0.0, 0.0)


def test_wipe_gate_holds_the_color_while_pressed(world, swipe):
    swipe.par.Mode = 'gate'
    press(world, swipe, frames=FPS * 2)
    assert wipe_shape(swipe)[1] == 1.0
    assert wipe_place(swipe)[3] == 0.0
    lift(world, swipe, 5)
    assert 0.0 < wipe_place(swipe)[3] < 1.0
    lift(world, swipe, WIPE_RELEASE_FRAMES)
    assert_wipe_at_rest(swipe)


def test_wipe_panic_and_held_pad_does_not_refire(world, swipe):
    press(world, swipe, frames=5)
    world.panic = 1.0
    world.tick(swipe)
    assert_wipe_at_rest(swipe)
    world.panic = 0.0
    world.tick(swipe, 5)
    assert_wipe_at_rest(swipe)


def test_wipe_reset_and_frame_gap(world, swipe):
    swipe.par.Attack = 0.0
    wipe_hit(world, swipe)
    swipe.op('reset_watch').module.onPulse()
    assert_wipe_at_rest(swipe)
    world.tick(swipe)
    assert_wipe_at_rest(swipe)
    wipe_hit(world, swipe)
    world.abs_time.frame += 31
    world.tick(swipe)
    assert_wipe_at_rest(swipe)
    wipe_hit(world, swipe)
    assert wipe_shape(swipe)[1] == 1.0, 'plays again after a reset'


def test_wipe_max_hold_in_gate_mode(world, swipe):
    swipe.par.Mode = 'gate'
    swipe.par.Maxhold = 0.5
    press(world, swipe, frames=FPS * 3)
    assert_wipe_at_rest(swipe)


# ------------------------------------------------------------ ken burns

KB_ATTACK_FRAMES = 60    # default Attack 1.0 s
KB_RELEASE_FRAMES = 90   # default Release 1.5 s


@pytest.fixture
def kb(world):
    world.root.create('moviefileinTOP', 'portrait')
    block = world.block('ken_burns')
    block.par.Photo = '/portrait'
    return block


def kb_frame(block):
    return uniforms(block, 'kenburns', 0)


def kb_shift(block):
    return uniforms(block, 'kenburns', 1)


def kb_view(block):
    return uniforms(block, 'kenburns', 2)


def kb_cover(block):
    return uniforms(block, 'kenburns', 3)


def assert_kb_at_rest(block):
    assert block.op('switch').par.index.val == 0
    for index in range(4):
        assert uniforms(block, 'kenburns', index) == (0.0, 0.0, 0.0, 0.0)


def test_kb_wiring(kb):
    assert kb.op('photo').type == 'selectTOP'
    assert kb.op('photo').par.top.eval() is kb.op('/portrait')
    assert kb.op('kenburns').inputs == {0: kb.op('in1'), 1: kb.op('photo')}
    assert kb.op('switch').inputs == {0: kb.op('in1'), 1: kb.op('kenburns')}
    assert kb.op('out1').inputs[0] is kb.op('switch')


def test_kb_shader_uniforms_match_their_entries(kb):
    node = kb.op('kenburns')
    text = kb.op(node.par.pixeldat.val).text
    assert node.seq.vec.numBlocks == 4
    for index in range(4):
        uniform = getattr(node.par, 'vec%dname' % index).val
        assert re.search(r'uniform vec4 %s;' % uniform, text), uniform


def test_kb_menus_match_the_effect(kb):
    effect = kb.op('effect').module
    assert kb.par.Entrance.menuNames == effect.STYLES
    assert kb.par.Exit.menuNames == effect.STYLES
    assert set(kb.par.Direction.menuNames) == set(effect.DIRECTIONS)


def test_kb_is_a_hold_that_never_times_out(kb):
    assert kb.par.Mode.val == 'gate'
    assert kb.par.Velocitydepth.val == 0.0
    assert kb.par.Maxhold.val == 0.0


def test_kb_without_a_photo_stays_at_rest(world, kb):
    kb.par.Photo = ''
    press(world, kb, frames=30)
    assert_kb_at_rest(kb)


def test_kb_lands_on_the_first_frame_with_no_attack(world, kb):
    kb.par.Attack = 0.0
    press(world, kb)
    assert kb.op('switch').par.index.val == 1
    assert kb_view(kb)[3] == 1.0
    assert kb_frame(kb) == (0.5, 0.5, 1.0, 0.0)


def test_kb_fades_in_over_attack(world, kb):
    seen = []
    for _ in range(KB_ATTACK_FRAMES + 2):
        press(world, kb)
        seen.append(kb_view(kb)[3])
    assert seen[0] < 0.2
    assert all(b >= a for a, b in zip(seen, seen[1:]))
    assert seen[-1] == 1.0


def test_kb_moves_from_start_to_end_over_duration(world, kb):
    kb.par.Attack = 0.0
    kb.par.Duration = 2.0
    kb.par.Startx, kb.par.Starty, kb.par.Startzoom = 0.5, 0.5, 1.0
    kb.par.Endx, kb.par.Endy, kb.par.Endzoom = 0.6, 0.4, 2.0
    press(world, kb)
    assert kb_view(kb)[:3] == pytest.approx((0.5, 0.5, 1.0))
    press(world, kb, frames=FPS)
    middle = kb_view(kb)[2]
    assert 1.0 < middle < 2.0
    press(world, kb, frames=FPS + 5)
    assert kb_view(kb)[:3] == pytest.approx((0.6, 0.4, 2.0))
    press(world, kb, frames=FPS)
    assert kb_view(kb)[:3] == pytest.approx((0.6, 0.4, 2.0)), 'holds the end framing'


def test_kb_view_never_leaves_the_photo(world, kb):
    kb.par.Attack = 0.0
    kb.par.Duration = 1.0
    kb.par.Endx, kb.par.Endy, kb.par.Endzoom = 0.0, 1.0, 2.0
    press(world, kb, frames=FPS * 2)
    x, y, zoom, _ = kb_view(kb)
    assert zoom == pytest.approx(2.0)
    assert (x, y) == pytest.approx((0.25, 0.75))


def test_kb_move_keeps_running_through_the_exit(world, kb):
    kb.par.Attack = 0.0
    kb.par.Duration = 4.0
    kb.par.Endzoom = 2.0
    press(world, kb, frames=FPS)
    before = kb_view(kb)[2]
    lift(world, kb, frames=30)
    assert kb_view(kb)[2] > before
    assert 0.0 < kb_view(kb)[3] < 1.0


def test_kb_fades_out_and_returns_exactly_to_rest(world, kb):
    kb.par.Attack = 0.0
    press(world, kb, frames=10)
    seen = []
    for _ in range(KB_RELEASE_FRAMES + 2):
        lift(world, kb)
        seen.append(kb_view(kb)[3])
    assert all(b <= a for a, b in zip(seen, seen[1:]))
    assert_kb_at_rest(kb)


def test_kb_press_during_exit_picks_up_without_a_jump(world, kb):
    kb.par.Attack = 0.5
    kb.par.Duration = 4.0
    kb.par.Endzoom = 2.0
    press(world, kb, frames=FPS)
    lift(world, kb, frames=30)
    opacity, zoom = kb_view(kb)[3], kb_view(kb)[2]
    press(world, kb)
    assert kb_view(kb)[3] == pytest.approx(opacity, abs=0.05)
    assert kb_view(kb)[2] >= zoom, 'the move is not restarted'


def test_kb_velocity_scales_opacity_when_depth_is_up(world, kb):
    kb.par.Attack = 0.0
    kb.par.Velocitydepth = 1.0
    press(world, kb, velocity=0.5)
    assert kb_view(kb)[3] == pytest.approx(0.5)


def test_kb_exit_styles(world, kb):
    kb.par.Attack = 0.0
    kb.par.Amount = 1.0
    kb.par.Direction = 'right'
    kb.par.Outeasing = 'linear'

    def halfway(style):
        kb.op('reset_watch').module.onPulse()
        kb.par.Trigger = 0.0
        world.tick(kb)
        kb.par.Exit = style
        press(world, kb, frames=2)
        lift(world, kb, frames=KB_RELEASE_FRAMES // 2)

    halfway('push')
    assert kb_view(kb)[2] > 1.3
    halfway('scale')
    assert kb_frame(kb)[2] < 1.0
    halfway('slide')
    assert kb_shift(kb)[0] > 0.0 and kb_shift(kb)[1] == 0.0
    halfway('wipe')
    assert kb_cover(kb)[0] == 1.0 and 0.3 < kb_cover(kb)[1] < 0.7
    assert kb_view(kb)[3] == 1.0, 'wipe hides by its edge, not by fading'
    assert kb_cover(kb)[2:] == (1.0, 0.0)
    halfway('iris')
    assert kb_cover(kb)[0] == 2.0


def test_kb_slide_entrance_comes_from_the_far_side(world, kb):
    kb.par.Entrance = 'slide'
    kb.par.Direction = 'right'
    press(world, kb, frames=5)
    assert kb_shift(kb)[0] < 0.0
    press(world, kb, frames=KB_ATTACK_FRAMES)
    assert kb_shift(kb)[0] == 0.0


def test_kb_panic_and_held_pad_does_not_refire(world, kb):
    press(world, kb, frames=5)
    world.panic = 1.0
    world.tick(kb)
    assert_kb_at_rest(kb)
    world.panic = 0.0
    world.tick(kb, 5)
    assert_kb_at_rest(kb)


def test_kb_reset_and_frame_gap(world, kb):
    kb.par.Attack = 0.0
    press(world, kb, frames=3)
    kb.op('reset_watch').module.onPulse()
    assert_kb_at_rest(kb)
    lift(world, kb)
    press(world, kb, frames=3)
    world.abs_time.frame += 31
    world.tick(kb)
    assert_kb_at_rest(kb)
    lift(world, kb)
    press(world, kb)
    assert kb_view(kb)[3] == 1.0, 'plays again after a reset'
    assert kb_view(kb)[2] == pytest.approx(1.0), 'the move starts over'


def test_kb_hit_mode_holds_then_leaves(world, kb):
    kb.par.Mode = 'hit'
    kb.par.Attack = 0.0
    kb.par.Hold = 1.0
    press(world, kb, frames=2)
    lift(world, kb, frames=FPS // 2)
    assert kb_view(kb)[3] == 1.0
    lift(world, kb, frames=FPS + KB_RELEASE_FRAMES)
    assert_kb_at_rest(kb)


# ------------------------------------------------------------ Glitch Smear playing

@pytest.fixture
def smear(world):
    return world.block('glitch_smear')


def smear_mosh(block):
    return uniforms(block, 'mosh', 0)


def smear_grid(block):
    return uniforms(block, 'mosh', 1)


def smear_heal(block):
    return uniforms(block, 'composite', 0)


def assert_smear_at_rest(block):
    assert block.op('switch').par.index.val == 0
    for name, index in (('motion', 0), ('field', 0), ('mosh', 0), ('mosh', 1), ('composite', 0)):
        assert uniforms(block, name, index) == (0.0, 0.0, 0.0, 0.0), name


def finish_smear(world, block):
    block.par.Trigger = 0.0
    world.tick(block, FPS * 2)


def test_smear_wiring(smear):
    block = smear
    source = block.op('in1')
    assert block.op('previous').inputs == {0: source}
    assert block.op('motion').inputs == {0: source, 1: block.op('previous'), 2: block.op('field_last')}
    assert block.op('field_last').inputs == {0: block.op('motion')}
    assert block.op('field_last').par.top.val == 'field'
    assert block.op('field').inputs == {0: block.op('motion'), 1: block.op('field_last')}
    assert block.op('mosh_last').inputs == {0: source}
    assert block.op('mosh_last').par.top.val == 'mosh'
    assert block.op('mosh').type == 'glslmultiTOP'
    assert block.op('mosh').inputs == {0: source, 1: block.op('previous'), 2: block.op('field'), 3: block.op('mosh_last')}
    assert block.op('composite').inputs == {0: source, 1: block.op('mosh')}
    assert block.op('switch').inputs == {0: source, 1: block.op('composite')}
    assert block.op('out1').inputs[0] is block.op('switch')


def test_smear_previous_frame_is_one_cook_back(smear):
    par = smear.op('previous').par
    assert (par.active.val, par.cachesize.val, par.outputindex.val, par.interp.val) == (True, 2, -1, False)


def test_smear_field_is_one_texel_per_block(smear):
    motion = smear.op('motion').par
    assert motion.outputresolution.val == 'custom'
    assert 'Blocksize' in motion.resolutionw.expr and "op('in1').width" in motion.resolutionw.expr
    assert 'Blocksize' in motion.resolutionh.expr and "op('in1').height" in motion.resolutionh.expr
    assert motion.inputfiltertype.val == 'mipmap'
    for name in ('motion', 'field', 'field_last'):
        assert smear.op(name).par.format.val == 'rgba32float'
    for name in ('mosh', 'mosh_last'):
        assert smear.op(name).par.format.val == 'rgba16float'
    assert smear.op('composite').par.format.val == 'useinput'
    assert smear.par.Blocksize.menuNames == ['8', '16', '32', '64']
    assert smear.par.Blocksize.val == '16'


def test_smear_shader_uniforms_match_their_entries(smear):
    for node in smear.children:
        if node.type not in ('glslTOP', 'glslmultiTOP'):
            continue
        text = smear.op(node.par.pixeldat.val).text
        declared = re.findall(r'^uniform vec\d (\w+);', text, re.M)
        entries = [getattr(node.par, 'vec%dname' % index).val for index in range(node.seq.vec.numBlocks)]
        assert entries == declared, node.name


def test_smear_freezes_on_the_very_first_frame(world, smear):
    smear.par.Trigger = 1.0
    world.tick(smear)
    assert smear.op('switch').par.index.val == 1
    assert smear_grid(smear)[2] == 1.0, 'the mosh pass takes this frame as the reference'
    assert uniforms(smear, 'motion', 0)[:2] == (16.0, 0.0), 'no search against a stale frame'
    assert uniforms(smear, 'field', 0)[1] == 1.0, 'held vectors start clear'
    world.tick(smear)
    assert smear_grid(smear)[2] == 0.0
    assert uniforms(smear, 'motion', 0)[:2] == (16.0, 1.0)
    assert uniforms(smear, 'field', 0)[1] == 0.0


def test_smear_velocity_scales_the_push(world, smear):
    smear.par.Smear = 1.0
    smear.par.Trigger = 0.25
    world.tick(smear)
    assert smear_mosh(smear)[0] == pytest.approx(1.0 + 3.0 * 0.25)
    finish_smear(world, smear)
    smear.par.Trigger = 1.0
    world.tick(smear)
    assert smear_mosh(smear)[0] == pytest.approx(4.0)


def test_smear_zero_moves_at_true_speed(world, smear):
    smear.par.Smear = 0.0
    smear.par.Trigger = 1.0
    world.tick(smear)
    assert smear_mosh(smear)[0] == 1.0


def test_smear_attack_ramps_the_push_from_a_still(world, smear):
    smear.par.Attack = 0.2
    smear.par.Trigger = 1.0
    world.tick(smear)
    assert smear_mosh(smear)[0] == 0.0
    gains = []
    for _ in range(int(FPS * 0.2)):
        world.tick(smear)
        gains.append(smear_mosh(smear)[0])
    assert gains == sorted(gains)
    assert gains[-1] == pytest.approx(1.0 + 3.0 * 0.35)


def test_smear_bloom_sets_the_vector_decay(world, smear):
    smear.par.Trigger = 1.0
    smear.par.Bloom = 0.0
    world.tick(smear)
    assert uniforms(smear, 'field', 0)[0] == 0.0
    smear.par.Bloom = 1.0
    world.tick(smear)
    assert uniforms(smear, 'field', 0)[0] == pytest.approx(0.5 ** (1.0 / (FPS * 2.0)))
    smear.par.Bloom = 0.5
    world.tick(smear)
    assert uniforms(smear, 'field', 0)[0] == pytest.approx(0.5 ** (1.0 / (FPS * 0.5)))


def test_smear_controls_reach_the_shader(world, smear):
    smear.par.Blocksize = '32'
    smear.par.Liquid = 0.6
    smear.par.Ghost = 0.7
    smear.par.Freeze = 0.5
    smear.par.Intra = 1.0
    smear.par.Mix = 0.4
    smear.par.Trigger = 1.0
    world.tick(smear, 2)
    mosh = smear_mosh(smear)
    assert mosh[1:] == pytest.approx((0.7, 0.06, 0.06))
    assert smear_grid(smear) == (32.0, 0.6, 0.0, 0.0)
    assert uniforms(smear, 'motion', 0)[0] == 32.0
    heal = smear_heal(smear)
    assert (heal[0], heal[2], heal[3]) == (0.0, 32.0, 0.4)


def test_smear_intra_off_never_refreshes(world, smear):
    smear.par.Intra = 0.0
    smear.par.Trigger = 1.0
    world.tick(smear)
    assert smear_mosh(smear)[3] == 1000.0


def test_smear_holds_while_pressed(world, smear):
    smear.par.Trigger = 1.0
    world.tick(smear, FPS * 3)
    assert smear.op('switch').par.index.val == 1
    assert smear_heal(smear)[0] == 0.0


def test_smear_release_heals_block_by_block_and_keeps_the_push(world, smear):
    smear.par.Trigger = 1.0
    world.tick(smear, 10)
    push = smear_mosh(smear)[0]
    smear.par.Trigger = 0.0
    previous = 0.0
    for _ in range(int(FPS * 0.8) - 1):
        world.tick(smear)
        heal = smear_heal(smear)[0]
        assert previous < heal < 1.0
        assert smear_mosh(smear)[0] == push
        previous = heal
    world.tick(smear, 2)
    assert_smear_at_rest(smear)


def test_smear_each_hit_heals_in_a_new_order(world, smear):
    seeds = []
    for _ in range(3):
        smear.par.Trigger = 1.0
        world.tick(smear)
        seed = smear_heal(smear)[1]
        world.tick(smear, 5)
        assert smear_heal(smear)[1] == seed
        seeds.append(seed)
        finish_smear(world, smear)
    assert len(set(seeds)) == 3


def test_smear_retrigger_refreezes_and_stops_healing(world, smear):
    smear.par.Trigger = 1.0
    world.tick(smear, 10)
    smear.par.Trigger = 0.0
    world.tick(smear, 20)
    assert smear_heal(smear)[0] > 0.0
    smear.par.Trigger = 1.0
    world.tick(smear)
    assert smear_grid(smear)[2] == 1.0
    assert smear_heal(smear)[0] == 0.0


def test_smear_hit_mode_holds_then_heals(world, smear):
    smear.par.Mode = 'hit'
    smear.par.Hold = 0.5
    smear.par.Trigger = 1.0
    world.tick(smear)
    smear.par.Trigger = 0.0
    world.tick(smear, int(FPS * 0.4))
    assert smear_heal(smear)[0] == 0.0
    world.tick(smear, int(FPS * 0.4))
    assert 0.0 < smear_heal(smear)[0] < 1.0
    world.tick(smear, FPS)
    assert_smear_at_rest(smear)


def test_smear_returns_exactly_to_rest(world, smear):
    smear.par.Trigger = 1.0
    world.tick(smear, 10)
    finish_smear(world, smear)
    assert_smear_at_rest(smear)


def test_smear_panic_and_held_pad_does_not_refire(world, smear):
    smear.par.Trigger = 1.0
    world.tick(smear, 10)
    world.panic = 1.0
    world.tick(smear)
    assert_smear_at_rest(smear)
    world.panic = 0.0
    world.tick(smear, 3)
    assert_smear_at_rest(smear)


def test_smear_reset_clears_the_loops(world, smear):
    smear.par.Trigger = 1.0
    world.tick(smear, 5)
    before = {name: smear.op(name).par.resetpulse.pulses for name in ('previous', 'field_last', 'mosh_last')}
    smear.op('reset_watch').module.onPulse()
    for name, count in before.items():
        assert smear.op(name).par.resetpulse.pulses == count + 1, name
    assert_smear_at_rest(smear)


def test_smear_frame_gap_resets(world, smear):
    smear.par.Trigger = 1.0
    world.tick(smear, 5)
    world.abs_time.frame += 31
    world.tick(smear)
    assert_smear_at_rest(smear)


def test_smear_max_hold(world, smear):
    smear.par.Maxhold = 0.1
    smear.par.Trigger = 1.0
    world.tick(smear, FPS * 2)
    assert_smear_at_rest(smear)
