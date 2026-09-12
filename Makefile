# ChessView developer entry points.
# Everything runs through the repo virtualenv so no global installs are needed.

PY := .venv/bin/python
PIP := .venv/bin/pip
# Modules under services/vision import `training.*`, which is not on the path of an
# installed package.
VISION := PYTHONPATH=services/vision

.PHONY: help setup protocol protocol-check test test-protocol test-engine test-vision test-app typecheck run-vision bench evaluate train loadtest demo clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv, install Python packages and node workspaces
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e packages/protocol -e services/engine -e services/vision
	$(PIP) install pytest pytest-asyncio
	npm install

protocol: ## Regenerate schema.json and the TypeScript types from the pydantic models
	npm run protocol:generate

protocol-check: ## Fail if the generated protocol files are stale (CI gate)
	npm run protocol:check

test: ## Run the full Python test suite
	$(PY) -m pytest packages services -q

test-protocol:
	$(PY) -m pytest packages/protocol/tests -q

test-engine:
	$(PY) -m pytest services/engine/tests -q

test-vision:
	$(PY) -m pytest services/vision/tests -q

test-app: ## Run the client's unit tests
	npm test --workspace @chessview/app

typecheck: ## Typecheck the client and the protocol package
	npm run typecheck

run-vision: ## Start the vision service (the client's WebSocket endpoint)
	$(PY) -m uvicorn chessview_vision.app:app --host 0.0.0.0 --port 8000 --reload

bench: ## Measure engine latency against the architecture's targets
	$(PY) services/engine/bench.py

evaluate: ## Measure vision accuracy, per-square and board-level
	$(VISION) $(PY) services/vision/evaluate.py --model models/square-classifier.onnx

train: ## Retrain the square classifier (~30 min on 4 CPU cores)
	$(VISION) $(PY) services/vision/training/train.py \
		--train-boards 1500 --epochs 14 --out models/square-classifier.onnx

loadtest: ## Drive concurrent sessions at a running service (needs make run-vision)
	$(VISION) $(PY) services/vision/loadtest.py

demo: ## Run the whole pipeline over one image: make demo IMAGE=board.jpg
	@test -n "$(IMAGE)" || (echo "usage: make demo IMAGE=path/to/board.jpg" && exit 1)
	$(VISION) $(PY) services/vision/demo.py "$(IMAGE)" $(ARGS)

clean:
	rm -rf .venv node_modules .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
