---
name: td-vj-block
description: Build, tune, or debug a hand-triggered TouchDesigner TOP effect in the TD FX Blox repository, using its shared builder, envelope, and simulator.
---

# Develop a TD FX Blox effect

Read the repo's `CONTRIBUTING.md` for the block interface and verification
workflow. Use the effect modules as the source of truth for network wiring.

1. Pin down the feel at peak strength, how it returns to live video, and what
   velocity changes. Choose a Hit, Hold, or Tail by how the effect is played.
2. Start with the closest working pattern. Zoom Punch uses a direct idle bypass.
   Stutter keeps its buffer recording at idle and reads only captured frames.
   Shockwave drives GLSL uniforms from the envelope. For feedback and tails,
   make reset clear stale state and let the visible tail finish before bypassing.
3. Add or edit one module in `blocks/`, using the existing kit and engine. Verify
   new operator parameters before using them and extend the shared parameter
   fixture as needed. Keep trigger smoothing and controller routing in the host
   project so the block can respond on the firing frame.
4. Simulate the real source with the existing pytest suite. When a TouchDesigner
   session is available, build only the requested effects in a scratch project.
   Check the first press, idle return, reset, and look in the live network.
   Record which checks were simulated and which ran in TouchDesigner.
5. Put tuned defaults in the effect source so the next build carries those
   values forward.
