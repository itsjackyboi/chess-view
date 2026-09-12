# ChessView

Point your phone at a physical chess board and see live Stockfish analysis overlaid
on the camera feed — best move, evaluation, and top engine lines, updating within
about a second of a piece moving. No shutter button, no manual FEN entry.

> **Status: working end to end, not yet shipped.** Camera → cloud → vision → engine →
> overlay all runs, with a trained model doing the detection. It reaches **99.3%
> board-level accuracy on synthetic boards** — which is a real held-out measurement
> and *not* a prediction of how it will do on photographs of real chess sets. That
> gap, a physical-device run, and an actual deployment are what remain. See
> [docs/roadmap.md](docs/roadmap.md).

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

The reasoning behind each, with sources, is in [docs/architecture.md](docs/architecture.md);
the vision pipeline and its measured accuracy are in [docs/vision.md](docs/vision.md).

## Layout

| Path | What it is |
|---|---|
| `app/` | Expo / React Native client (iOS + Android) |
| `services/vision/` | WebSocket endpoint, session state, board tracking, detection |
| `services/vision/training/` | Synthetic data generation and model training (PyTorch, dev only) |
| `services/engine/` | Stockfish process pool behind a UCI wrapper |
| `packages/protocol/` | Wire protocol — pydantic models are the source of truth, TS types are generated |
| `models/` | The exported ONNX classifier (~35 KB, committed) |
| `infra/` | Dockerfile, compose stack, deployment guide |
| `docs/` | Architecture, vision pipeline, roadmap |

## Getting started

```bash
make setup          # virtualenv + Python packages + npm workspaces
make test           # full Python suite
make run-vision     # WebSocket endpoint on :8000
```

### See it work on one image

The fastest way to check the whole pipeline, no phone required:

```bash
PYTHONPATH=services/vision .venv/bin/python services/vision/demo.py board.jpg \
    --corners 194,117 569,127 630,651 118,652
```

```
8 r . b q k b n r
7 . p p p . p p p
6 p . n . . . . .
5 . B . . p . . .
4 . . . . P . . .
3 . . . . . N . .
2 P P P P . P P P
1 R N B Q K . . R
  a b c d e f g h

  confidence   0.901  (weakest square)
  detection    46ms

  depth 16
               +0.15   Ba4 Nf6 Nc3 Bb4 Nd5 Nxd5
```

Omit `--corners` to let it find the board itself — and watch the confidence fall,
which is the point: detection alone is not accurate enough, and the system says so
instead of guessing.

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

## What is not done

- **The model has only seen synthetic boards.** Its accuracy on those is real and
  measured; it will be worse on photographs. Closing that needs real images, and
  it is the biggest remaining risk.
- **The client does not warp on-device yet.** It sends downscaled full frames and
  the server rectifies. The warp needs a native frame-processor plugin, and until
  it lands the bandwidth and privacy properties above are targets rather than facts.
- **Nothing is deployed.** The container config exists and its dependency set is
  verified, but `docker build` has never been run.
- **Nothing has run on a phone.** Everything except the camera path is covered by
  tests. The camera path cannot be.

## Testing on real hardware

Camera lifecycle, thermals, network behaviour under a weak signal, and detection
accuracy are all invisible in a simulator. Every milestone is validated on physical
iOS and Android devices; the simulator is only useful for layout work.
