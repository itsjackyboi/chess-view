"""
chess_server.py  –  Local YOLO chess detection server
Run this on your PC while developing. Your phone hits it over WiFi.

Usage:
    python chess_server.py --model C:/chess-yolo/chess.pt --port 5050
"""

import argparse
import base64
import io
import sys
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, request
from PIL import Image
from ultralytics import YOLO

app = Flask(__name__)
model: YOLO | None = None


def load_model(model_path: str):
    global model
    print(f"Loading model from {model_path} ...")
    model = YOLO(model_path)
    print("Model ready.")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "model_loaded": model is not None})


@app.route("/detect", methods=["POST"])
def detect():
    if model is None:
        return jsonify({"error": "Model not loaded"}), 503

    data = request.get_json(force=True)
    if not data or "image" not in data:
        return jsonify({"error": "Missing 'image' field (base64)"}), 400

    try:
        # Decode base64 image
        img_bytes = base64.b64decode(data["image"])
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        from PIL import ImageEnhance
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(1.5)
        img_w, img_h = img.size
        img.save(r"C:\Users\mcgra\ChessView\debug_frame.jpg")

        # Run inference
        results = model(img, verbose=True, conf=0.15)[0]

        predictions = []
        for box in results.boxes:
            cls_id    = int(box.cls[0])
            cls_name  = model.names[cls_id]
            conf      = float(box.conf[0])
            x1, y1, x2, y2 = box.xyxy[0].tolist()

            # Normalize to 0–1
            cx = ((x1 + x2) / 2) / img_w
            cy = ((y1 + y2) / 2) / img_h
            w  = (x2 - x1) / img_w
            h  = (y2 - y1) / img_h

            predictions.append({
                "class":      cls_name,
                "confidence": round(conf, 3),
                "x":          round(cx, 4),
                "y":          round(cy, 4),
                "width":      round(w,  4),
                "height":     round(h,  4),
            })

        return jsonify({
            "predictions": predictions,
            "image": {"width": img_w, "height": img_h},
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Path to .pt weights file")
    parser.add_argument("--port",  type=int, default=5050)
    args = parser.parse_args()

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"ERROR: Model not found at {model_path}", file=sys.stderr)
        sys.exit(1)

    load_model(str(model_path))

    print(f"\n Server running on http://0.0.0.0:{args.port}")
    print("  Find your local IP with: ipconfig (Windows)")
    print("  Then set SERVER_URL in ChessScannerScreen.tsx to http://<your-ip>:5050\n")

    app.run(host="0.0.0.0", port=args.port, debug=False)
