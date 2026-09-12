"""Guarding the image decode path.

This is the largest untrusted surface in the system: arbitrary bytes from the
internet handed to OpenCV. The cheapest attack against it is a decompression bomb,
which the protocol's byte-length cap cannot catch -- being small on the wire is the
whole point of the attack.
"""

from __future__ import annotations

import struct

import cv2
import numpy as np
import pytest

from chessview_vision.imagesafety import (
    MAX_DIMENSION,
    MAX_PIXELS,
    UnsafeImage,
    check_safe_to_decode,
    probe_dimensions,
)


def encode(width: int, height: int, suffix: str = ".jpg") -> bytes:
    ok, buffer = cv2.imencode(suffix, np.zeros((height, width, 3), np.uint8))
    assert ok
    return buffer.tobytes()


class TestProbe:
    @pytest.mark.parametrize("suffix", [".jpg", ".png"])
    def test_reads_dimensions_without_decoding(self, suffix: str):
        assert probe_dimensions(encode(640, 480, suffix)) == (640, 480)

    @pytest.mark.parametrize("size", [(1, 1), (320, 320), (1920, 1080), (100, 3000)])
    def test_handles_a_range_of_shapes(self, size: tuple[int, int]):
        assert probe_dimensions(encode(*size)) == size

    def test_walks_past_metadata_segments_to_the_frame_header(self):
        """Dimensions sit after any number of variable-length segments.

        A reader that assumed a fixed offset would work on plain encoder output and
        fail on anything carrying EXIF, which is most photographs.
        """
        jpeg = encode(640, 480)
        # Splice a fake APP1 (EXIF) segment in after SOI.
        payload = b"\x00" * 200
        segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
        assert probe_dimensions(jpeg[:2] + segment + jpeg[2:]) == (640, 480)

    @pytest.mark.parametrize(
        "data, match",
        [
            (b"GIF89a" + b"\x00" * 40, "not a JPEG or PNG"),
            (b"\x89PNG\r\n\x1a\n" + b"\x00" * 4, "malformed PNG"),
            (b"\xff\xd8\xff", "no frame header"),
            (b"\xff\xd8" + b"\x00\x00", "expected a marker"),
        ],
    )
    def test_refuses_what_it_cannot_parse(self, data: bytes, match: str):
        # An unparseable header is itself a reason not to hand the bytes to a
        # decoder, so this must raise rather than guess.
        with pytest.raises(UnsafeImage, match=match):
            probe_dimensions(data)


class TestSafetyCheck:
    def test_accepts_a_normal_frame(self):
        assert check_safe_to_decode(encode(320, 320)) == (320, 320)

    def test_refuses_an_empty_payload(self):
        with pytest.raises(UnsafeImage, match="empty"):
            check_safe_to_decode(b"")

    def test_refuses_an_image_too_large_on_one_side(self):
        # Built by hand: actually encoding one of these is the attack.
        header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
        bomb = header + struct.pack(">II", MAX_DIMENSION + 1, 100) + b"\x00" * 8
        with pytest.raises(UnsafeImage, match="limit is"):
            check_safe_to_decode(bomb)

    def test_refuses_a_long_thin_image_that_passes_the_per_side_check(self):
        """The case a per-side limit alone misses.

        8000 x 8000 is under the per-side cap in both directions but is 64 megapixels.
        """
        header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
        wide = header + struct.pack(">II", 8000, 8000) + b"\x00" * 8
        with pytest.raises(UnsafeImage, match="pixels"):
            check_safe_to_decode(wide)
        assert 8000 * 8000 > MAX_PIXELS

    def test_refuses_nonsensical_dimensions(self):
        header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
        zero = header + struct.pack(">II", 0, 0) + b"\x00" * 8
        with pytest.raises(UnsafeImage, match="nonsensical"):
            check_safe_to_decode(zero)

    def test_a_bomb_is_refused_without_allocating_its_pixels(self):
        """The property that matters: refusal happens before decode.

        A declared 20000x20000 image would be 1.2 GB decoded. The check reads the
        header only, so nothing is allocated.
        """
        header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
        bomb = header + struct.pack(">II", 20_000, 20_000) + b"\x00" * 8
        assert len(bomb) < 100, "the payload itself is tiny -- that is the attack"
        with pytest.raises(UnsafeImage):
            check_safe_to_decode(bomb)
