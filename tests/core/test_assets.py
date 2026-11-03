"""The watermark library accepts PNG, WebP and JPG (what FFmpeg reads), and the transparency of the first two survives."""
import io
import os
import tempfile
import unittest
from unittest import mock

from PIL import Image

import config
from app import app
from modules.competitivo import layout


def _image(fmt: str, alpha: bool = True) -> bytes:
    img = Image.new("RGBA" if alpha else "RGB", (200, 120), (0, 0, 0, 0) if alpha else (30, 60, 200))
    if alpha:
        for x in range(60, 140):                      # a visible block inside a transparent margin
            for y in range(30, 90):
                img.putpixel((x, y), (255, 40, 40, 255))
    buf = io.BytesIO()
    img.save(buf, fmt, **({"lossless": True} if fmt == "WEBP" else {}))
    return buf.getvalue()


class WatermarkFormats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "WATERMARKS_FOLDER", self.tmp.name)
        self.patch.start()
        self.client = app.test_client()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _upload(self, name: str, data: bytes):
        return self.client.post("/api/watermarks", data={"file": (io.BytesIO(data), name)},
                                content_type="multipart/form-data")

    def test_upload_and_list(self):
        for name, data in (("a.png", _image("PNG")), ("b.webp", _image("WEBP")), ("c.jpg", _image("JPEG", False)),
                           ("d.JPEG", _image("JPEG", False))):
            self.assertEqual(self._upload(name, data).status_code, 201, name)
        listed = [w["name"] for w in self.client.get("/api/watermarks").get_json()["watermarks"]]
        self.assertEqual(listed, ["a.png", "b.webp", "c.jpg", "d.JPEG"])
        for name in ("e.gif", "f.txt", "g.svg", "noext"):
            r = self._upload(name, b"x")
            self.assertEqual(r.status_code, 400, name)
            self.assertIn("PNG, WebP o JPG", r.get_json()["error"])

    def test_the_transparency_of_png_and_webp_is_kept(self):
        for name, fmt in (("a.png", "PNG"), ("b.webp", "WEBP")):
            path = os.path.join(self.tmp.name, name)
            with open(path, "wb") as f:
                f.write(_image(fmt))
            self.assertEqual(layout.recorte_marca(path), (80, 60, 60, 30), name)     # only the visible block

    def test_a_jpg_has_no_transparency(self):
        path = os.path.join(self.tmp.name, "c.jpg")
        with open(path, "wb") as f:
            f.write(_image("JPEG", False))
        self.assertEqual(layout.recorte_marca(path), (200, 120, 0, 0))                # the whole picture
