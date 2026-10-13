# SPDX-License-Identifier: AGPL-3.0-only
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from pocus_ai_reader.library import Library, LibraryError
from pocus_ai_reader.transfer import export_zip, import_zip
from tests.support import fake_mp4

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
MANIFEST = json.dumps({"format": "pocus-ai-reader-library", "version": 1})


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = Library(self.root / "src")
        self.dst = Library(self.root / "dst")

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self):
        a = self.src.create_archive("Teaching set", "notes", ["demo"])
        c1 = self.src.add_clip(a["id"], "PLAX.mp4", io.BytesIO(fake_mp4(300)))
        c2 = self.src.add_clip(a["id"], "A4C.mp4", io.BytesIO(fake_mp4(300)))
        self.src.update_clip(a["id"], c1["id"], tags=["rv"], notes="dilated?")
        self.src.set_thumbnail(a["id"], c1["id"], JPEG)
        return a, c1, c2

    def test_roundtrip_keeps_everything(self):
        a, c1, _ = self.seed()
        zip_path = self.root / "out.zip"
        counts = export_zip(self.src, zip_path)
        self.assertEqual(counts, {"archives": 1, "clips": 2})

        result = import_zip(self.dst, zip_path)
        self.assertEqual((result["clips"], result["skipped_clips"]), (2, 0))
        self.assertEqual(result["archives"], [a["id"]])  # no conflict, so the id is kept

        got = self.dst.get_archive(a["id"])
        self.assertEqual((got["label"], got["notes"], got["tags"]), ("Teaching set", "notes", ["demo"]))
        first = got["clips"][0]
        self.assertEqual((first["view"], first["tags"], first["notes"], first["has_thumb"]),
                         ("PLAX", ["rv"], "dilated?", True))
        original, _ = self.src.media_path(a["id"], c1["id"])
        copied, _ = self.dst.media_path(a["id"], c1["id"])
        self.assertEqual(original.read_bytes(), copied.read_bytes())

    def test_importing_twice_creates_a_separate_copy(self):
        self.seed()
        zip_path = self.root / "out.zip"
        export_zip(self.src, zip_path)
        first = import_zip(self.dst, zip_path)["archives"][0]
        second = import_zip(self.dst, zip_path)["archives"][0]
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.dst.list_archives()), 2)

    def test_export_selected_archive_only(self):
        a, *_ = self.seed()
        b = self.src.create_archive("Other")
        self.src.add_clip(b["id"], "x.mp4", io.BytesIO(fake_mp4(10)))
        zip_path = self.root / "one.zip"
        self.assertEqual(export_zip(self.src, zip_path, [b["id"]])["archives"], 1)
        self.assertEqual(import_zip(self.dst, zip_path)["archives"], [b["id"]])
        with self.assertRaises(LibraryError):
            self.dst.get_archive(a["id"])

    def test_hostile_zip_cannot_write_outside_the_library(self):
        aid = "a" * 32
        cid = "b" * 32
        record = {"label": "Evil <script>", "clips": [
            {"id": cid, "ext": ".mp4", "filename": "../../../evil.mp4", "view": "NOPE",
             "tags": ["../x"], "notes": "n", "results": "not-a-dict"},
            {"id": "../../escape", "ext": ".mp4"},
            {"id": cid, "ext": ".exe"},
        ]}
        zip_path = self.root / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", MANIFEST)
            zf.writestr(f"archives/{aid}/archive.json", json.dumps(record))
            zf.writestr(f"archives/{aid}/media/{cid}.mp4", fake_mp4(50))
            zf.writestr("../../outside.txt", "pwned")
            zf.writestr(f"archives/{aid}/../../outside2.txt", "pwned")
            zf.writestr("/abs/outside3.txt", "pwned")
            zf.writestr(f"archives/{aid}/media/../../../outside4.txt", "pwned")
            zf.writestr(f"archives/{aid}/notes.txt", "extra")
            zf.writestr(f"archives/{aid}/media/{cid}.exe", "MZ")

        result = import_zip(self.dst, zip_path)
        self.assertEqual(result["clips"], 1)
        self.assertGreaterEqual(result["ignored_entries"], 5)
        self.assertGreaterEqual(result["skipped_clips"], 2)

        outside = [p.name for p in self.root.rglob("*") if p.name.startswith("outside") or p.name == "evil.mp4"]
        self.assertEqual(outside, [])
        clip = self.dst.get_archive(aid)["clips"][0]
        self.assertEqual(clip["filename"], "evil.mp4")
        self.assertEqual((clip["view"], clip["tags"], clip["results"]), ("", ["x"], {}))
        files = sorted(p.relative_to(self.dst.root).as_posix() for p in self.dst.root.rglob("*") if p.is_file())
        self.assertEqual(files, [f"archives/{aid}/archive.json", f"archives/{aid}/media/{cid}.mp4"])

    def test_rejects_things_that_are_not_exports(self):
        junk = self.root / "junk.zip"
        junk.write_bytes(b"not a zip")
        with self.assertRaises(LibraryError):
            import_zip(self.dst, junk)
        plain = self.root / "plain.zip"
        with zipfile.ZipFile(plain, "w") as zf:
            zf.writestr("hello.txt", "hi")
        with self.assertRaises(LibraryError) as ctx:
            import_zip(self.dst, plain)
        self.assertIn("not a POCUS AI Reader export", str(ctx.exception))
        newer = self.root / "newer.zip"
        with zipfile.ZipFile(newer, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format": "pocus-ai-reader-library", "version": 99}))
        with self.assertRaises(LibraryError):
            import_zip(self.dst, newer)

    def test_failed_import_leaves_nothing_behind(self):
        aid, cid = "c" * 32, "d" * 32
        zip_path = self.root / "oversize.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", MANIFEST)
            zf.writestr(f"archives/{aid}/archive.json",
                        json.dumps({"label": "L", "clips": [{"id": cid, "ext": ".mp4"}]}))
            zf.writestr(f"archives/{aid}/media/{cid}.mp4", b"x" * 500)
        import os
        os.environ["POCUS_AI_READER_MAX_FILE_BYTES"] = "100"
        try:
            with self.assertRaises(LibraryError):
                import_zip(self.dst, zip_path)
        finally:
            del os.environ["POCUS_AI_READER_MAX_FILE_BYTES"]
        self.assertEqual(list(self.dst.archives_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
