# ChessView

Point your phone at a physical chess board and see live Stockfish analysis overlaid
on the camera feed — best move, evaluation, and top engine lines, updating within
about a second of a piece moving. No shutter button, no manual FEN entry.

> **Status: M0 — pipeline skeleton.** The end-to-end path (camera → cloud → engine →
> overlay) runs against a *stub detector* that replays scripted positions. Real
> computer vision lands at M2. See [docs/roadmap.md](docs/roadmap.md).

## How it works

```
 phone                          cloud
┌────────────────────┐        ┌──────────────────────┐     ┌───────────────────┐
│ camera 30fps       │        │ vision service       │     │ engine service    │
│  ├ homography warp │  WSS   │  ├ per-square CNN    │     │  ├ Stockfish pool │
│  ├ motion gate     │ ─────► │  ├ legal-move match  │ ──► │  ├ MultiPV 3      │
│  └ JPEG ~22 KB     │ ◄───── │  └ stability gate    │ ◄── │  └ progressive    │
│ overlay UI         │        │  authoritative state │     │     depth 10→20   │
└────────────────────┘        └──────────────────────┘     └───────────────────┘
```

Three design decisions carry most of the weight:

1. **Rectify on-device, classify in the cloud.** The phone warps the board to a flat
   320×320 crop before sending. That cuts bandwidth ~10x, removes all perspective
   variance from the model's input, and means the camera's view of the room never
   leaves the device — only the board does.
2. **Diff against legal moves, never re-read the whole board.** A full 64-square read
   is wrong more often than it is right (99.5% per-square accuracy still yields a
   73% chance of a correct board). After calibration we track *state* and ask which
   of the ~35 legal moves explains what changed.
3. **Stream the evaluation progressively.** Depth 10 lands in ~80 ms and refines
   upward. Blocking on final depth would blow the latency budget on exactly the
   complex positions that matter.

The reasoning behind each, with sources, is in [docs/architecture.md](docs/architecture.md).

## Layout

| Path | What it is |
|---|---|
| `app/` | Expo / React Native client (iOS + Android) |
| `services/vision/` | WebSocket endpoint, session state, detection pipeline |
| `services/engine/` | Stockfish process pool behind a UCI wrapper |
| `packages/protocol/` | Wire protocol — pydantic models are the source of truth, TS types are generated |
| `infra/` | Deployment |
| `docs/` | Architecture, roadmap, vision-model notes |

## Getting started

```bash
make setup          # virtualenv + Python packages + npm workspaces
make test           # full Python suite
make run-vision     # WebSocket endpoint on :8000
```

The mobile client needs a **custom Expo dev client** — camera frame processors are
native, so **Expo Go cannot run this app**:

```bash
cd app && npx expo run:ios     # or: npx expo run:android
```

### Editing the protocol

`packages/protocol/chessview_protocol/messages.py` is the single source of truth.
After changing it:

```bash
make protocol       # regenerates schema.json and src/generated.ts
```

`make protocol-check` fails on stale generated files and runs in CI, so the client
and services cannot drift apart.

## Testing on real hardware

Camera lifecycle, thermals, network behaviour under a weak signal, and detection
accuracy are all invisible in a simulator. Every milestone is validated on physical
iOS and Android devices; the simulator is only useful for layout work.
