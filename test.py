from ultralytics import YOLO
from PIL import Image

model = YOLO(r"C:\Users\mcgra\ChessView\temp_exp1_last.pt")
print("Classes:", model.names)

# Test on a sample image - download any chess board image and put it here
results = model(r"C:\Users\mcgra\ChessView\test.jpg", verbose=True)
for r in results:
    print(f"Detected {len(r.boxes)} objects")
    for box in r.boxes:
        print(f"  {model.names[int(box.cls[0])]} - confidence: {box.conf[0]:.2f}")