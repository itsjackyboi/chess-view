# ChessView architecture

Why the system is shaped the way it is. The short version lives in the README; this
is the reasoning and the evidence behind it.

## The central problem: whole-board reads don't work

Reading all 64 squares independently means every square has to be right at once:

| Per-square accuracy | Whole-board accuracy |
|---|---|
| 99.0% | 52% |
| 99.5% | 73% |
| 99.83% (best published) | 90% |

Published full-system results sit around **93.4% board-level accuracy** under
favourable conditions ([Wölflein & Arandjelović 2021][wolflein]), and a 2025 system
reports **29.9% of boards yielding a perfect FEN** ([CVChess][cvchess]). At 93%,
roughly one position in fifteen is wrong — and a confidently-rendered wrong
evaluation is worse for the user than no evaluation at all.

**So the board is read in full exactly once, at calibration, with the user
confirming it.** From then on the system holds the position as game state and asks a
much narrower question on each update: *which of the ~35 legal moves from the current
position best explains what changed?*

This turns a 13^64 classification problem into a ~35-way choice. The literature puts
the win at **~95% move recognition accuracy autonomously, ~99% when the system asks
the user about low-confidence cases** ([Wölflein & Arandjelović][wolflein]).

It also makes the honesty requirement fall out for free: when no legal move explains
the observation confidently, there is nothing to guess at, so the client shows
`unclear` rather than a fabricated position.

## Latency budget

Target: a piece settles, and the overlay reflects it within ~1 second.

| Stage | Estimate |
|---|---|
| Capture, rectify, downscale, JPEG encode (device) | 15–40 ms |
| Uplink ~22 KB (LTE / WiFi) | 40–120 / 10–30 ms |
| Decode + 64-crop classify (server CPU) | 15–30 ms |
| Downlink FEN | 20–60 ms |
| **Subtotal** | **~100–250 ms** |
| Stability confirmation (3 frames @ 5 fps burst) | ~600 ms |
| **New FEN on screen** | **~750–850 ms** |

Two choices make this fit:

- **Confirm in ~600 ms, not 2 s.** Stability checking is necessary — a hand over the
  board must not commit a position — but N and the burst rate have to be chosen
  against the budget. Three frames at a 5 fps burst works; three at 2 fps does not.
- **Progressive evaluation.** Stockfish at depth 18 takes 0.3–1.5 s and is *variable*;
  blocking on it busts the budget on exactly the complex positions users care about.
  Depth ~10 ships at ~80 ms and refines upward to depth 20 / 1.2 s. The bar appears
  immediately and sharpens, which is how Lichess and chess.com feel instant.

## Transport: WebSocket, not WebRTC

We send ~1 fps of discrete JPEGs, event-driven — not a video stream. WebRTC's
advantages (congestion control, adaptive bitrate, UDP avoiding head-of-line blocking)
pay off at 30 fps continuous video. At our rate, TCP head-of-line blocking costs at
most one frame interval, which the confirmation window already absorbs. Against that
it costs signalling infrastructure, STUN/TURN, a heavy React Native dependency, and
much harder debugging.

Revisit only if instrumentation shows uplink stalls.

## Event-driven streaming

During a real game the board is static ~95% of the time, so a constant 3 fps upstream
mostly pays to send the same image repeatedly. Instead the client runs cheap frame
differencing in the camera worklet and streams only when something moves: **0.5 fps
idle keepalive, bursting to 5 fps on motion until the position is stable.**

Estimated uplink drops from ~80 MB/hr to **~15 MB/hr** — a >5x cut in data, battery
and cloud compute, with *better* accuracy, since every frame sent is one worth
classifying.

## Where state lives

Authoritative game state — current FEN, move history, calibration homography, manual
overrides — lives in the **vision service, in memory, keyed by session ID**. Not on
the client, not in a database.

This keeps the legal-move matcher and the engine agreeing on what position is being
analysed, and makes reconnection a resync rather than a replay. Nothing in v1 needs
to outlive a session, so there is no database.

## Vision pipeline (lands at M2–M4)

Two stages, deliberately *not* YOLO on the raw frame:

1. **On-device: board localisation → homography.** The user drags four corners at
   calibration; optical flow tracks them at camera frame rate afterwards. Hand shake
   is corrected locally at 30 fps instead of over a network round-trip at 3 fps.
2. **Server: per-square occupancy + piece classification** over the warped 8×8 grid,
   13 classes.

Rectifying first removes all perspective variance from the model's input rather than
forcing it to learn invariance — a smaller model, a faster one, and CPU-viable.

**Occlusion** is geometric: at a low angle a piece on rank 7 hides one on rank 8.
Mitigated by extending each crop upward (pieces are tall) and by coaching the user
toward an elevated angle during calibration.

## Privacy

- **Geometric minimisation.** Only the rectified 320×320 board leaves the phone.
  Faces, room and surroundings are cropped out *before transmission*, not discarded
  afterwards.
- **No frame persistence.** Frames are decoded, classified and dropped in RAM — never
  written to disk, never logged. Only derived state is retained, for the session only.
- **No identity.** WSS throughout, opaque random session IDs, no account, no PII, no
  device identifier.
- **Model-improvement capture is opt-in and off by default.**

## Open items

- **Hosting region** — round-trip dominates the budget; needs to follow the users.
- **chesscog licensing** — must be verified before the pretrained weights become
  load-bearing at M2.

[wolflein]: https://arxiv.org/pdf/2104.14963v1
[cvchess]: https://arxiv.org/pdf/2511.11522
