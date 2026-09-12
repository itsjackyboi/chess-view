# ChessView

Point a phone at a physical chess board, see live Stockfish analysis on the camera
feed. See `README.md` for the shape of the system and `docs/architecture.md` for why
it is built that way.

## Commands

```bash
make setup          # virtualenv, Python packages, npm workspaces
make test           # full Python suite
make bench          # engine latency against the architecture's targets
make protocol       # regenerate schema.json + TypeScript after editing the protocol

cd app && npx vitest run     # client tests
cd app && npx tsc --noEmit   # client typecheck
```

Python work runs through `.venv/bin/python`, not a global interpreter. Anything
under `services/vision/` importing `training.*` needs `PYTHONPATH=services/vision`.

## Things that will bite you

**The protocol is generated.** `packages/protocol/chessview_protocol/messages.py` is
the single source of truth; `schema.json` and `src/generated.ts` are outputs. Edit
the pydantic models and run `make protocol`. CI fails on stale output.

**Per-square accuracy is a misleading metric.** 64 squares must all be right for one
position to be right, so 99% per square is a coin flip at board level. Always report
board-level accuracy alongside it. `services/vision/evaluate.py` does both.

**Confidence is the weakest square, never the mean.** A board is only as trustworthy
as its worst square, and averaging hides exactly the one that makes the position
wrong.

**Corner ordering exists in two languages.** `order_corners` in
`services/vision/chessview_vision/board.py` and `orderCorners` in
`app/src/camera/homography.ts` must agree. A disagreement mirrors the board and
produces a wrong position that still looks valid.

**Never show analysis that might be stale.** `deriveAnalysis` in
`app/src/state/reducer.ts` fails closed on every path. A frozen evaluation presented
as live is worse than none, because the user cannot tell.

**Don't raise the uvicorn worker count.** The engine pool and session state live in
process memory; a second worker holds its own, and a reconnecting client routed to
the wrong one silently loses its game.

## Testing

Integration tests drive a real Stockfish (`/usr/games/stockfish` on Debian, which is
not on `PATH` — `CHESSVIEW_STOCKFISH` overrides). They exist because progressive
arrival, clean cancellation and perspective-correct scores do not show up against a
mock; one of them already caught a wrong-best-move bug.

Client tests cover the pure modules only — reconnection, state reduction, formatting,
the frame gate, homography. Anything touching the camera is validated on a physical
device, which is also why **Expo Go cannot run this app**: frame processors are
native and need a custom dev client.

## Current state

M0, M5 and M6 are done. M2 (vision) and M3 (on-device rectification) are partly done:

- The model is trained on **synthetic renders only**. Its accuracy on those is real
  and measured, and it does not predict performance on photographs of real boards.
  That gap is the main outstanding risk.
- The client does not yet warp on-device, so it sends downscaled full frames and the
  server rectifies. The warp needs a native frame-processor plugin. Until it lands,
  the bandwidth and privacy claims in `docs/architecture.md` are targets.
