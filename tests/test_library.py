# SPDX-License-Identifier: AGPL-3.0-only
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocus_ai_reader.library import (
    Library,
    LibraryError,
    guess_view,
    normalize_tags,
    summarize,
)
from tests.support import fake_mp4

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


class GuessViewTests(unittest.TestCase):
    def test_known_names(self):
        cases = {
            "PLAX_01.mp4": "PLAX",
            "patient-psax mid.mp4": "PSAX",
            "A4C.mp4": "A4C",
            "apical 4 chamber.mov": "A4C",
            "ap4_clip3.mp4": "A4C",
            "SC4C_final.mp4": "SC4C",
            "subcostal four chamber.mp4": "SC4C",
            "IVC_long.mp4": "IVC",
            "inferior vena cava.mp4": "IVC",
            "clip0001.mp4": "",
            "plaxy.mp4": "",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(guess_view(name), expected)

    def test_path_parts_ignored(self):
        self.assertEqual(guess_view("/some/IVC/folder/clip.mp4"), "")


class TagTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_tags(["#CHF", " Pleural effusion ", "chf", "", "a/b<c>"]),
                         ["chf", "pleural-effusion", "abc"])

    def test_limits(self):
        self.assertEqual(len(normalize_tags([f"t{i}" for i in range(100)])), 30)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = Library(Path(self.tmp.name) / "lib")

    def tearDown(self):
        self.tmp.cleanup()

    def test_archive_roundtrip(self):
        a = self.lib.create_archive("  Morning scans ", "notes here", ["#Test"])
        self.assertEqual(a["label"], "Morning scans")
        self.assertEqual(a["tags"], ["test"])
        clip = self.lib.add_clip(a["id"], "PLAX_1.mp4", io.BytesIO(fake_mp4()))
        self.assertEqual(clip["view"], "PLAX")
        self.assertEqual(clip["kind"], "clip")
        path, _ = self.lib.media_path(a["id"], clip["id"])
        self.assertTrue(path.is_file())

        updated = self.lib.update_clip(a["id"], clip["id"], view="A4C", tags=["RV"], notes="check RV")
        self.assertEqual((updated["view"], updated["tags"], updated["notes"]), ("A4C", ["rv"], "check RV"))
        s = summarize(self.lib.get_archive(a["id"]))
        self.assertEqual(s["views"], ["A4C"])
        self.assertEqual(s["clip_count"], 1)
        self.assertIn("rv", s["tags"])

        self.lib.delete_clip(a["id"], clip["id"])
        self.assertFalse(path.exists())
        self.lib.delete_archive(a["id"])
        with self.assertRaises(LibraryError) as ctx:
            self.lib.get_archive(a["id"])
        self.assertEqual(ctx.exception.status, 404)

    def test_blank_label_gets_default(self):
        self.assertEqual(self.lib.create_archive("   ")["label"], "Untitled archive")

    def test_ids_cannot_escape_the_library(self):
        for bad in ["../etc", "..", "a" * 32 + "/../..", "ABCDEF" * 6, ""]:
            with self.subTest(bad=bad):
                with self.assertRaises(LibraryError):
                    self.lib.get_archive(bad)

    def test_unsupported_type_and_empty_file(self):
        a = self.lib.create_archive("x")
        with self.assertRaises(LibraryError) as ctx:
            self.lib.add_clip(a["id"], "notes.pdf", io.BytesIO(b"abc"))
        self.assertEqual(ctx.exception.status, 415)
        with self.assertRaises(LibraryError):
            self.lib.add_clip(a["id"], "empty.mp4", io.BytesIO(b""))
        self.assertEqual(self.lib.get_archive(a["id"])["clips"], [])
        self.assertEqual(list((self.lib.archive_dir(a["id"]) / "media").iterdir()), [])

    def test_size_limit_removes_partial_file(self):
        a = self.lib.create_archive("x")
        with mock.patch.dict(os.environ, {"POCUS_AI_READER_MAX_FILE_BYTES": "100"}):
            with self.assertRaises(LibraryError) as ctx:
                self.lib.add_clip(a["id"], "big.mp4", io.BytesIO(b"x" * 500))
        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(list((self.lib.archive_dir(a["id"]) / "media").iterdir()), [])

    def test_filename_is_reduced_to_its_basename(self):
        a = self.lib.create_archive("x")
        clip = self.lib.add_clip(a["id"], "../../etc/passwd.mp4", io.BytesIO(fake_mp4(16)))
        self.assertEqual(clip["filename"], "passwd.mp4")

    def test_thumbnails(self):
        a = self.lib.create_archive("x")
        clip = self.lib.add_clip(a["id"], "a.mp4", io.BytesIO(fake_mp4(16)))
        with self.assertRaises(LibraryError):
            self.lib.set_thumbnail(a["id"], clip["id"], b"not a jpeg")
        self.assertIsNone(self.lib.thumb_path(a["id"], clip["id"]))
        self.lib.set_thumbnail(a["id"], clip["id"], JPEG)
        self.assertTrue(self.lib.thumb_path(a["id"], clip["id"]).is_file())
        self.assertTrue(self.lib.get_archive(a["id"])["clips"][0]["has_thumb"])

    def test_still_images_serve_as_their_own_thumbnail(self):
        a = self.lib.create_archive("x")
        clip = self.lib.add_clip(a["id"], "frame.png", io.BytesIO(b"\x89PNG" + b"0" * 20))
        self.assertEqual(clip["kind"], "still")
        self.assertEqual(self.lib.thumb_path(a["id"], clip["id"]).suffix, ".png")

    def test_invalid_view_rejected(self):
        a = self.lib.create_archive("x")
        clip = self.lib.add_clip(a["id"], "a.mp4", io.BytesIO(fake_mp4(16)))
        with self.assertRaises(LibraryError):
            self.lib.update_clip(a["id"], clip["id"], view="A5C")

    def test_unreadable_archive_does_not_break_listing(self):
        good = self.lib.create_archive("good")
        bad = self.lib.create_archive("bad")
        (self.lib.archive_dir(bad["id"]) / "archive.json").write_text("{not json")
        ids = [s["id"] for s in self.lib.list_archives()]
        self.assertEqual(ids, [good["id"]])


if __name__ == "__main__":
    unittest.main()
