"""Read-only SQLite snapshot adapter for OTTO /v5/products, marketplace-status, and prices.

Reads exported ``otto_jv_*.db`` / ``otto_xl_*.db`` SQLite snapshots (exact ``raw_json``
columns captured from OTTO Market API v5) in zero-write immutable/read-only mode
and transparently falls back to the live ``OttoMarketClient`` only when a SKU is
absent or unreadable in the snapshot.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from integrations.otto_client import OttoApiError

LOGGER = logging.getLogger("ottorestart.otto_sqlite_snapshot")

EXPECTED_ACCOUNT_BRAND_IDS: dict[str, str] = {
    "JV": "UO4EGHSX",
    "XL": "6HMOZBOU",
}

DEFAULT_SNAPSHOT_DIR = Path(os.environ.get("OTTORESTART_OTTO_SNAPSHOT_DIR", "/app/snapshots"))


def _validate_account(account: str) -> str:
    normalized = str(account or "").strip().upper()
    if normalized not in {"JV", "XL"}:
        raise OttoApiError("INVALID_ACCOUNT")
    return normalized


def _parse_json_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def resolve_snapshot_db_path(
    account: str,
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve an existing read-only OTTO SQLite snapshot file for an account."""
    scoped = _validate_account(account)
    source = os.environ if environment is None else environment
    explicit = source.get(f"OTTORESTART_OTTO_{scoped}_SNAPSHOT_DB", "").strip()
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None

    snapshot_dir_raw = source.get("OTTORESTART_OTTO_SNAPSHOT_DIR", str(DEFAULT_SNAPSHOT_DIR)).strip()
    snapshot_dir = Path(snapshot_dir_raw) if snapshot_dir_raw else DEFAULT_SNAPSHOT_DIR
    lower = scoped.lower()
    for name in (f"otto_{lower}_subset.db", f"otto_{lower}_full.db", f"otto_{lower}.db"):
        candidate = snapshot_dir / name
        if candidate.is_file():
            return candidate
    return None


