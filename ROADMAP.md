# TD FX Blox development roadmap

Build a library of hand-played TouchDesigner effects that coding agents can author and test, and that a performer can audition, tune, and trust in a show. Start by making the development loop work end to end for Zoom Punch, Stutter, and Shockwave. Use those three blocks to prove changes to the shared interface before rolling them out across the library.

Keep Python and GLSL in Git and ship self-contained `.tox` components. The standard block is a Base COMP with one primary video TOP input, one output, and the shared Control page. Keep `build`, `reset`, and `apply` as the basic authoring interface. The host project owns MIDI, OSC, scene routing, and beat quantization. See the [current block contract](CONTRIBUTING.md).

## 1. Build a TouchDesigner preview workbench

- Create a scratch project with an effect picker, a single test slot, and a Rebuild action that uses the existing [builder](build.py) for the selected block. Show source and output side by side. Use a moving test pattern and a representative show clip, with widescreen and ultrawide presets.
- Expose the block’s custom parameters alongside velocity, press and release, and reset. Add repeatable sequences for light and full strikes, a hold, and a quick retrigger. Capture short clips that include the onset and return to live video.
- Copy changed parameter names and values into a text snapshot. Use the snapshot to update defaults in the source, then rebuild and replay the same sequence to confirm the result.

**Ready to move on when** [Zoom Punch](blocks/zoom_punch.py), [Stutter](blocks/stutter.py), and [Shockwave](blocks/shockwave.py) each complete the build, audition, tune, rebuild, export, and fresh-project import loop. Record cook errors and any unexpected timing or output as issues for the next milestone.

## 2. Strengthen the shared block contract

- **Trigger events.** Keep the existing float `Trigger` and Hit and Gate behavior. Add a callable `Fire(velocity)` for discrete hits and consume queued events in order at the next render tick. Define how events received in the same frame interact with restart and ignore. Add live pressure or continuous intensity as an optional input when an effect uses it.
- **Timing and state.** Fix the [active-attack divide by zero](engine.py#L67) when `Attack` is edited to zero. Latch duration when each envelope phase begins and keep look controls such as color and mix live. Provide effects with a shared frame context that includes time and delta. Let a block declare a tail that outlives the envelope or background work that continues while idle. Define how reset, panic, and disable clear queued hits and local state, including what happens if a gate is still held.
- **Output and resource use.** Define and verify exact dry output at idle and `Mix = 0`, including pixel values, alpha, dimensions, and pixel format. Add a disable control that returns dry video and stops background work. Measure Stutter’s cache and GPU-heavy blocks at show resolution. Use those measurements to choose a cache budget or reduced-resolution recording option and define when a restarted buffer is ready.
- **Compatibility.** Preserve existing parameter names and mappings. Embed the effect ID, interface version, and source revision in each export. Declare extra inputs where the effect needs them, starting with Ken Burns’ photo TOP and Polaroid’s image folder. Add a second video socket when a concrete effect, such as an A/B transition, needs it.

**Ready to move on when** the three reference blocks pass the existing simulations plus coverage for the new behavior. In TouchDesigner, verify first press, rapid retrigger, live parameter edits, tail completion, disable and re-enable, and dry output. Run two copies of one block to check state isolation, then run a short chain to check composition. Reimport the exports in a fresh project and verify that existing `Trigger` mappings still work.

## 3. Give agents the live review loop

- Expose the workbench’s existing actions to an agent in a dedicated scratch session. Start with rebuilding one named block, setting custom parameters, running a trigger sequence, and collecting cook or shader errors and captures. Return the input preset, settings, and source revision with each result so a person can replay it.
- Update the [contributor guide](CONTRIBUTING.md) and [agent skill](.agents/skills/td-vj-block/SKILL.md) once the workflow is proven. For a new effect, describe the peak look, velocity response, and return to dry video. Use the closest working block as a starting point. Run simulation for state and wiring, then audition the result in TouchDesigner. The performer signs off on the look and feel, and tuned defaults go back into source.

**Ready to move on when** an agent can make a source change and return a reproducible live clip and diagnostics for review. The same parameter values should reproduce the approved look after export and reimport.

## 4. Grow the catalog and release in batches

- Keep stable effect IDs and the flat `blocks/` directory. Maintain one metadata entry per effect and use it to generate [the catalog](CATALOG.md) and populate the workbench picker. Include a short description, visual tags, play style, velocity response, extra inputs, preview link, and live verification record. Keep the original show approval as provenance. Record each standalone check with the source revision, TouchDesigner build, and platform.
- Roll out the new contract in batches organized by behavior, such as stateless effects, rolling buffers, effects with tails, and media-dependent blocks. After shared engine changes, run the full simulation suite. For each release, build from tagged source, smoke-test each included `.tox` in a fresh project, and package the verified exports with their compatibility notes.
- Add a browser gallery of captured clips once the catalog is large enough to benefit from visual search. Use the same metadata and previews as the workbench.

**First release target** is a tagged pack of the three reference blocks, with repeatable previews, documented compatibility, and standalone live checks. Expand the pack as each batch clears the same checks.
