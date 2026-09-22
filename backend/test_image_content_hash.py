import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from PIL import Image

import httpx
from services.image_processor import (
    mirror_image_for_xl,
    ensure_image_dimensions_url,
    STATIC_DIR,
)


class TestImageContentHash(unittest.TestCase):
    def setUp(self):
        self.temp_static = tempfile.mkdtemp(prefix="test_static_mirrored_")
        self.patcher = patch("services.image_processor.STATIC_DIR", self.temp_static)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.temp_static):
            shutil.rmtree(self.temp_static)

    def _create_dummy_image_bytes(self, color="red", size=(1200, 800)) -> bytes:
        img = Image.new("RGB", size, color=color)
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_mirror_image_content_hash_and_cache_busting(self):
        import asyncio
        loop = asyncio.new_event_loop()

        img_bytes_v1 = self._create_dummy_image_bytes(color="blue", size=(1200, 800))
        img_bytes_v2 = self._create_dummy_image_bytes(color="green", size=(1200, 800))

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = img_bytes_v1
        mock_client.get = MagicMock(return_value=mock_resp)

        # Async mock for client.get
        async def _async_get(*args, **kwargs):
            return mock_resp
        mock_client.get = _async_get

        try:
            # 1. First mirroring: returns URL with content hash for v1
            url_v1 = loop.run_until_complete(
                mirror_image_for_xl(
                    image_url="https://supplier.com/item.jpg",
                    ean="4071489000001",
                    account="XL",
                    client=mock_client,
                )
            )
            self.assertTrue(url_v1.startswith("/static/mirrored/mirrored_XL_4071489000001_"))
            fname_v1 = os.path.basename(url_v1)
            self.assertTrue(os.path.exists(os.path.join(self.temp_static, fname_v1)))

            # 2. Fast cache hit: without force_refresh, returns cached file
            url_cached = loop.run_until_complete(
                mirror_image_for_xl(
                    image_url="https://supplier.com/item.jpg",
                    ean="4071489000001",
                    account="XL",
                    client=mock_client,
                    force_refresh=False,
                )
            )
            self.assertTrue(os.path.exists(os.path.join(self.temp_static, os.path.basename(url_cached))))

            # 3. Content changes at SAME URL -> Cache-busting with force_refresh=True
            mock_resp.content = img_bytes_v2
            url_v2 = loop.run_until_complete(
                mirror_image_for_xl(
                    image_url="https://supplier.com/item.jpg",
                    ean="4071489000001",
                    account="XL",
                    client=mock_client,
                    force_refresh=True,
                )
            )
            fname_v2 = os.path.basename(url_v2)
            self.assertTrue(os.path.exists(os.path.join(self.temp_static, fname_v2)))

            # CRITICAL CDN CACHE BUSTING VERIFICATION:
            # The filenames MUST be different because content changed!
            self.assertNotEqual(url_v1, url_v2)
            self.assertNotEqual(fname_v1, fname_v2)

        finally:
            loop.close()

    @patch("services.image_processor.fetch_image_bytes")
    def test_ensure_image_dimensions_upscales_with_content_hash(self, mock_fetch):
        import asyncio
        loop = asyncio.new_event_loop()

        # Image smaller than OTTO minimum dimensions (400x300)
        small_img = self._create_dummy_image_bytes(color="yellow", size=(400, 300))
        mock_fetch.return_value = small_img

        try:
            res_url = loop.run_until_complete(
                ensure_image_dimensions_url(
                    image_url="https://supplier.com/small.jpg",
                    ean="4071489000002",
                )
            )
            self.assertTrue(res_url.startswith("/static/mirrored/mirrored_upscaled_4071489000002_"))
            fname = os.path.basename(res_url)
            saved_path = os.path.join(self.temp_static, fname)
            self.assertTrue(os.path.exists(saved_path))

            # Verify image was upscaled to meet OTTO standards (>= 1000 long side, >= 500 short side)
            with Image.open(saved_path) as im:
                w, h = im.size
                self.assertGreaterEqual(max(w, h), 1000)
                self.assertGreaterEqual(min(w, h), 500)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
