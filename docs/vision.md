# The vision pipeline

How a camera frame becomes a position, what it measures, and — importantly — what
those measurements do not tell you.

## Shape

Two stages, and the order is the whole design:

1. **Localise and flatten.** Find the board's four corners, compute a homography,
   warp to a 512×512 square.
2. **Classify each square.** Cut the flat board into 64 crops and run a 210k
   parameter CNN over them, 13 classes each.

Rectifying first is what makes stage two small. After the warp there is no
perspective, scale or rotation left to learn, so the model only has to tell thirteen
aligned silhouettes apart. A detector working on the raw frame would have to learn
all of that invariance itself, and would need to be far larger to do it.

Crops extend one square *upward* past their own square. Pieces are tall, so a crop
bounded by its own square shows a pawn's collar and a queen's collar and little else
to separate them.

## Measured accuracy

`services/vision/evaluate.py`, 150 held-out synthetic boards, seeds disjoint from
training and validation:

| | calibrated corners | detected corners |
|---|---|---|
| Per-square accuracy | 99.990% | 95.32% |
| **Board-level accuracy** | **99.33%** | **79.20%** |
| Latency (median) | 49 ms | 43 ms |
| Confidence when the board was right | 0.916 | 0.821 |
| Confidence when the board was wrong | 0.429 | 0.266 |

Three things worth drawing out.

**Calibration is worth 20 points of board accuracy.** 99.3% against 79.2% is the
entire argument for making the user drag four corners once. Automatic detection lands
about 0.2 squares off with the pattern detector and nearer 2 with the contour
fallback, and a crop shifted by a fraction of a square cuts pieces in half.

**The confidence gate separates right from wrong.** 0.916 against 0.429 is a wide gap,
and the 0.6 threshold sits cleanly in it. This is what makes "never show analysis for
a position that might be wrong" an implementable rule rather than an aspiration — the
system can actually tell the difference. Confidence is reported as the board's
*weakest* square precisely so a single bad square cannot hide behind 63 good ones.

**Per-square accuracy is a misleading number on its own.** 99.99% per square reads
like a solved problem; it is 99.3% of boards. At 99.5% per square — which still sounds
excellent — barely seven boards in ten would be right. Always quote both.

## What these numbers are not

**They are measured on synthetic renders, and they will not hold on photographs.**

The training and test sets both come from the same renderer. It randomises board
colours, piece palettes, lighting gradients, blur, noise and perspective, and the test
seeds are disjoint — so the figures above are a real held-out measurement, not
memorisation. But rendered pieces are cleaner, better lit and more consistent than
wood on a kitchen table under a lamp, and a model trained only on them will do
markedly worse on real images.

For scale: published systems trained and tested on photographs report around
[93% board-level accuracy][wolflein], and a 2025 system reports [29.9% of boards
yielding a perfect FEN][cvchess]. Our 99.3% is not comparable to those, and quoting it
as though it were would be dishonest.

Treat it as a regression check on the pipeline. Closing the gap needs photographs of
real boards — the outstanding M2 dataset work.

## Training

```bash
PYTHONPATH=services/vision .venv/bin/python services/vision/training/train.py \
    --train-boards 1500 --epochs 14 --out models/square-classifier.onnx
```

Data is rendered once and cached, then photometrically augmented per epoch. Rendering
costs ~230 ms per board against a few milliseconds for the forward and backward pass
over its 64 crops, so regenerating every epoch would spend almost the whole run inside
OpenCV.

Positions come from random legal play rather than scattering pieces onto squares. That
keeps material counts, pawn structures, king positions and — importantly — the
proportion of empty squares realistic. Roughly 70% of a real board is empty, and a
uniform scatter would badly misrepresent that.

Corners are jittered by up to 0.18 squares during training. Calibration is never
pixel-exact and optical-flow tracking drifts, so a model trained on perfect alignment
falls apart in the field.

Inference is **onnxruntime only**. PyTorch is a training dependency and stays out of
the deployed image, which would otherwise grow by over a gigabyte to serve a 35 KB
model.

## Failure modes and what happens

| What goes wrong | What the system does |
|---|---|
| No board in frame | `no_board` — "point the camera at the board" |
| Board found, pieces unreadable | Weakest-square confidence drops below 0.6, reported as `low_light` |
| Misaligned corners | Same gate catches it; measured 0.43 confidence on a half-square shift |
| Hand over the board | Every frame differs, so the stability gate never reaches agreement |
| A move was missed | No legal move explains the board; after 8 stable frames it resynchronises at reduced confidence |
| Board is not a legal position | Refused outright; the previous position stands |

None of these guess. That is the point.

[wolflein]: https://arxiv.org/pdf/2104.14963v1
[cvchess]: https://arxiv.org/pdf/2511.11522
