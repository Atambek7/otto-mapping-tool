from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from services.image_processor import (
    ImageNormalizationError,
    MIN_LONG_SIDE,
    MIN_SHORT_SIDE,
    PUBLIC_MEDIA_FILENAME_RE,
    ensure_image_min_dimensions,
    normalize_candidate_images,
    validate_remote_image_url,
)


def _jpeg_bytes(size: tuple[int, int], *, mode: str = "RGB", fmt: str = "JPEG") -> bytes:
    buf = io.BytesIO()
    color = (200, 120, 40, 128) if mode == "RGBA" else (200, 120, 40)
    Image.new(mode, size, color=color).save(buf, format=fmt)
    return buf.getvalue()


class ImageProcessorTests(unittest.TestCase):
    def test_valid_image_is_untouched_and_small_image_is_upscaled_without_skip(self) -> None:
        small_bytes = _jpeg_bytes((640, 236))
        large_bytes = _jpeg_bytes((1280, 851))
        rgba_png_bytes = _jpeg_bytes((400, 400), mode="RGBA", fmt="PNG")

        fixtures = {
            "https://cdn.example/b1.jpg": large_bytes,
            "https://cdn.example/FarbmusterST.jpg": small_bytes,
            "https://cdn.example/swatch.png": rgba_png_bytes,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Path(tmpdir)
            for account in ("JV", "XL"):
                result = normalize_candidate_images(
                    account=account,
                    urls=list(fixtures.keys()),
                    public_base_url="https://omt.automatonsoft.de",
                    storage_dir=storage,
                    fetcher=fixtures.__getitem__,
                )
                # Large valid image keeps original URL untouched
                large_entry = result["https://cdn.example/b1.jpg"]
                self.assertFalse(large_entry["upscaled"])
                self.assertEqual(large_entry["normalizedUrl"], "https://cdn.example/b1.jpg")
                self.assertEqual(large_entry["originalSize"], [1280, 851])

                # Small 640x236 banner is upscaled cleanly with aspect ratio preserved
                small_entry = result["https://cdn.example/FarbmusterST.jpg"]
                self.assertTrue(small_entry["upscaled"])
                self.assertTrue(small_entry["normalizedUrl"].startswith("https://omt.automatonsoft.de/restart/media/upscaled_"))
                self.assertTrue(PUBLIC_MEDIA_FILENAME_RE.fullmatch(small_entry["filename"]))
                self.assertGreaterEqual(max(small_entry["finalSize"]), MIN_LONG_SIDE)
                self.assertGreaterEqual(min(small_entry["finalSize"]), MIN_SHORT_SIDE)
                fw, fh = small_entry["finalSize"]
                self.assertAlmostEqual(fw / fh, 640 / 236, delta=0.05)

                # Saved file exists and matches finalSize
                saved_path = storage / small_entry["filename"]
                self.assertTrue(saved_path.is_file())
                with Image.open(saved_path) as saved_img:
                    self.assertEqual(saved_img.size, (fw, fh))
                    self.assertEqual(saved_img.mode, "RGB")

                # Transparent PNG also converts cleanly to white-backed JPEG
                png_entry = result["https://cdn.example/swatch.png"]
                self.assertTrue(png_entry["upscaled"])
                self.assertGreaterEqual(max(png_entry["finalSize"]), MIN_LONG_SIDE)
                self.assertGreaterEqual(min(png_entry["finalSize"]), MIN_SHORT_SIDE)

    def test_ensure_image_min_dimensions_boundary_exact(self) -> None:
        img = Image.new("RGB", (1000, 500), color="white")
        out, upscaled, orig = ensure_image_min_dimensions(img)
        self.assertFalse(upscaled)
        self.assertEqual(orig, (1000, 500))
        self.assertEqual(out.size, (1000, 500))

    def test_rejects_unsafe_urls_and_corrupt_bytes(self) -> None:
        for bad_url in (
            "http://cdn.example/a.jpg",
            "https://127.0.0.1/a.jpg",
            "https://localhost/a.jpg",
            "https://user:pass@cdn.example/a.jpg",
        ):
            with self.assertRaises(ImageNormalizationError):
                validate_remote_image_url(bad_url)

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ImageNormalizationError) as raised:
                normalize_candidate_images(
                    account="JV",
                    urls=["https://cdn.example/broken.jpg"],
                    public_base_url="https://omt.automatonsoft.de",
                    storage_dir=tmpdir,
                    fetcher=lambda _u: b"not-an-image",
                )
            self.assertEqual(raised.exception.code, "IMAGE_CORRUPT_OR_UNREADABLE")

    def test_public_media_filename_regex_prevents_traversal(self) -> None:
        valid = "upscaled_JV_0123456789ab_fedcba987654.jpg"
        self.assertIsNotNone(PUBLIC_MEDIA_FILENAME_RE.fullmatch(valid))
        for invalid in (
            "../etc/passwd",
            "upscaled_XX_0123456789ab_fedcba987654.jpg",
            "upscaled_JV_0123456789ab_fedcba987654.png",
            "arbitrary.jpg",
        ):
            self.assertIsNone(PUBLIC_MEDIA_FILENAME_RE.fullmatch(invalid))


if __name__ == "__main__":
    unittest.main()
