from __future__ import annotations

import asyncio
import io
import unittest
from unittest.mock import patch

from fastapi import Request, UploadFile

import app


class EanPoolPreviewTests(unittest.TestCase):
    def test_preview_keeps_all_rows_and_never_opens_database(self):
        numbers = [f"{index:013d}" for index in range(150)]
        content = ("\n".join(numbers + [numbers[0], "not-an-ean"]) + "\n").encode()
        file = UploadFile(file=io.BytesIO(content), filename="eans.txt")
        request = Request({"type": "http", "method": "POST", "path": "/api/restart/ean-pool/preview",
                           "headers": [(b"origin", app.allowed_origins[0].encode())], "query_string": b"",
                           "server": ("testserver", 80), "client": ("testclient", 50000), "scheme": "http", "http_version": "1.1"})
        request.state.principal = app.Principal(kind="operator", subject="test", accounts=frozenset({"JV"}),
                                                permissions=frozenset({"restart:read", "restart:write"}), request_id="offline", csrf_verified=True)
        with patch.object(app, "connect", side_effect=AssertionError("preview must not access DB")):
            result = asyncio.run(app.preview_ean_pool_file(request, account="JV", file=file, x_csrf_token="token"))
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["uniqueCount"], 150)
        self.assertEqual(result["uniqueEans"], numbers)
        self.assertEqual(result["duplicates"], [numbers[0]])
        self.assertEqual(result["invalidEans"], ["not-an-ean"])
