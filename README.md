# TD FX Blox

Hand-played video effects for TouchDesigner. Each block takes one video TOP in,
passes it through at idle, and reacts to a velocity-sensitive trigger. The
[catalog](CATALOG.md) lists the 32 effects carried over from THUNDERGONG! 2026.

This is a source scaffold. The first pack of prebuilt components is pending
live checks in TouchDesigner.

## Try three effects

Open a blank TouchDesigner project. Paste the following into the Textport,
changing the path to your checkout if needed.

```python
import runpy
runpy.run_path("/Users/yourname/Projects/td-fxblox/build.py", init_globals=dict(globals(), FXBLOX={"blocks": ["zoom_punch", "stutter", "shockwave"]}), run_name="__main__")
```

The build creates the three components in `/vj_block_library` and exports them
to `dist/` beside `build.py`. Drag a `.tox` from there into a fresh project.
Wire a TOP with moving video or a generated test pattern to the block, then wire
the block to a Null TOP and view the result. On the block's **Control** page,
change **Trigger** from 0 to 1 to play it and back to 0 to release or rearm it.
Try a smaller trigger value to see how the effect responds to velocity.

Omit `blocks` from the options to build the entire catalog. Set `output` to an
absolute directory to export elsewhere. Building again replaces the selected
library components and export files. Copies already placed in other networks
keep their own parameters and engine.

## Use your own controls

Map MIDI, OSC, or any other control source to **Trigger** in your project.
**Hit** mode fires on a rising edge and plays the programmed envelope.
**Gate** mode sustains while the trigger is held. **Reset** clears the state.

For an optional shared panic channel, pass `panic_chop` and `panic_channel` in
`FXBLOX`. For scene reset, pass `scene_tag` to let the block watch the enclosing
component with that tag for an `Init` pulse. Both integrations are off by default.

Polaroid reads images from the folder you select. Relative paths resolve from
the current TouchDesigner project folder. Adjust **Side Region Width** for the
left and right presets, or select **Custom** to frame your own region.

## Develop an effect

The [contributor guide](CONTRIBUTING.md) covers the block interface, simulation,
and live checks. Codex can use the repo-local [`td-vj-block` skill](.agents/skills/td-vj-block/SKILL.md)
for the same workflow.

The effect sources and simulation harness were extracted from THUNDERGONG! 2026
at commit `aa4aa5623596175b923926a5c16195644eda24f7`.