class SqliteSnapshotOttoClient:
    """Thread-safe, read-only OTTO client backed by SQLite with live API fallback."""

    def __init__(
        self,
        account: str,
        sqlite_path: str | Path,
        *,
        fallback_client: Any = None,
    ) -> None:
        self.account = _validate_account(account)
        self.expected_brand_id = EXPECTED_ACCOUNT_BRAND_IDS[self.account]
        self.sqlite_path = Path(sqlite_path)
        if not self.sqlite_path.is_file():
            raise FileNotFoundError(f"OTTO snapshot DB not found: {self.sqlite_path}")
        self._fallback = fallback_client
        self._local = threading.local()
        self._connections_lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._tables_cache: frozenset[str] | None = None

    def _connection(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        resolved = self.sqlite_path.resolve()
        encoded_path = quote(str(resolved), safe="/:")
        try:
            conn = sqlite3.connect(
                f"file:{encoded_path}?mode=ro&immutable=1",
                uri=True,
                check_same_thread=False,
                timeout=5.0,
            )
        except sqlite3.Error:
            conn = sqlite3.connect(
                f"file:{encoded_path}?mode=ro",
                uri=True,
                check_same_thread=False,
                timeout=5.0,
            )
        conn.row_factory = sqlite3.Row
        self._local.conn = conn
        with self._connections_lock:
            self._connections.append(conn)
            if self._tables_cache is None:
                rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                self._tables_cache = frozenset(str(r[0]) for r in rows)
        return conn

    def close(self) -> None:
        with self._connections_lock:
            conns, self._connections = self._connections, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _has_table(self, name: str) -> bool:
        self._connection()
        return bool(self._tables_cache and name in self._tables_cache)

    def _assert_product_brand(self, product: Mapping[str, Any]) -> None:
        desc = product.get("productDescription")
        brand_id = None
        if isinstance(desc, Mapping) and desc.get("brandId") is not None:
            brand_id = str(desc.get("brandId") or "").strip()
        elif product.get("brandId") is not None:
            brand_id = str(product.get("brandId") or "").strip()
        if brand_id and brand_id != self.expected_brand_id:
            raise OttoApiError("ACCOUNT_CREDENTIAL_MISMATCH")

    def get_marketplace_status(self, sku: str) -> dict[str, Any]:
        normalized = str(sku or "").strip()
        if not normalized:
            raise OttoApiError("SKU_REQUIRED")
        if self._has_table("product_marketplace_status"):
            row = self._connection().execute(
                "SELECT * FROM product_marketplace_status WHERE sku = ? LIMIT 1",
                (normalized,),
            ).fetchone()
            if row is not None:
                payload = _parse_json_object(row["raw_json"] if "raw_json" in row.keys() else None)
                if payload is not None and payload.get("sku") == normalized:
                    return payload
                status = row["status"] if "status" in row.keys() else None
                if isinstance(status, str) and status.strip():
                    errors_raw = row["errors_json"] if "errors_json" in row.keys() else "[]"
                    info_raw = row["information_json"] if "information_json" in row.keys() else None
                    try:
                        errors = json.loads(errors_raw) if isinstance(errors_raw, str) else (errors_raw or [])
                    except (TypeError, ValueError):
                        errors = []
                    reconstructed: dict[str, Any] = {
                        "sku": normalized,
                        "status": status.strip(),
                        "errors": errors if isinstance(errors, list) else [],
                    }
                    if "moin" in row.keys() and row["moin"]:
                        reconstructed["moin"] = row["moin"]
                    if "last_modified" in row.keys() and row["last_modified"]:
                        reconstructed["lastModified"] = row["last_modified"]
                    if isinstance(info_raw, str) and info_raw.strip():
                        try:
                            reconstructed["information"] = json.loads(info_raw)
                        except (TypeError, ValueError):
                            pass
                    return reconstructed
        if self._fallback is not None:
            return self._fallback.get_marketplace_status(normalized)
        raise OttoApiError("HTTP_404", status_code=404, retryable=False)

    def get_product(self, sku: str) -> dict[str, Any]:
        normalized = str(sku or "").strip()
        if not normalized:
            raise OttoApiError("SKU_REQUIRED")
        if self._has_table("products"):
            row = self._connection().execute(
                "SELECT * FROM products WHERE sku = ? LIMIT 1",
                (normalized,),
            ).fetchone()
            if row is not None:
                payload = _parse_json_object(row["raw_json"] if "raw_json" in row.keys() else None)
                if payload is not None and payload.get("sku") == normalized:
                    self._assert_product_brand(payload)
                    return payload
        if self._fallback is not None:
            return self._fallback.get_product(normalized)
        raise OttoApiError("HTTP_404", status_code=404, retryable=False)

    def get_product_prices(self, sku: str) -> dict[str, Any]:
        normalized = str(sku or "").strip()
        if not normalized:
            raise OttoApiError("SKU_REQUIRED")
        if self._has_table("product_prices"):
            rows = self._connection().execute(
                "SELECT * FROM product_prices WHERE sku = ?",
                (normalized,),
            ).fetchall()
            # Prefer dedicated /prices endpoint snapshot over nested products_v5 row.
            ordered = sorted(
                rows,
                key=lambda r: 1 if ("source" in r.keys() and str(r["source"] or "") == "products_v5") else 0,
            )
            for row in ordered:
                payload = _parse_json_object(row["raw_json"] if "raw_json" in row.keys() else None)
                if payload is not None and payload.get("sku") == normalized and isinstance(payload.get("standardPrice"), Mapping):
                    return payload
                amount_raw = row["standard_amount"] if "standard_amount" in row.keys() else None
                currency = row["currency"] if "currency" in row.keys() else None
                if amount_raw is not None and isinstance(currency, str) and currency.strip():
                    try:
                        amount = float(amount_raw)
                    except (TypeError, ValueError):
                        continue
                    reconstructed: dict[str, Any] = {
                        "sku": normalized,
                        "standardPrice": {"amount": amount, "currency": currency.strip()},
                    }
                    if "vat" in row.keys() and row["vat"]:
                        reconstructed["vat"] = row["vat"]
                    if "msrp_amount" in row.keys() and row["msrp_amount"] is not None:
                        try:
                            reconstructed["msrp"] = {
                                "price": {
                                    "amount": float(row["msrp_amount"]),
                                    "currency": (row["msrp_currency"] if "msrp_currency" in row.keys() and row["msrp_currency"] else currency.strip()),
                                }
                            }
                        except (TypeError, ValueError):
                            pass
                    return reconstructed
        if self._fallback is not None:
            return self._fallback.get_product_prices(normalized)
        raise OttoApiError("HTTP_404", status_code=404, retryable=False)

    def __getattr__(self, item: str) -> Any:
        if self._fallback is not None:
            return getattr(self._fallback, item)
        raise AttributeError(item)
