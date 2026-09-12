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

The image bakes in `models/square-classifier.onnx` (826 KB). It is committed to the
repo, so a clean checkout builds without training. To retrain:

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

> **The image has not been built yet.** The dependency set is verified — the three
> packages install into a clean environment with only `uvicorn`,
> `opencv-python-headless` and `onnxruntime`, the model loads, and PyTorch is
> correctly absent. But no Docker daemon was available where this was written, so
> the `docker build` itself is unexercised. Expect to fix something on the first run.

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

## Monitoring

`/health` is a liveness check. `/metrics` is the one worth watching, and Caddy only
serves it to private addresses — it describes usage patterns and how well the model
is coping, which is not something to publish.

The useful numbers are not the usual ones. Request counts say little here; these do:

| Field | Why it matters |
|---|---|
| `detection.confidenceP05` | The weak tail. Falling means the model is meeting conditions it was not trained for — the expected failure for a synthetic-trained model. |
| `detection.resyncRate` | Share of committed positions that came from resynchronising rather than a matched move. Rising means the tracker keeps losing the game, which users experience as the position going wrong. |
| `detection.unclearObservations` | Growing without a matching rise in commits means detection is degrading rather than the board being busy. |
| `sessions.refusedAtCapacity` | The engine pool is full. Add capacity. |
| `frames.rateLimited` | Distinguishes a busy service from a misbehaving client. |

## Deploying a new version

The service drains on shutdown. On SIGTERM it stops accepting new sessions, reports
`"status": "draining"` on `/health`, and gives live sessions up to ten seconds to
finish before stopping the engines.

`draining` is deliberately distinct from `at_capacity`: capacity is temporary and the
instance still wants traffic afterwards, whereas a draining instance is going away
and should leave the load balancer's rotation. Point your health check at that field.

Without draining, a deploy tears engines out from under active sessions. The client
sees an unexplained drop — indistinguishable from a network failure — so it
reconnects, to a server that is still going down. Clients refused during a drain get
a `fatal` error instead, which stops them retrying that instance.

`--timeout-graceful-shutdown 20` in the Dockerfile gives uvicorn room to run the
drain; keep any orchestrator's termination grace period above it.

## Rate limiting

Sessions are anonymous, so the only thing between one client and the whole engine
pool is the limiter. Two separate limits:

- **Three concurrent sessions per source address.** An engine serves one session at a
  time, so this is the per-client share of the pool. The address is an imperfect
  identity — shared NAT groups strangers together — which is why the cap is a handful
  rather than one.
- **12 frames per second per session**, with a burst allowance. The client gates
  itself to 5 fps, but a client is not something the server may rely on. Over-rate
  frames are dropped rather than closing the connection: the usual cause is a
  misbehaving motion gate, and killing the session would turn a minor client bug
  into a broken app.

Both are per-process and in-memory, matching how sessions are held. Across several
containers each enforces its own share, so the effective limit scales with the fleet
— fine while the fleet is small, worth a shared store before it is not.

## What is not here yet

- **Backups.** Deliberately: nothing persists beyond a session, so there is nothing
  to back up.
- **A real identity for rate limiting.** Source address is what anonymous sessions
  allow. Anything better needs something the v1 scope explicitly excludes.
