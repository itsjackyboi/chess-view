"""Guarding the image decode path.

The vision service accepts arbitrary bytes from the internet and hands them to
OpenCV. That is the largest untrusted surface in the system, and the cheapest
attack against it is a decompression bomb: a few kilobytes of JPEG that decode into
a gigapixel image and exhaust the container's memory before any of our code runs.

The byte-length cap in the protocol does not help here -- the whole point of the
attack is that the compressed form is small. So dimensions are read from the file
header *before* decoding, and oversized images are refused without ever allocating
the pixel buffer.

Only JPEG and PNG are probed, because those are the only formats the client sends.
Anything else is refused rather than passed through on the assumption it is
harmless.
"""

from __future__ import annotations

import logging
import struct

log = logging.getLogger(__name__)

# A rectified board is 320-512px square and a full camera frame is at most a few
# thousand pixels on a side. 8192 is far above anything legitimate and far below
# what it takes to hurt us: 8192x8192x3 is ~200 MB, which is survivable, and
# anything larger is refused.
MAX_DIMENSION = 8192

# Total pixels, which catches a long thin image that passes the per-side check.
MAX_PIXELS = 40_000_000


class UnsafeImage(ValueError):
    """The image is malformed, an unexpected format, or too large to decode."""


def probe_dimensions(data: bytes) -> tuple[int, int]:
    """Read (width, height) from a JPEG or PNG header without decoding it.

    Raises UnsafeImage if the format is unrecognised or the header is malformed,
    rather than guessing -- an unparseable header is itself a reason not to hand
    the bytes to a decoder.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png_dimensions(data)
    if data[:2] == b"\xff\xd8":
        return _jpeg_dimensions(data)
    raise UnsafeImage("not a JPEG or PNG")


def _png_dimensions(data: bytes) -> tuple[int, int]:
    # IHDR is always the first chunk: 8 signature + 4 length + 4 type, then w/h.
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise UnsafeImage("malformed PNG header")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Walk JPEG segments to the frame header.

    Dimensions live in a SOFn marker, which can sit after any number of
    variable-length metadata segments, so the segment chain has to be walked rather
    than read at a fixed offset.
    """
    index = 2
    length = len(data)

    while index < length - 1:
        if data[index] != 0xFF:
            raise UnsafeImage("malformed JPEG: expected a marker")

        marker = data[index + 1]
        index += 2

        # Standalone markers carry no payload.
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        # Start of scan: pixel data begins and no SOF was found.
        if marker == 0xDA:
            break

        if index + 2 > length:
            raise UnsafeImage("truncated JPEG segment")
        segment_length = struct.unpack(">H", data[index:index + 2])[0]
        if segment_length < 2:
            raise UnsafeImage("malformed JPEG segment length")

        # SOF0-SOF15, excluding DHT (0xC4), JPG (0xC8) and DAC (0xCC), which share
        # the range but are not frame headers.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if index + 7 > length:
                raise UnsafeImage("truncated JPEG frame header")
            height, width = struct.unpack(">HH", data[index + 3:index + 7])
            return int(width), int(height)

        index += segment_length

    raise UnsafeImage("no frame header found in JPEG")


def check_safe_to_decode(data: bytes) -> tuple[int, int]:
    """Refuse anything we should not hand to a decoder. Returns (width, height)."""
    if not data:
        raise UnsafeImage("empty image")

    width, height = probe_dimensions(data)

    if width <= 0 or height <= 0:
        raise UnsafeImage(f"nonsensical dimensions {width}x{height}")
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise UnsafeImage(f"image is {width}x{height}, limit is {MAX_DIMENSION} per side")
    if width * height > MAX_PIXELS:
        # Catches a long thin image that slips past the per-side check.
        raise UnsafeImage(f"image is {width * height} pixels, limit is {MAX_PIXELS}")

    return width, height
