# SPDX-License-Identifier: AGPL-3.0-only
"""Convert files the app window cannot play (AVI) into MP4 (H.264) with PyAV.

The conversion is near-lossless (CRF 12) and drops audio, which cardiac clips do not use.
Only the converted MP4 is kept in the library.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

CRF = "12"
THUMB_WIDTH = 320


class ConversionError(Exception):
    """The file could not be read or re-encoded."""


def _first_frame_jpeg(frame) -> bytes:
    import av

    width = THUMB_WIDTH
    height = max(2, round(frame.height * width / frame.width / 2) * 2)
    small = frame.reformat(width=width, height=height, format="yuvj420p")
    enc = av.CodecContext.create("mjpeg", "w")
    enc.width, enc.height, enc.pix_fmt = width, height, "yuvj420p"
    enc.time_base = Fraction(1, 1)
    enc.options = {"qscale": "3"}
    small.pts = 0
    packets = list(enc.encode(small)) + list(enc.encode(None))
    return b"".join(bytes(p) for p in packets)


def avi_to_mp4(src: Path, dest: Path) -> bytes:
    """Write ``dest`` as H.264 MP4 and return a JPEG of the first frame."""
    try:
        import av
    except ImportError as err:  # pragma: no cover - PyAV ships with the app
        raise ConversionError("AVI support is not installed.") from err

    try:
        with av.open(str(src)) as inp:
            if not inp.streams.video:
                raise ConversionError("This file has no video.")
            stream = inp.streams.video[0]
            rate = stream.average_rate or stream.guessed_rate or Fraction(30)
            width, height = stream.codec_context.width // 2 * 2, stream.codec_context.height // 2 * 2
            if width < 2 or height < 2:
                raise ConversionError("This file has no readable picture.")

            thumb = b""
            with av.open(str(dest), "w", format="mp4", options={"movflags": "+faststart"}) as out:
                vout = out.add_stream("libx264", rate=rate)
                vout.width, vout.height, vout.pix_fmt = width, height, "yuv420p"
                vout.options = {"crf": CRF, "preset": "medium"}
                count = 0
                for frame in inp.decode(stream):
                    if not thumb:
                        thumb = _first_frame_jpeg(frame)
                    frame = frame.reformat(width=width, height=height, format="yuv420p")
                    frame.pts = count
                    frame.time_base = 1 / Fraction(rate)
                    count += 1
                    for packet in vout.encode(frame):
                        out.mux(packet)
                if count == 0:
                    raise ConversionError("This file has no readable frames.")
                for packet in vout.encode(None):
                    out.mux(packet)
        return thumb
    except ConversionError:
        dest.unlink(missing_ok=True)
        raise
    except Exception as err:  # PyAV raises many error types for damaged or unusual files
        dest.unlink(missing_ok=True)
        raise ConversionError("This AVI could not be read. Try exporting it again from the scanner.") from err
