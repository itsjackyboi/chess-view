# Deploying ChessView

One host runs everything: the vision service (which embeds the engine pool) behind
Caddy for TLS. This is the "launch" tier from [docs/architecture.md](../docs/architecture.md)
— roughly €60/month of dedicated Ryzen, good for about 50 concurrent sessions.

## Before you start

Two decisions are still open and both affect this:

- **Region.** Round-trip latency dominates the budget, so the host should sit near
  the users. Pick this before provisioning; moving later means reissuing certificates
  and repointing the app.
- **Provider.** The recommendation is Hetzner AX-series on price-per-core, since
  Stockfish is what consumes the box. Nothing here is Hetzner-specific.

## Prerequisites

- A host with Docker and the compose plugin.
- A DNS `A` record pointing at it. Caddy provisions certificates automatically, and
  mobile platforms refuse anything but `wss://`, so TLS is not optional.
- Ports 80 and 443 reachable. Port 80 is needed for the ACME challenge even though
  nothing is served over it.

## Build the model first

The image bakes in `models/square-classifier.onnx`. It is committed to the repo, so
a clean checkout builds without training. To retrain:

```bash
PYTHONPATH=services/vision .venv/bin/python services/vision/training/train.py \
    --train-boards 1500 --epochs 14 --out models/square-classifier.onnx
```

Then check what you got before shipping it:

```bash
PYTHONPATH=services/vision .venv/bin/python services/vision/evaluate.py \
    --model models/square-classifier.onnx --boards 300
```

> **The accuracy that harness reports is on synthetic renders.** It is a real
> held-out measurement and it will not predict performance on photographs of real
> boards. Treat it as a regression check on the pipeline, not as a product metric.

## Deploy

```bash
export CHESSVIEW_DOMAIN=api.example.com
docker compose -f infra/docker-compose.yml up -d --build
curl -fsS https://$CHESSVIEW_DOMAIN/health | jq
```

A healthy response looks like:

```json
{
  "status": "ok",
  "engine": "Stockfish 17",
  "pool": { "size": 4, "available": 4 },
  "detector": "model",
  "protocolVersion": 1
}
```

**Check `detector`.** If it says `stub`, the service is reporting scripted positions
that have nothing to do with the camera — the model is missing or `CHESSVIEW_MODEL`
points at the wrong path. The container logs say which.

## Point the app at it

```bash
EXPO_PUBLIC_CHESSVIEW_URL=wss://api.example.com/v1/session npx expo run:ios
```

## Tuning

Defaults in `docker-compose.yml` assume 8 cores.

| Variable | Default | Notes |
|---|---|---|
| `CHESSVIEW_ENGINE_POOL_SIZE` | 4 | One engine per active session — this is the concurrency ceiling |
| `CHESSVIEW_ENGINE_THREADS` | 2 | `pool_size × threads` should land near the core count |
| `CHESSVIEW_ENGINE_DEPTH` | 20 | Target depth; the movetime cap usually binds first |
| `CHESSVIEW_ENGINE_MOVETIME_MS` | 1200 | The hard latency bound on analysis |
| `CHESSVIEW_STABILITY_FRAMES` | 3 | Agreeing frames before committing a move |
| `CHESSVIEW_MIN_CONFIDENCE` | 0.6 | Below this the client is told "unclear" |
| `CHESSVIEW_EXPECT_RECTIFIED` | 0 | Set to 1 once the client warps on-device (M3) |

**Do not raise the worker count.** The engine pool and session state live in process
memory. A second worker would hold its own pool and its own sessions, and a
reconnecting client routed to the wrong one would silently lose its game. Scale by
running more containers behind session affinity.

Re-run `make bench` on the real host before tuning depth — the numbers in
`docs/architecture.md` were measured on a shared CI container and a dedicated box
reaches further within the same time budget.

## Scaling past one box

The engine pool is a module boundary inside the vision service, not a separate
container, because splitting it only pays once the tiers need to scale
independently. When that happens:

1. Give the engine pool a small RPC surface and run it as its own service.
2. Put the vision tier behind a load balancer with **session affinity** — a session's
   state is in one process's memory, so its reconnects must return there.
3. Keep both tiers in one region. A second internet hop between them would come
   straight out of the latency budget.

## What is not here yet

- **Metrics.** `/health` is a liveness check, not instrumentation. Session counts,
  detection confidence distributions and engine queue depth all want exporting
  before this carries real traffic.
- **Rate limiting.** Sessions are anonymous, and nothing currently stops one client
  opening enough of them to exhaust the pool.
- **Backups.** Deliberately: nothing persists beyond a session, so there is nothing
  to back up.
