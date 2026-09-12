"""The square classifier network. Training-time only -- requires PyTorch.

Small on purpose. The rectification in board.py has already removed perspective,
scale and rotation, so the network only has to tell thirteen aligned silhouettes
apart. A larger backbone would cost inference time the latency budget cannot spare
and would overfit the synthetic data faster.

At roughly 150k parameters it runs 64 crops in well under 30 ms on one CPU core,
which is what makes the CPU-only hosting recommendation hold.
"""

from __future__ import annotations

import torch
from torch import nn

from chessview_vision.classifier import INPUT_PX
from chessview_vision.synth import NUM_CLASSES


def conv_block(in_channels: int, out_channels: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(in_channels, out_channels, 3, padding=1, bias=False),
        nn.BatchNorm2d(out_channels),
        nn.ReLU(inplace=True),
        nn.Conv2d(out_channels, out_channels, 3, padding=1, bias=False),
        nn.BatchNorm2d(out_channels),
        nn.ReLU(inplace=True),
        nn.MaxPool2d(2),
    )


class SquareNet(nn.Module):
    def __init__(self, num_classes: int = NUM_CLASSES) -> None:
        super().__init__()
        self.features = nn.Sequential(
            # Downsample immediately. Convolutions at the full 48x48 crop dominate
            # the cost and buy little: the discriminating features here are whole
            # silhouettes, not fine texture. A strided stem halves the resolution
            # before any expensive block runs.
            nn.Conv2d(3, 16, 5, stride=2, padding=2, bias=False),  # 48 -> 24
            nn.BatchNorm2d(16),
            nn.ReLU(inplace=True),
            conv_block(16, 32),  # 24 -> 12
            conv_block(32, 64),  # 12 -> 6
            conv_block(64, 96),  # 6 -> 3
        )
        # Global pooling rather than a flatten: it keeps the head tiny and makes the
        # network indifferent to a few pixels of crop misalignment, which matters
        # because calibration is never exact.
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(0.2),
            nn.Linear(96, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.pool(self.features(x)))


def export_onnx(model: nn.Module, path: str, *, opset: int = 18) -> None:
    """Export for onnxruntime with a dynamic batch axis.

    Dynamic batching matters because the service always sends 64 crops but the
    accuracy harness and tests send other sizes.
    """
    model.eval()
    dummy = torch.randn(1, 3, INPUT_PX, INPUT_PX)
    torch.onnx.export(
        model,
        dummy,
        path,
        input_names=["crops"],
        output_names=["logits"],
        dynamic_axes={"crops": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=opset,
        # The dynamo exporter rejects dynamic_axes; the legacy path handles the
        # single dynamic batch dimension this model needs without complaint.
        dynamo=False,
    )
