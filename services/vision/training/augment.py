"""Batch-level augmentation and normalisation, as torch ops.

Doing this per crop in a Dataset costs more than the forward and backward pass
combined -- it is thousands of small NumPy allocations per batch. Vectorised over
the whole batch it is a handful of tensor ops and effectively free.

Only photometric augmentation lives here. Geometry (perspective, corner jitter,
piece placement) is baked into the rendered cache, because re-warping on the fly
would put OpenCV back in the training loop.
"""

from __future__ import annotations

import torch


def augment(batch: torch.Tensor, generator: torch.Generator | None = None) -> torch.Tensor:
    """Randomise brightness, contrast and noise. Input and output are float [0,255].

    Ranges are wide on purpose: a model trained on synthetic renders has no
    exposure to real camera behaviour, and aggressive photometric variation is the
    cheapest way to stop it keying on rendered pixel values it will never see again.
    """
    count = batch.shape[0]
    shape = (count, 1, 1, 1)
    device = batch.device

    # torch.empty takes no generator, so the shape is allocated first and filled
    # through the in-place samplers, which do.
    contrast = torch.empty(shape, device=device).uniform_(0.75, 1.25, generator=generator)
    brightness = torch.empty(shape, device=device).uniform_(-25.0, 25.0, generator=generator)
    noise_scale = torch.empty(shape, device=device).uniform_(0.0, 7.0, generator=generator)
    noise = torch.empty(batch.shape, device=device).normal_(0.0, 1.0, generator=generator)

    out = batch * contrast + brightness
    out += noise * noise_scale
    return out.clamp_(0.0, 255.0)


def normalise(batch: torch.Tensor) -> torch.Tensor:
    """Per-crop standardisation, matching classifier.preprocess exactly.

    Any drift between this and the inference path shows up as a model that scores
    well offline and badly in the service, which is a miserable thing to debug --
    so both compute mean and standard deviation over each crop's own pixels.
    """
    scaled = batch / 255.0
    mean = scaled.mean(dim=(1, 2, 3), keepdim=True)
    std = scaled.std(dim=(1, 2, 3), keepdim=True).clamp_min(1e-5)
    return (scaled - mean) / std
