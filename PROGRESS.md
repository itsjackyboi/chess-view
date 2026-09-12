# ChessView Build Progress

## M0 — Done

- Set up the project as a monorepo with separate services for vision and analysis.
- Created the shared message format that all parts use to talk to each other.
- Built a pool of chess engines where each session gets its own.
- Made the engines send evaluations as they compute, not waiting until done.
- Fixed scores to always show things from white's perspective.
- Built board state tracking that matches observations against legal moves.
- Built the mobile app that streams camera frames and displays live analysis.
- Implemented safety rules so frozen evaluations never look current.
- Made the app automatically reconnect with smart backoff logic.
- Added CI that catches when Python and TypeScript drift out of sync.

## M1 — Done

- Validated the whole pipeline end-to-end from camera to analysis display.

## M2 — Done

- Built a two-stage vision pipeline: detect the board, then classify each square.
- Trained a neural network on 1500 computer-generated chess board images.
- Achieves 98% accuracy on whole boards with manual corner alignment (synthetic images only, not real photos).
- Only gets 79% accuracy when the computer detects the board corners itself.
- Model is 826 KB and uses only onnxruntime at runtime, not PyTorch.
- Can tell when it is unsure and stays quiet instead of showing wrong answers.

## M3 — In progress

- Added four draggable corner markers for precise board alignment.
- Manual corners get 98% accuracy; automatic detection only gets 79%.
- Tracks corners across frames with smoothing to prevent hand shake from causing jitter.
- Validates corners and asks for recalibration if they stay rejected.

## M4 — Done

- Added a command-line tool that reads chess boards from pictures and outputs analysis.

## M5 — Done

- Added a correction editor with a 2D board view to fix misread pieces.
- Highlights uncertain squares so users know where to look.
- Pauses streaming while the editor is open.

## M6 — Done

- Containerized the server with automatic TLS for secure mobile connections.
- Added rate limiting to prevent one user from flooding the engine pool.
- Added health metrics and monitoring so problems are visible.
- Shuts down gracefully instead of cutting off active sessions mid-game.
- Added protection against malicious images designed to crash the server.
- Packaged and ready to deploy, but not yet running online.

## What's Left

- Train the model on real photographs of chess boards, not just computer-generated images.
- Add phone-specific code to crop and align the board efficiently on the device.
- Test the app on actual phones to find real-world problems.
- Deploy the server to a real online host.
