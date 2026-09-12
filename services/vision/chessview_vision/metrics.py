"""Service metrics.

Deliberately a handful of counters rather than a metrics library. What needs
watching here is specific and small, and the numbers that matter are not the usual
ones -- request counts say very little about whether this service is doing its job.

The ones that do:

* **Detection confidence** and how often the tracker says "unclear". These are the
  early warning that the model is meeting conditions it was not trained for, which
  for a synthetic-trained model is the expected failure.
* **Resyncs.** A rising rate means the tracker is repeatedly losing the thread of
  the game, which a user experiences as the position going wrong.
* **Frames dropped** at the rate limiter, distinguishing a busy service from a
  misbehaving client.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class Metrics:
    started_at: float = field(default_factory=time.monotonic)

    sessions_started: int = 0
    sessions_refused_capacity: int = 0
    sessions_refused_rate_limit: int = 0
    sessions_resumed: int = 0

    frames_received: int = 0
    frames_rate_limited: int = 0
    frames_undecodable: int = 0

    positions_committed: int = 0
    positions_resynced: int = 0
    observations_unclear: int = 0
    observations_low_confidence: int = 0

    evaluations_sent: int = 0

    # A rolling sample rather than every value: enough to see the distribution
    # move without unbounded memory.
    _confidence: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    _lock: Lock = field(default_factory=Lock)

    def record_confidence(self, value: float) -> None:
        with self._lock:
            self._confidence.append(value)

    def snapshot(self) -> dict:
        with self._lock:
            confidence = list(self._confidence)

        ordered = sorted(confidence)
        def percentile(q: float) -> float | None:
            if not ordered:
                return None
            return round(ordered[min(len(ordered) - 1, int(len(ordered) * q))], 4)

        committed = max(self.positions_committed, 1)
        return {
            "uptimeSeconds": round(time.monotonic() - self.started_at, 1),
            "sessions": {
                "started": self.sessions_started,
                "resumed": self.sessions_resumed,
                "refusedAtCapacity": self.sessions_refused_capacity,
                "refusedRateLimited": self.sessions_refused_rate_limit,
            },
            "frames": {
                "received": self.frames_received,
                "rateLimited": self.frames_rate_limited,
                "undecodable": self.frames_undecodable,
            },
            "detection": {
                "positionsCommitted": self.positions_committed,
                "positionsResynced": self.positions_resynced,
                # The rate, not the count: a rising share is the signal that the
                # tracker is losing the game, and the raw count only ever goes up.
                "resyncRate": round(self.positions_resynced / committed, 4),
                "unclearObservations": self.observations_unclear,
                "lowConfidenceObservations": self.observations_low_confidence,
                "confidenceP05": percentile(0.05),
                "confidenceP50": percentile(0.50),
                "samples": len(confidence),
            },
            "engine": {"evaluationsSent": self.evaluations_sent},
        }


METRICS = Metrics()
