# SPDX-License-Identifier: AGPL-3.0-only
"""AVI files are converted to H.264 MP4 on upload."""
import io
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from pocus_ai_reader.library import Library, LibraryError


def make_avi(path: Path, codec: str, frames: int = 12, size=(64, 48), rate=15, pix_fmt="yuv420p") -> None:
    with av.open(str(path), "w", format="avi") as out:
        stream = out.add_stream(codec, rate=rate)
        stream.width, stream.height, stream.pix_fmt = size[0], size[1], pix_fmt
        for i in range(frames):
            img = np.full((size[1], size[0], 3), (i * 20) % 255, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(img, format="rgb24").reformat(format=pix_fmt)
            frame.pts = i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


class AviUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = Library(Path(self.tmp.name) / "lib")
        self.aid = self.lib.create_archive("t", "", None)["id"]

    def upload(self, name: str, data: bytes):
        return self.lib.add_clip(self.aid, name, io.BytesIO(data))

    def test_codecs_convert_to_playable_mp4(self):
        for codec, pix_fmt in (("mpeg4", "yuv420p"), ("mjpeg", "yuvj420p"), ("rawvideo", "yuv420p")):
            with self.subTest(codec=codec):
                src = Path(self.tmp.name) / f"{codec}.avi"
                make_avi(src, codec, pix_fmt=pix_fmt)
                clip = self.upload(f"PLAX_{codec}.avi", src.read_bytes())
                self.assertEqual(clip["ext"], ".mp4")
                self.assertEqual(clip["kind"], "clip")
                self.assertEqual(clip["filename"], f"PLAX_{codec}.avi")
                self.assertEqual(clip["view"], "PLAX")
                self.assertTrue(clip["has_thumb"])
                path, _ = self.lib.media_path(self.aid, clip["id"])
                with av.open(str(path)) as out:
                    stream = out.streams.video[0]
                    self.assertEqual(stream.codec_context.name, "h264")
                    self.assertEqual(stream.frames, 12)
                    self.assertEqual(stream.average_rate, Fraction(15))
                thumb = self.lib.thumb_path(self.aid, clip["id"])
                self.assertTrue(thumb.read_bytes().startswith(b"\xff\xd8\xff"))

    def test_odd_dimensions_are_made_even(self):
        src = Path(self.tmp.name) / "odd.avi"
        make_avi(src, "mjpeg", size=(63, 47), pix_fmt="yuvj420p")
        clip = self.upload("odd.avi", src.read_bytes())
        path, _ = self.lib.media_path(self.aid, clip["id"])
        with av.open(str(path)) as out:
            self.assertEqual((out.streams.video[0].width, out.streams.video[0].height), (62, 46))

    def test_no_leftover_upload_files(self):
        src = Path(self.tmp.name) / "a.avi"
        make_avi(src, "mpeg4")
        self.upload("a.avi", src.read_bytes())
        media = list((self.lib.archive_dir(self.aid) / "media").iterdir())
        self.assertEqual([p.suffix for p in media], [".mp4"])

    def test_garbage_avi_is_rejected_cleanly(self):
        with self.assertRaises(LibraryError) as ctx:
            self.upload("bad.avi", b"RIFF" + b"\x00" * 200)
        self.assertEqual(ctx.exception.status, 415)
        self.assertEqual(list((self.lib.archive_dir(self.aid) / "media").iterdir()), [])
        self.assertEqual(self.lib.get_archive(self.aid)["clips"], [])


if __name__ == "__main__":
    unittest.main()
