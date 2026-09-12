# ChessView vision service.
#
# Two stages so the build toolchain does not ship. The runtime image carries
# Python, OpenCV's runtime libraries, onnxruntime and a Stockfish binary -- and
# deliberately not PyTorch, which is a training dependency and would add well over
# a gigabyte to an image whose model is 33 KB.

FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY packages/protocol/ packages/protocol/
COPY services/engine/ services/engine/
COPY services/vision/ services/vision/

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# opencv-python-headless, not opencv-python: the full package pulls in GUI
# libraries (GTK, X11) that a server has no use for and that add ~200 MB.
RUN pip install --no-cache-dir \
        "uvicorn[standard]>=0.30" \
        "opencv-python-headless>=4.10" \
        "onnxruntime>=1.19" \
    && pip install --no-cache-dir \
        ./packages/protocol \
        ./services/engine \
        ./services/vision


FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    CHESSVIEW_STOCKFISH=/usr/games/stockfish

RUN apt-get update && apt-get install -y --no-install-recommends \
        stockfish \
        # OpenCV's headless wheel still needs these two shared libraries present.
        libglib2.0-0 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY models/ /app/models/
ENV CHESSVIEW_MODEL=/app/models/square-classifier.onnx

# Unprivileged: the service accepts arbitrary image data from the internet and
# decodes it with OpenCV, so it should not be running as root when it does.
RUN useradd --system --uid 10001 --no-create-home chessview \
    && chown -R chessview:chessview /app
USER chessview

EXPOSE 8000

# Exercises the engine pool and model load, not just the process being up -- a
# container whose engines failed to spawn is not healthy, however alive it looks.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health | grep -q '"status": *"ok"' || exit 1

# One worker. The engine pool and session state live in process memory, so a second
# worker would hold its own pool and its own sessions, and a reconnecting client
# routed to the wrong one would silently lose its game. Scale by running more
# containers behind the load balancer's session affinity, not more workers here.
CMD ["uvicorn", "chessview_vision.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--timeout-graceful-shutdown", "20"]
