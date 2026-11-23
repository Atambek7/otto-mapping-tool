#!/usr/bin/env python3
"""Extract only the SKUs present in 3/12M import spreadsheets from full OTTO SQLite dumps.

Usage (on Windows or Linux):
  python extract_otto_sqlite_subset.py \
      --account JV \
      --db otto_jv_full.db \
      --excel jv_new_3m.xlsx jv_new_12m.xlsx \
      --out otto_jv_subset.db

Copies only `(sku, raw_json)` from `products`, `product_marketplace_status`, and
`product_prices`, cutting ~4 GB dumps down to a compact file easy to transfer.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Iterable


def _clean_cell(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).replace("\ufeff", "").strip().strip("\"'").strip()
    return text


def _collect_identifiers_from_xlsx(path: Path) -> set[str]:
    from openpyxl import load_workbook

    found: set[str] = set()
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = wb.active
        for row in sheet.iter_rows(values_only=True):
            for cell in row[:4]:
                val = _clean_cell(cell)
                if val and len(val) <= 128 and not re.fullmatch(r"(?i)(sku|ean|gtin|periode|period|titel|title|id)", val):
                    found.add(val)
    finally:
        wb.close()
    return found


def _collect_identifiers_from_text(path: Path) -> set[str]:
    found: set[str] = set()
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        return found
    delimiter = ";" if ";" in lines[0] else "\t" if "\t" in lines[0] else ","
    if any(sep in lines[0] for sep in (";", ",", "\t")):
        reader = csv.reader(lines, delimiter=delimiter)
        for row in reader:
            for cell in row[:4]:
                val = _clean_cell(cell)
                if val and len(val) <= 128:
                    found.add(val)
    else:
        for line in lines:
            token = _clean_cell(line.split()[0])
            if token and len(token) <= 128:
                found.add(token)
    return found


def collect_identifiers(paths: Iterable[Path]) -> list[str]:
    identifiers: set[str] = set()
    for path in paths:
        suffix = path.suffix.lower()
        if suffix == ".xlsx":
            identifiers.update(_collect_identifiers_from_xlsx(path))
        else:
            identifiers.update(_collect_identifiers_from_text(path))
    return sorted(identifiers)


def extract_subset(source_db: Path, dest_db: Path, identifiers: list[str], *, compress: bool = True) -> dict[str, int]:
    if dest_db.exists():
        dest_db.unlink()
    src = sqlite3.connect(f"file:{source_db.resolve()}?mode=ro", uri=True)
    dst = sqlite3.connect(dest_db)
    try:
        dst.execute("PRAGMA journal_mode=OFF")
        dst.execute("PRAGMA synchronous=OFF")
        dst.execute("CREATE TABLE products (sku TEXT PRIMARY KEY, raw_json TEXT)")
        dst.execute("CREATE TABLE product_marketplace_status (sku TEXT PRIMARY KEY, raw_json TEXT)")
        dst.execute("CREATE TABLE product_prices (sku TEXT, source TEXT, raw_json TEXT)")

        stats = {"requested_identifiers": len(identifiers), "matched_products": 0, "matched_status": 0, "matched_prices": 0}
        batch_size = 900
        matched_skus: set[str] = set()

        # Pass 1: resolve both sku matches AND ean -> sku mappings in `products`.
        for i in range(0, len(identifiers), batch_size):
            chunk = identifiers[i : i + batch_size]
            ph = ",".join("?" for _ in chunk)
            rows = src.execute(
                f"SELECT sku, raw_json FROM products WHERE sku IN ({ph}) OR ean IN ({ph})",
                chunk + chunk,
            ).fetchall()
            if rows:
                dst.executemany("INSERT OR REPLACE INTO products (sku, raw_json) VALUES (?, ?)", rows)
                for sku, _raw in rows:
                    matched_skus.add(str(sku))
        stats["matched_products"] = len(matched_skus)

        lookup_keys = sorted(matched_skus | set(identifiers))
        for i in range(0, len(lookup_keys), batch_size):
            chunk = lookup_keys[i : i + batch_size]
            ph = ",".join("?" for _ in chunk)
            status_rows = src.execute(
                f"SELECT sku, raw_json FROM product_marketplace_status WHERE sku IN ({ph})",
                chunk,
            ).fetchall()
            if status_rows:
                dst.executemany(
                    "INSERT OR REPLACE INTO product_marketplace_status (sku, raw_json) VALUES (?, ?)",
                    status_rows,
                )
                stats["matched_status"] += len(status_rows)

            price_rows = src.execute(
                f"SELECT sku, source, raw_json FROM product_prices WHERE sku IN ({ph})",
                chunk,
            ).fetchall()
            if price_rows:
                dst.executemany(
                    "INSERT INTO product_prices (sku, source, raw_json) VALUES (?, ?, ?)",
                    price_rows,
                )
                stats["matched_prices"] += len(price_rows)

        dst.execute("CREATE INDEX ix_product_prices_sku ON product_prices (sku)")
        dst.commit()
    finally:
        dst.close()
        src.close()

    if compress:
        gz_path = Path(str(dest_db) + ".gz")
        with dest_db.open("rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract subset SQLite DB for OttoRestart 3/12M mass import.")
    parser.add_argument("--db", required=True, help="Path to otto_jv_full.db or otto_xl_full.db")
    parser.add_argument("--excel", nargs="+", required=True, help="One or more .xlsx/.csv/.txt import files")
    parser.add_argument("--out", required=True, help="Output SQLite path (e.g., otto_jv_subset.db)")
    parser.add_argument("--no-gz", action="store_true", help="Do not create .db.gz archive")
    args = parser.parse_args()

    excel_paths = [Path(p) for p in args.excel]
    identifiers = collect_identifiers(excel_paths)
    print(f"Collected {len(identifiers)} unique identifiers from {len(excel_paths)} file(s).")
    stats = extract_subset(Path(args.db), Path(args.out), identifiers, compress=not args.no_gz)
    print("Extraction complete:", stats)


if __name__ == "__main__":
    main()
