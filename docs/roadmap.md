# Roadmap

Built in order, each milestone validated before the next starts. The full real-time
pipeline is deliberately never step one.

| # | Milestone | Exit criteria | Status |
|---|---|---|---|
| **M0** | Skeleton, protocol, **stub detector** replaying scripted FENs | Real device: camera → WSS → engine → overlay under 1 s, no ML involved | **done** (pending device run) |
| **M1** | Engine service — Stockfish pool, MultiPV 3, progressive depth | p95 first eval < 150 ms, stable under concurrent sessions | |
| **M2** | **Single still image → FEN** — corners, rectify, classify, accuracy harness | Measured board-level accuracy on our own labelled test set | **done on synthetic**; real photos outstanding |
| **M3** | Calibration UI, on-device homography + optical flow, motion gating | Board crop stays locked through hand shake; uplink ≈ 15 MB/hr | partly — UI and maths done, native warp outstanding |
| **M4** | Temporal engine — square diff, legal-move matcher, stability, confidence gate | Move recognition accuracy over a full recorded game | **done** (built early at M0.3) |
| **M5** | Hardening — reconnect, degraded states, 2D correction board, battery/background | Both platforms on physical hardware | **done** (pending device run) |
| **M6** | Deploy, monitoring, design pass, store prerequisites | Reachable over the internet with no dev machine in the loop | **config done**, not yet deployed |

## M0 status

Built and green in CI: protocol, engine service, vision service, tracking, and the
client, with 170 tests across Python and TypeScript. The pipeline is proven
end-to-end through a real WebSocket against a real Stockfish, with only the detector
stubbed.

Two things remain before M0 can be called finished outright:

- **A run on physical hardware.** Everything except the camera path is covered by
  tests; the camera path cannot be. This is the one exit criterion still open.
- **Deployment.** The services run locally. M6 covers putting them somewhere the app
  can reach, which needs the hosting decisions in docs/architecture.md settled.

## M2 gate: passed on synthetic, unproven on real photos

The gate was meant to decide whether M4 was a tuning exercise or a research project.
On synthetic data the pipeline reaches **99.3% board-level accuracy with calibrated
corners** and the confidence gate cleanly separates correct readings (0.916) from
incorrect ones (0.429). Full figures in [vision.md](vision.md).

That is a genuine held-out measurement and it says the *pipeline* is sound. It does
not say the model will work on photographs of real chess sets, and it should never be
quoted as though it did — rendered pieces are cleaner, better lit and more consistent
than wood under a kitchen lamp.

**So the remaining M2 risk is entirely a dataset problem.** Photographs of real
boards, across several chess sets and lighting conditions, with labels. Everything
that consumes them is built and measured.

M0's stub detector existed so the whole product was provably working end-to-end
before the ML risk landed, which is why swapping the real model in was a
configuration change.

## What is actually outstanding

1. **Real photographs.** The dataset gap above. The single biggest risk.
2. **The on-device warp.** Needs a native frame-processor plugin. Until it exists the
   client sends downscaled full frames and the server rectifies — which works, but the
   bandwidth and privacy properties in [architecture.md](architecture.md) are targets
   rather than facts.
3. **A run on physical hardware.** Camera lifecycle, thermals and real network
   behaviour are invisible to the test suite.
4. **Deployment.** The config exists; nothing is running anywhere yet. Blocked on
   choosing a hosting region.

## Real-device testing from M0

Camera lifecycle, thermals, real network behaviour and detection accuracy are all
invisible in a simulator. Frame processors are native, so a custom Expo dev client
(**not Expo Go**) exists from day one regardless.

Per-milestone manual checks: airplane-mode toggle mid-session (reconnect), hand over
board (occlusion), dim room (low-light degradation), 30-minute session (battery and
thermals).
