# ChessView Build Progress

## M0 — Done

- Set up the project as a monorepo with separate services for vision and analysis.
- Created the shared message format that all parts use to talk to each other.
- Tested that Python and TypeScript both read and write the format the same way.
- Built a pool of chess engines where each session gets its own.
- Made the engines send evaluations as they compute, not waiting until done.
- Fixed scores to always show things from white's perspective.
- Built board state tracking that matches observations against legal moves.
- Built the mobile app that streams camera frames and displays live analysis.
- Implemented safety rules so frozen evaluations never look current.
- Made the app automatically reconnect with smart backoff logic.
- Added CI that catches when Python and TypeScript drift out of sync.

## M1 — Not started

## M2 — In progress

- Built a two-stage vision pipeline: detect the board, then classify each square.
- Trained a neural network on computer-generated chess board images.
- Achieves 95% accuracy on whole boards from training data (not real photos yet).
- Model is tiny (33 KB) and uses only onnxruntime at runtime, not PyTorch.
- Reports confidence as the weakest square, not the average.

## M3 — In progress

- Added four draggable corner markers for precise board alignment.
- Manual corners are the primary path; automatic detection is a starting guess.
- Validates corners before accepting them to prevent bad warps.

## M4 — Not started

## M5 — Done

- Added a correction editor with a 2D board view to fix misread pieces.
- Highlights uncertain squares so users know where to look.
- Pauses streaming while the editor is open.

## M6 — Done

- Containerized the server with automatic TLS for secure mobile connections.
- Stripped PyTorch from the runtime image to keep it small.
- Health check exercises the engine pool and model to catch real problems.
