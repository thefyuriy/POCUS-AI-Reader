# SPDX-License-Identifier: AGPL-3.0-only
import unittest

from tests.support import TOKEN, Client, ServerFixture, fake_mp4

CONFIRM = ("confirm_deidentified", "true")


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = ServerFixture()
        cls.c = cls.fx.client

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def make_archive(self, label="Test", files=None, views=None):
        files = files or [("files", "PLAX_1.mp4", fake_mp4()), ("files", "clip2.mp4", fake_mp4())]
        fields = [("label", label), CONFIRM] + [("views", v) for v in (views or ["", "A4C"])]
        r = self.c.upload("/api/archives", fields, files)
        self.assertEqual(r.status, 201, r.body)
        return r.json()

    # -- security --------------------------------------------------------
    def test_api_needs_the_session_cookie(self):
        r = Client(self.fx.port, cookie=False).get("/api/archives")
        self.assertEqual(r.status, 401)
        r = Client(self.fx.port, cookie=False).get("/")
        self.assertEqual(r.status, 401)

    def test_wrong_cookie_is_refused(self):
        r = self.c.get("/api/archives", headers={"Cookie": "pocus_session=nope"})
        self.assertEqual(r.status, 401)

    def test_foreign_host_header_is_refused(self):
        r = self.c.get("/api/archives", headers={"Host": "evil.example:80"})
        self.assertEqual(r.status, 403)

    def test_launch_link_sets_cookie_and_redirects(self):
        r = Client(self.fx.port, cookie=False).get(f"/?t={TOKEN}")
        self.assertEqual(r.status, 303)
        self.assertIn("pocus_session=", r.headers["set-cookie"])
        self.assertIn("HttpOnly", r.headers["set-cookie"])
        self.assertIn("SameSite=strict", r.headers["set-cookie"])
        bad = Client(self.fx.port, cookie=False).get("/?t=wrong")
        self.assertEqual(bad.status, 401)

    def test_cross_origin_writes_are_refused(self):
        r = self.c.send_json("POST", "/api/guess-views", {"names": ["a.mp4"]},
                             headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status, 403)

    def test_static_files_are_public_but_page_is_not(self):
        self.assertEqual(Client(self.fx.port, cookie=False).get("/static/style.css").status, 200)
        page = self.c.get("/")
        self.assertEqual(page.status, 200)
        self.assertIn("default-src 'self'", page.headers["content-security-policy"])

    # -- archives and clips ---------------------------------------------
    def test_upload_requires_deidentification_confirmation(self):
        r = self.c.upload("/api/archives", [("label", "x")], [("files", "a.mp4", fake_mp4())])
        self.assertEqual(r.status, 400)
        self.assertIn("de-identified", r.json()["detail"])
        self.assertNotIn("x", [a["label"] for a in self.c.get("/api/archives").json()])

    def test_unsupported_file_creates_nothing(self):
        before = len(self.c.get("/api/archives").json())
        r = self.c.upload("/api/archives", [("label", "bad"), CONFIRM],
                          [("files", "a.mp4", fake_mp4()), ("files", "report.pdf", b"%PDF")])
        self.assertEqual(r.status, 415)
        self.assertEqual(len(self.c.get("/api/archives").json()), before)

    def test_create_list_get_patch_delete(self):
        a = self.make_archive("Round trip")
        self.assertEqual(len(a["new_clip_ids"]), 2)
        self.assertEqual([c["view"] for c in a["clips"]], ["PLAX", "A4C"])  # guessed, then explicit

        listing = {s["id"]: s for s in self.c.get("/api/archives").json()}
        self.assertEqual(listing[a["id"]]["views"], ["PLAX", "A4C"])
        self.assertEqual(listing[a["id"]]["clip_count"], 2)

        p = self.c.send_json("PATCH", f"/api/archives/{a['id']}", {"label": "Renamed", "tags": "Alpha, beta"})
        self.assertEqual((p.json()["label"], p.json()["tags"]), ("Renamed", ["alpha", "beta"]))

        cid = a["clips"][0]["id"]
        pc = self.c.send_json("PATCH", f"/api/archives/{a['id']}/clips/{cid}",
                              {"view": "IVC", "tags": ["rv"], "notes": "n"})
        self.assertEqual(pc.json()["view"], "IVC")
        bad = self.c.send_json("PATCH", f"/api/archives/{a['id']}/clips/{cid}", {"view": "XYZ"})
        self.assertEqual(bad.status, 400)

        self.assertEqual(self.c.request("DELETE", f"/api/archives/{a['id']}/clips/{cid}").status, 200)
        self.assertEqual(len(self.c.get(f"/api/archives/{a['id']}").json()["clips"]), 1)
        self.assertEqual(self.c.request("DELETE", f"/api/archives/{a['id']}").status, 200)
        self.assertEqual(self.c.get(f"/api/archives/{a['id']}").status, 404)

    def test_add_clips_to_existing_archive(self):
        a = self.make_archive("Grow")
        r = self.c.upload(f"/api/archives/{a['id']}/clips", [CONFIRM, ("views", "SC4C")],
                          [("files", "subcostal.mp4", fake_mp4())])
        self.assertEqual(r.status, 201)
        self.assertEqual(len(r.json()["clips"]), 3)
        self.assertEqual(r.json()["clips"][-1]["view"], "SC4C")

    def test_media_supports_byte_ranges(self):
        data = fake_mp4(5000)
        a = self.make_archive("Range", files=[("files", "r.mp4", data)], views=[""])
        cid = a["clips"][0]["id"]
        url = f"/media/{a['id']}/{cid}"
        full = self.c.get(url)
        self.assertEqual((full.status, full.body), (200, data))
        self.assertEqual(full.headers["content-type"], "video/mp4")
        part = self.c.get(url, headers={"Range": "bytes=10-19"})
        self.assertEqual(part.status, 206)
        self.assertEqual(part.body, data[10:20])
        self.assertEqual(part.headers["content-range"], f"bytes 10-19/{len(data)}")

    def test_thumbnail_upload_and_fetch(self):
        a = self.make_archive("Thumb")
        cid = a["clips"][0]["id"]
        self.assertEqual(self.c.get(f"/thumb/{a['id']}/{cid}").status, 404)
        jpeg = b"\xff\xd8\xff\xe0" + b"1" * 64
        r = self.c.request("PUT", f"/api/archives/{a['id']}/clips/{cid}/thumbnail", jpeg,
                           {"Content-Type": "image/jpeg"})
        self.assertEqual(r.status, 200)
        got = self.c.get(f"/thumb/{a['id']}/{cid}")
        self.assertEqual((got.status, got.body), (200, jpeg))
        bad = self.c.request("PUT", f"/api/archives/{a['id']}/clips/{cid}/thumbnail", b"nope")
        self.assertEqual(bad.status, 415)
        huge = self.c.request("PUT", f"/api/archives/{a['id']}/clips/{cid}/thumbnail", b"\xff\xd8\xff" + b"0" * (3 * 1024 * 1024))
        self.assertEqual(huge.status, 413)

    def test_bad_ids_are_404_not_errors(self):
        for url in ["/api/archives/not-an-id", "/media/not-an-id/also-not", "/thumb/..%2F..%2Fetc/x"]:
            with self.subTest(url=url):
                self.assertEqual(self.c.get(url).status, 404)

    def test_guess_views_endpoint(self):
        r = self.c.send_json("POST", "/api/guess-views", {"names": ["IVC.mp4", "x.mp4", "plax.mov"]})
        self.assertEqual(r.json()["views"], ["IVC", "", "PLAX"])

    def test_export_and_reveal(self):
        a = self.make_archive("Export me")
        r = self.c.send_json("POST", "/api/export", {"ids": [a["id"]]})
        self.assertEqual(r.status, 200, r.body)
        out = r.json()
        self.assertTrue((self.fx.export_dir / out["filename"]).is_file())
        self.assertEqual((out["archives"], out["clips"]), (1, 2))
        rv = self.c.send_json("POST", "/api/reveal", {"target": "export", "filename": out["filename"]})
        self.assertEqual(rv.json()["revealed"], False)  # revealing is disabled in tests
        nope = self.c.send_json("POST", "/api/reveal", {"target": "export", "filename": "../x.zip"})
        self.assertEqual(nope.status, 404)

    def test_import_through_the_api(self):
        a = self.make_archive("Move me")
        exp = self.c.send_json("POST", "/api/export", {"ids": [a["id"]]}).json()
        data = (self.fx.export_dir / exp["filename"]).read_bytes()
        r = self.c.upload("/api/import", [], [("file", "export.zip", data)])
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(r.json()["clips"], 2)
        new_id = r.json()["archives"][0]
        self.assertNotEqual(new_id, a["id"])  # original still exists, so the copy gets a new id
        self.assertEqual(self.c.get(f"/api/archives/{new_id}").json()["label"], "Move me")
        junk = self.c.upload("/api/import", [], [("file", "x.zip", b"not a zip")])
        self.assertEqual(junk.status, 400)


if __name__ == "__main__":
    unittest.main()
