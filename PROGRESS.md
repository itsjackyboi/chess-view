# ChessView Build Progress

## M0 — Done

- Set up the project as a monorepo with separate services for vision and analysis.
- Created the shared message format that all parts use to talk to each other.
- Made sure camera frames and their metadata stay together as one message.
- Tested that Python and TypeScript both read and write the format the same way.
- Built a pool of chess engines where each session gets its own.
- Made the engines send evaluations as they compute, not waiting until done.
- Fixed scores to always show things from white's perspective.
- Built board state tracking that matches observations against legal moves.
- Made the tracker wait for the same board position twice before accepting it.
- Built the mobile app that streams camera frames and displays live analysis.
- Implemented safety rules so frozen evaluations never look current.
- Made the app automatically reconnect with smart backoff logic.
- Added CI that catches when Python and TypeScript drift out of sync.
- Added automated tests for the chess engine service.

## M1 — Not started

## M2 — Not started
