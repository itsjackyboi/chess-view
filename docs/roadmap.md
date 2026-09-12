# Roadmap

Built in order, each milestone validated before the next starts. The full real-time
pipeline is deliberately never step one.

| # | Milestone | Exit criteria | Status |
|---|---|---|---|
| **M0** | Skeleton, protocol, **stub detector** replaying scripted FENs | Real device: camera → WSS → engine → overlay under 1 s, no ML involved | in progress |
| **M1** | Engine service — Stockfish pool, MultiPV 3, progressive depth | p95 first eval < 150 ms, stable under concurrent sessions | |
| **M2** | **Single still image → FEN** — corners, rectify, classify, accuracy harness | Measured board-level accuracy on our own labelled test set | |
| **M3** | Calibration UI, on-device homography + optical flow, motion gating | Board crop stays locked through hand shake; uplink ≈ 15 MB/hr | |
| **M4** | Temporal engine — square diff, legal-move matcher, stability, confidence gate | Move recognition accuracy over a full recorded game | |
| **M5** | Hardening — reconnect, degraded states, 2D correction board, battery/background | Both platforms on physical hardware | |
| **M6** | Deploy, monitoring, design pass, store prerequisites | Reachable over the internet with no dev machine in the loop | |

## M2 is the go/no-go gate

Its measured accuracy decides whether M4 is a tuning exercise or a research project.
Better to learn that in week two against a test harness than in week eight against a
camera.

M0's stub detector exists precisely so the whole product is provably working
end-to-end *before* the ML risk lands.

## Real-device testing from M0

Camera lifecycle, thermals, real network behaviour and detection accuracy are all
invisible in a simulator. Frame processors are native, so a custom Expo dev client
(**not Expo Go**) exists from day one regardless.

Per-milestone manual checks: airplane-mode toggle mid-session (reconnect), hand over
board (occlusion), dim room (low-light degradation), 30-minute session (battery and
thermals).
