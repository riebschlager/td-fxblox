# Contributing to TD FX Blox

## Design and build

Describe what happens at peak strength, how velocity changes it, and how the
image returns to the input. Choose a Hit, Hold, or Tail based on how it feels
to play. Hits and tails generally use Hit mode. Holds generally use Gate mode.

Add `blocks/<effect_id>.py` with a `build(library)` entry point. Use the helpers in
`kit.py` to create the component and embed the effect and engine as Text DATs.
Use [Zoom Punch](blocks/zoom_punch.py) for stateless effects,
[Stutter](blocks/stutter.py) for rolling buffers, and
[Shockwave](blocks/shockwave.py) for GLSL. Check new TouchDesigner parameter
names in the target build or [Derivative's documentation](https://docs.derivative.ca/).
Record verified parameters in `tests/td_op_params.json`.

Edit [palettes.py](palettes.py) to update the shared palette menus and colors.
Each row has five distinct RGB hex stops in display order. The builder
embeds the selected blocks' menus, GLSL tables and Python color data in the
components. Rebuild affected blocks after changing the catalog.

## Block contract

- Export a Base COMP with one `in1` In TOP and one `out1` Out TOP. Preserve input
  resolution and pass through the exact input at idle.
- Use the shared Control page. `Trigger` is a float. Zero is off and positive
  values encode velocity. Keep controller mappings in the host project.
- Put the effect controls on a separate page ending with `Mix`. Use normalized
  controls where that fits and capitalize only the first letter of parameter names.
- Expose `reset(block)` and `apply(block, env)` in the effect DAT. Reset clears
  local state. Apply drives the effect using the envelope's `stage`, `level`,
  `velocity`, `progress`, and `started`. Restore the rest state when the envelope
  returns to idle.
- Keep each exported component portable. Declare any user-provided inputs in the
  catalog or docs. Changes to `engine.py` apply to all newly built components, so
  verify the whole pack when the shared engine changes.

## Verify and release

Run the simulation suite with Python 3.11.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Add effect-specific cases to the shared harness. Cover wiring, the first press
after load, the return to idle, and any state, retrigger, or reset behavior
specific to the effect. CI uses the same suite. Simulation verifies Python state
and wiring against a stub. Shader compilation, pixels, and timing need a live run.

Run `python3 tools/build_tox.py <effect_id>` on the TouchDesigner machine to
export from a scratch project. Load the resulting `.tox` into a fresh project,
confirm it cooks without errors, and play it at different
velocities. Check idle output and reset, and use TouchDesigner's Performance
Monitor for effects that buffer frames or use heavier shaders. Save any tuned
defaults in the source and add or update the effect's entry in [the catalog](CATALOG.md).

For a release, build from the tagged source, package the live-tested `.tox`
files together, and record the effects, TouchDesigner build, and platform tested.
A copied component needs to be replaced or deliberately upgraded to use a new
engine. Preserve existing parameter names where possible so people can carry
their mappings forward.

`tools/Build.toe` contains a single startup Execute DAT at
`/project1/build_on_start`. If its source in `tools/td_startup.py` changes,
paste that source into the DAT and save the project. Keep the DAT's **Start**
toggle enabled. The export worker in `tools/td_build.py` is loaded from a fresh
copy each time the launcher runs.
