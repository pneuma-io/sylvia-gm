"""Ordinary PNG background hints must not prevent retaining exact character reference bytes."""

import struct
import zlib

import pytest

from sylvia_gm.application.image_hooks import ImageData, ImageHookError


def background_png(color, background, *, after_data=False, duplicate=False, late_palette=False):
    """Make a minimal one-pixel reference with a standard bKGD hint and valid chunk checksums."""

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, color, 0, 0, 0))
    palette = chunk(b"PLTE", b"\0\0\0\xff\xff\xff") if color == 3 or late_palette else b""
    hint = chunk(b"bKGD", background)
    pixels = chunk(b"IDAT", zlib.compress(b"\0" * (1 + {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color])))
    return (
        b"\x89PNG\r\n\x1a\n" + header
        + (b"" if late_palette else palette)
        + (pixels if after_data else b"") + hint + (hint if duplicate else b"")
        + (palette if late_palette else b"")
        + (b"" if after_data else pixels) + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize(
    ("color", "background"),
    [(0, b"\xff\xff"), (2, b"\xff" * 6), (3, b"\x01"),
     (4, b"\xff\xff"), (6, b"\xff" * 6)],
)
def test_valid_background_hints_keep_exact_reference_bytes(color, background):
    raw = background_png(color, background)
    image = ImageData(raw)
    assert image.dimensions == (1, 1)
    assert image.data == raw


@pytest.mark.parametrize(
    ("color", "background", "options"),
    [(0, b"\x00", {}), (2, b"\x00\x00", {}), (3, b"\x02", {}),
     (3, b"\x00\x01", {}), (2, b"\0" * 6, {"after_data": True}),
     (2, b"\0" * 6, {"duplicate": True}), (3, b"\x00", {"late_palette": True})],
)
def test_malformed_or_misordered_background_hints_are_refused(color, background, options):
    with pytest.raises(ImageHookError, match="invalid_image"):
        _ = ImageData(background_png(color, background, **options)).dimensions
