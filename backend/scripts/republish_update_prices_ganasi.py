#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conservative, low-load Aftercool -> OTTO standard-price sync for 6 Ganasi factories.

Two-stage design:
  1. python scripts/safe_ganasi_price_sync.py --account JV
     Reads local Published mappings + Aftercool only, writes JSON plan. No OTTO calls.
  2. python scripts/safe_ganasi_price_sync.py --execute --plan PATH --sku-is-ean
     Uses saved plan, NO Aftercool calls; sequential price-only OTTO updates.

SCOPE: Only standardPrice (EUR). No full republish, MSRP, sale, availability,
       product status, attributes, images, categories or descriptions.

CRITICAL: --sku-is-ean is required if DB has no explicit SKU attribute. Only use
          it after verifying the existing OTTO SKUs are really the EANs.

Direct seller OAuth (client credentials) is supported. A service-partner
installation-token integration may require a project-specific adapter.

Dependencies: requests, existing project database/models/aftercool_service.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOG = logging.getLogger("ganasi.price_sync")
FACTORIES = {
    "XL": {"483389": "NEW_GANASI_SPECIAL 1 XL", "483390": "NEW_GANASI 2 XL", "483393": "NEW_Ganasi 3 XL"},
    "JV": {"503917": "NEW_Ganasi 1 JV", "504042": "NEW ganasi 2 JV", "504078": "new ganasi 3 JV"},
}
API_BASE = "https://api.otto.market"
TASK_RE = re.compile(r"^/v5/products/update-tasks/[0-9a-fA-F-]{36}(?:/(?:succeeded|failed|unchanged))?/?$")
EAN_RE = re.compile(r"^\d{13}$")
BLOCKING_STATUSES = {"sending", "pending", "unknown", "throttled", "failed", "unchanged", "succeeded_db_error", "succeeded_db_conflict"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def money(value: Any) -> Decimal | None:
    """Strict positive EUR amount, cents precision; don't silently misparse ambiguous numbers."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        num = value
    else:
        s = str(value).strip().replace("\u00a0", "").replace(" ", "")
        if not s or s.lower() in {"none", "null", "nan", "-"}:
            return None
        s = s.replace("€", "")
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            num = Decimal(s)
        except InvalidOperation:
            return None
    if not num.is_finite() or num <= 0 or num > 1000000:
        return None
    if num != num.quantize(Decimal("0.01")):
        return None
    return num.quantize(Decimal("0.01"))


def norm_ean(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def get_sku(mapping: Any, ean: str) -> tuple[str, str]:
    for attr in ("otto_sku", "product_sku", "sku"):
        sku = getattr(mapping, attr, None)
        if sku and str(sku).strip():
            return str(sku).strip(), attr
    return ean, "ean_unverified"


def plan_digest(items: list[dict]) -> str:
    serial = json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


async def aftercool_prices(account: str, factory: str, eans: set[str], delay: float) -> tuple[dict[str, Decimal], set[str]]:
    """Read factory's existing Aftercool paginated iterator; never query per SKU.

    One consumer, with a configurable sleep BETWEEN returned batches. If the
    iterator itself internally prefetches, internal pacing must be verified too.
    """
    from services.aftercool_service import iter_products

    found: dict[str, Decimal] = {}
    invalid: set[str] = set()
    batches = 0
    async for batch in iter_products(factory_id=factory, account=account, dataset="lister"):
        batches += 1
        for prod in batch:
            ean = norm_ean(getattr(prod, "ean", None))
            if ean not in eans:
                continue
            new = money(getattr(prod, "price", None))
            if new is None:
                invalid.add(ean)
                continue
            if ean in found and found[ean] != new:
                invalid.add(ean)  # conflicting duplicate: NEVER choose arbitrary value
            else:
                found[ean] = new
        if delay > 0:
            await asyncio.sleep(delay)
    for ean in invalid:
        found.pop(ean, None)
    LOG.info("Aftercool %s/%s: batches=%d, matched=%d, invalid/conflicting=%d", account, factory, batches, len(found), len(invalid))
    return found, invalid


def build_plan(db: Any, args: argparse.Namespace) -> dict:
    from models import ProductMapping

    items: list[dict] = []
    stats: Counter = Counter()
    accounts = ("XL", "JV") if args.account == "ALL" else (args.account,)
    mappings_by_factory: dict[tuple[str, str], list[Any]] = {}
    global_eans: dict[tuple[str, str], int] = Counter()
    global_skus: dict[tuple[str, str], int] = Counter()

    for account in accounts:
        for factory in FACTORIES[account]:
            rows = (db.query(ProductMapping).filter(
                ProductMapping.account_name == account,
                ProductMapping.factory_id == factory,
                ProductMapping.status == "Published",
            ).all())
            mappings_by_factory[(account, factory)] = rows
            stats["published"] += len(rows)
            for row in rows:
                ean = norm_ean(row.product_ean)
                sku, _ = get_sku(row, ean)
                global_eans[account, ean] += 1
                global_skus[account, sku] += 1

    for (account, factory), rows in mappings_by_factory.items():
        if not rows:
            LOG.info("Skip empty factory %s/%s (no Published mappings)", account, factory)
            continue
        lookup = {norm_ean(row.product_ean) for row in rows if EAN_RE.fullmatch(norm_ean(row.product_ean))}
        if not lookup:
            stats["invalid_ean"] += len(rows)
            continue
        try:
            prices, invalid = asyncio.run(aftercool_prices(account, factory, lookup, args.aftercool_delay))
        except Exception:
            LOG.exception("Aftercool failed for %s/%s: plan generation ABORTED; no incomplete plan saved", account, factory)
            raise
        stats["bad_aftercool_price"] += len(invalid)
        for row in rows:
            ean = norm_ean(row.product_ean)
            if not EAN_RE.fullmatch(ean):
                stats["invalid_ean"] += 1
                continue
            sku, sku_source = get_sku(row, ean)
            if not sku or len(sku) > 50:
                stats["invalid_sku"] += 1
                continue
            if global_eans[account, ean] != 1 or global_skus[account, sku] != 1:
                stats["duplicate_db_mapping"] += 1
                continue
            if ean in invalid:
                continue
            new = prices.get(ean)
            if new is None:
                stats["missing_aftercool"] += 1
                continue
            old = money(row.price)
            if old == new:
                stats["unchanged_db"] += 1
                continue
            if old and (abs(new - old) / old * 100) > Decimal(str(args.max_change_pct)):
                stats["large_change_skipped"] += 1
                continue
            stats["changed"] += 1
            if sku_source == "ean_unverified":
                stats["sku_needs_confirmation"] += 1
            items.append({
                "account": account, "factory": factory, "ean": ean, "sku": sku,
                "sku_source": sku_source, "old": str(old) if old else None,
                "new": str(new),
            })
        LOG.info("Factory %s/%s processed", account, factory)
    items.sort(key=lambda x: (x["account"], x["factory"], x["sku"]))
    return {"version": 1, "created_at": utcnow(), "accounts": list(accounts),
            "stats": dict(stats), "items": items, "sha256": plan_digest(items)}


def write_plan(plan: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"ganasi_price_plan_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"
    if path.exists():
        raise FileExistsError(path)
    with path.open("x", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def load_plan(path: Path, max_age_minutes: int, account: str) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("version") != 1 or not isinstance(plan.get("items"), list):
        raise ValueError("Unknown plan schema")
    if plan.get("sha256") != plan_digest(plan["items"]):
        raise ValueError("Plan modified or corrupted (sha256 mismatch)")
    created = datetime.fromisoformat(plan["created_at"])
    if created.tzinfo is None:
        raise ValueError("Plan timestamp missing timezone")
    age = (datetime.now(timezone.utc) - created).total_seconds()
    if age < -120 or age > max_age_minutes * 60:
        raise ValueError("Plan is expired or created in the future: regenerate to get current Aftercool prices")
    if account != "ALL" and account not in plan.get("accounts", []):
        raise ValueError(f"Plan does not contain account {account}")
    seen_keys = set()
    for i in plan["items"]:
        key = (i.get("account"), i.get("sku"))
        if key in seen_keys:
            raise ValueError("Duplicate account+SKU in plan")
        seen_keys.add(key)
        if i.get("sku_source") not in {"otto_sku", "product_sku", "sku", "ean_unverified"}:
            raise ValueError("Unknown SKU mapping provenance")
        if i.get("account") not in FACTORIES or i.get("factory") not in FACTORIES[i["account"]]:
            raise ValueError("Plan contains non-target factory")
        if not EAN_RE.fullmatch(i.get("ean", "")) or not i.get("sku") or len(i["sku"]) > 50 or money(i.get("new")) is None:
            raise ValueError("Plan includes invalid product identifier or price")
    return plan


class Journal:
    """Durable safety journal. Never delete it between runs."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=4)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=4000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS items (
            account TEXT NOT NULL, sku TEXT NOT NULL, ean TEXT NOT NULL,
            factory TEXT NOT NULL, old_price TEXT, new_price TEXT NOT NULL,
            status TEXT NOT NULL, task_href TEXT, error TEXT, changed_at TEXT NOT NULL,
            PRIMARY KEY(account,sku))""")
        self.db.commit()

    def get(self, account: str, sku: str) -> dict | None:
        row = self.db.execute("SELECT account,sku,ean,factory,old_price,new_price,status,task_href,error FROM items WHERE account=? AND sku=?", (account, sku)).fetchone()
        if row is None:
            return None
        return dict(zip(("account", "sku", "ean", "factory", "old", "new", "status", "task_href", "error"), row))

    def set(self, entry: dict, status: str, href: str | None = None, error: str | None = None):
        self.db.execute("""INSERT INTO items(account,sku,ean,factory,old_price,new_price,status,task_href,error,changed_at)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account,sku) DO UPDATE SET
        ean=excluded.ean,factory=excluded.factory,old_price=excluded.old_price,
        new_price=excluded.new_price,status=excluded.status,task_href=excluded.task_href,
        error=excluded.error,changed_at=excluded.changed_at""",
        (entry["account"], entry["sku"], entry["ean"], entry["factory"], entry.get("old"),
         entry["new"], status, href, (error or "")[:2000], utcnow()))
        self.db.commit()

    def unresolved_tasks(self, account: str):
        rows = self.db.execute("""SELECT DISTINCT task_href FROM items WHERE account=? AND status='pending' AND task_href IS NOT NULL""", (account,)).fetchall()
        return [r[0] for r in rows]

    def entries_for_task(self, account: str, href: str):
        rows = self.db.execute("SELECT account,sku,ean,factory,old_price,new_price FROM items WHERE account=? AND task_href=? AND status='pending'", (account, href)).fetchall()
        return [dict(zip(("account", "sku", "ean", "factory", "old", "new"), r)) for r in rows]

    def export(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.db.execute("SELECT account, factory, ean, sku, old_price, new_price, status, task_href, error, changed_at FROM items ORDER BY account,factory,sku").fetchall()
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["account", "factory", "ean", "sku", "old_price", "new_price", "status", "task_href", "error", "changed_at"])
            w.writerows(rows)

    def count_states(self, accounts: tuple[str, ...]) -> Counter:
        counts = Counter()
        for account in accounts:
            for status, amount in self.db.execute(
                    "SELECT status,COUNT(*) FROM items WHERE account=? GROUP BY status", (account,)):
                counts[status] += amount
        return counts

    def close(self):
        self.db.close()


@contextmanager
def exclusive_run_lock(path: Path):
    """Protect API services and the SQLite safety journal against concurrent runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(str(path) + ".lock", "a+b")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if handle.tell() == 0 and not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"Another Ganasi price-sync may be running (lock {path}.lock)") from exc
        yield
    finally:
        if os.name == "nt":
            try:
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        handle.close()


class PauseForRateLimit(RuntimeError):
    pass


class Otto:
    def __init__(self, client_id: str, client_secret: str, gap: float):
        self.id, self.secret, self.gap = client_id, client_secret, gap
        self.session = requests.Session()
        self.token: str | None = None
        self.token_expires = 0.0
        self.last_request = 0.0

    def _pace(self):
        pause = self.gap - (time.monotonic() - self.last_request)
        if pause > 0:
            time.sleep(pause)
        self.last_request = time.monotonic()

    @staticmethod
    def _retry_after(header: str | None) -> float:
        if not header:
            return 60.0
        try:
            return max(1.0, min(600.0, float(header)))
        except ValueError:
            try:
                return max(1.0, min(600.0, (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                return 60.0

    def _token(self):
        if self.token and time.monotonic() < self.token_expires:
            return
        self._pace()
        resp = self.session.post(API_BASE + "/v1/token", data={
            "grant_type": "client_credentials", "client_id": self.id,
            "client_secret": self.secret, "scope": "products",
        }, timeout=(10, 30))
        if resp.status_code == 429:
            raise PauseForRateLimit(f"OTTO token 429, Retry-After={resp.headers.get('Retry-After')}")
        resp.raise_for_status()
        data = resp.json()
        if not data.get("access_token"):
            raise ValueError("OTTO token response has no access_token")
        self.token = data["access_token"]
        self.token_expires = time.monotonic() + max(0, int(data.get("expires_in", 1800)) - 90)

    def request(self, method: str, path: str, *, body=None) -> dict:
        if path != "/v5/products/prices" and not TASK_RE.fullmatch(path):
            raise ValueError("Refusing endpoint outside price sync/update tasks")
        for attempt in range(3 if method == "GET" else 2):
            self._token()
            self._pace()
            res = self.session.request(method, API_BASE + path,
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                json=body, timeout=(10, 35))
            if res.status_code == 401 and attempt == 0:
                self.token = None
                continue
            if res.status_code == 429:
                wait = self._retry_after(res.headers.get("Retry-After"))
                LOG.warning("OTTO 429 on %s: sleeping %.0fs", path, wait)
                time.sleep(wait)
                if attempt < (2 if method == "GET" else 1):
                    continue  # server rejected with 429, safe retry once
                raise PauseForRateLimit(f"OTTO repeated 429 on {path}; stop execution")
            if method == "GET" and 500 <= res.status_code <= 599 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            res.raise_for_status()
            if method == "POST" and res.status_code != 202:
                raise RuntimeError(f"Unexpected OTTO POST status {res.status_code}, expected 202")
            data = res.json()
            if not isinstance(data, dict):
                raise ValueError("OTTO API response not an object")
            return data
        raise RuntimeError("OTTO retry attempts exhausted")

    def close(self):
        self.session.close()


def links(data: dict) -> dict[str, str]:
    result = {}
    for lnk in data.get("links", []) or []:
        if isinstance(lnk, dict) and lnk.get("rel") in {"self", "succeeded", "failed", "unchanged", "next"}:
            href = lnk.get("href")
            if isinstance(href, str):
                parsed = urlparse(href)
                if parsed.scheme or parsed.netloc or not TASK_RE.fullmatch(parsed.path) or parsed.query:
                    continue
                result[lnk["rel"]] = parsed.path
    return result


def items_from_task_result(data: Any) -> set[str] | None:
    """Conservative SKU extractor. None if response format can't be interpreted."""
    if isinstance(data, list):
        arr = data
    elif isinstance(data, dict):
        arr = next((data[key] for key in ("items", "results", "products", "productVariations", "data")
                    if isinstance(data.get(key), list)), None)
        if arr is None:
            return None
    else:
        return None
    if any(not isinstance(v, dict) or not isinstance(v.get("sku"), str) for v in arr):
        return None
    return {v["sku"] for v in arr}


def _get_result_set(client: Otto, href: str, expected: int) -> set[str] | None:
    # A small batch (<100) should fit in one page, fail closed if it doesn't.
    data = client.request("GET", href)
    result = items_from_task_result(data)
    if result is None or len(result) != expected:
        return None
    return result


def sync_db_for_succeeded(db: Any, journal: Journal, item: dict, href: str):
    from models import ProductMapping
    rows = db.query(ProductMapping).filter(
        ProductMapping.account_name == item["account"],
        ProductMapping.factory_id == item["factory"],
        ProductMapping.product_ean == item["ean"],
        ProductMapping.status == "Published").all()
    if len(rows) != 1:
        journal.set(item, "succeeded_db_conflict", href, "Published mapping not unique or no longer present")
        return
    row = rows[0]
    old_now = money(row.price)
    target = money(item["new"])
    if old_now == target:
        journal.set(item, "succeeded", href)
        return
    if old_now != money(item.get("old")):
        journal.set(item, "succeeded_db_conflict", href, "DB price changed since plan generation")
        return
    try:
        row.price = str(target)
        db.commit()
    except Exception as ex:
        db.rollback()
        journal.set(item, "succeeded_db_error", href, f"DB commit failure: {ex}")
        return
    journal.set(item, "succeeded", href)


def reconcile_task(client: Otto, db: Any, journal: Journal, account: str, href: str,
                   poll_interval: float, timeout_sec: float) -> bool:
    batch = journal.entries_for_task(account, href)
    if not batch:
        return True
    until = time.monotonic() + timeout_sec
    while True:
        info = client.request("GET", href)
        state = str(info.get("state", "")).lower()
        if state == "done":
            break
        if state not in {"pending", "processing", "in_progress"}:
            LOG.error("Unknown OTTO task state %r; keep batch pending", state)
            return False
        if time.monotonic() >= until:
            LOG.warning("Task polling timeout; task remains pending: %s", href)
            return False
        time.sleep(poll_interval)
    counters = {}
    for key in ("succeeded", "failed", "unchanged"):
        val = info.get(key, 0)
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            LOG.error("Unrecognized task %s counter: %r", key, val)
            return False
        counters[key] = val
    if sum(counters.values()) != len(batch):
        LOG.error("Task total doesn't match saved batch: %s vs %s. Remains pending", counters, len(batch))
        return False
    if counters["succeeded"] == len(batch):
        for item in batch:
            sync_db_for_succeeded(db, journal, item, href)
        return True
    hrefs = links(info)
    sets: dict[str, set[str]] = {}
    for kind, count in counters.items():
        if not count:
            sets[kind] = set()
            continue
        detail_href = hrefs.get(kind, href.rstrip("/") + "/" + kind)
        sets[kind] = _get_result_set(client, detail_href, count)
        if sets[kind] is None:
            LOG.error("Can't identify per-SKU %s task results. Batch remains pending", kind)
            return False
    sku_set = {x["sku"] for x in batch}
    if (set.union(*sets.values()) != sku_set or
            sum(len(s) for s in sets.values()) != len(batch)):
        LOG.error("Result SKUs don't match submitted SKUs. Batch remains pending")
        return False
    for item in batch:
        if item["sku"] in sets["succeeded"]:
            sync_db_for_succeeded(db, journal, item, href)
        elif item["sku"] in sets["unchanged"]:
            journal.set(item, "unchanged", href, "OTTO reported unchanged: verify current price before retry")
        else:
            journal.set(item, "failed", href, "OTTO rejected price: see task failed details")
    return True


def resume_existing(client: Otto, db: Any, journal: Journal, account: str, args):
    for href in journal.unresolved_tasks(account):
        LOG.info("Resuming previously accepted OTTO task %s %s", account, href)
        reconcile_task(client, db, journal, account, href, args.poll_interval, args.poll_timeout)


def _load_credentials(db: Any, account: str) -> tuple[str, str]:
    from models import AccountSettings
    settings = db.query(AccountSettings).filter(AccountSettings.account_name == account).first()
    if not settings or not settings.otto_client_id or not settings.otto_client_secret:
        raise ValueError(f"OTTO credentials not configured for {account}")
    return settings.otto_client_id, settings.otto_client_secret


def _still_published(db: Any, item: dict) -> bool:
    from models import ProductMapping
    rows = db.query(ProductMapping).filter(
        ProductMapping.account_name == item["account"],
        ProductMapping.factory_id == item["factory"],
        ProductMapping.product_ean == item["ean"],
        ProductMapping.status == "Published").all()
    return len(rows) == 1 and get_sku(rows[0], item["ean"])[0] == item["sku"] and money(rows[0].price) == money(item.get("old"))


def perform(db: Any, journal: Journal, plan: dict | None, args):
    accounts = ("XL", "JV") if args.account == "ALL" else (args.account,)
    if plan and not args.sku_is_ean and any(
        i.get("sku_source") == "ean_unverified" and (args.account == "ALL" or i["account"] == args.account)
        for i in plan["items"]
    ):
        raise ValueError("Plan contains mappings without explicit OTTO SKU. Verify SKU == EAN first; then use --sku-is-ean")
    total_sent = 0
    for account in accounts:
        intended = [i for i in (plan or {}).get("items", []) if i["account"] == account]
        if not intended and not journal.unresolved_tasks(account):
            continue
        client_id, secret = _load_credentials(db, account)
        client = Otto(client_id, secret, args.otto_gap)
        try:
            resume_existing(client, db, journal, account, args)
            if not intended:
                continue
            queue = []
            for item in intended:
                state = journal.get(account, item["sku"])
                if state and state["status"] in BLOCKING_STATUSES:
                    LOG.warning("SKIP %s/%s: journal status=%s, no automatic re-submit", account, item["sku"], state["status"])
                    continue
                if state and state["status"] == "succeeded" and state["new"] == item["new"]:
                    continue
                if item["sku_source"] == "ean_unverified" and not args.sku_is_ean:
                    LOG.error("SKIP %s/%s: cannot verify SKU == EAN. Use --sku-is-ean ONLY after confirming", account, item["ean"])
                    continue
                if not _still_published(db, item):
                    LOG.warning("SKIP %s/%s: DB mapping/status/old price changed since the plan", account, item["sku"])
                    continue
                queue.append(item)
            if args.max_updates:
                queue = queue[:max(0, args.max_updates - total_sent)]
            if not queue:
                continue
            LOG.info("OTTO %s: %d changed SKU(s), batches=%d", account, len(queue), (len(queue)+args.batch_size-1)//args.batch_size)
            for start in range(0, len(queue), args.batch_size):
                chunk = queue[start:start + args.batch_size]
                # Persist pre-flight state BEFORE contacting OTTO (crash-safe against auto replay).
                for item in chunk:
                    journal.set(item, "sending", error="Potentially sent: do NOT auto retry without checking OTTO")
                payload = [{"sku": i["sku"], "standardPrice": {"amount": float(i["new"]), "currency": "EUR"}} for i in chunk]
                try:
                    response = client.request("POST", "/v5/products/prices", body=payload)
                    href = links(response).get("self")
                    if not href:
                        raise ValueError("202 response missing valid update-task self href")
                    for item in chunk:
                        journal.set(item, "pending", href)
                    total_sent += len(chunk)
                    reconcile_task(client, db, journal, account, href, args.poll_interval, args.poll_timeout)
                except PauseForRateLimit:
                    for item in chunk:
                        if journal.get(account, item["sku"])["status"] == "sending":
                            journal.set(item, "throttled", error="429: verify before retry")
                    raise
                except Exception as ex:
                    for item in chunk:
                        if journal.get(account, item["sku"])["status"] == "sending":
                            journal.set(item, "unknown", error=str(ex))
                    LOG.exception("Batch outcome UNKNOWN; STOP to prevent duplicates")
                    raise
                # Avoid stacking updates while any task remains unresolved.
                if journal.entries_for_task(account, href):
                    LOG.error("Task unresolved. STOP this account to avoid unnecessary load")
                    break
                if any(journal.get(account, i["sku"])["status"] != "succeeded" for i in chunk):
                    LOG.error("Batch includes failed/unchanged/DB-conflicted results. STOP account for manual inspection")
                    break
        finally:
            client.close()
    LOG.info("Finished. Submitted %d SKU(s) to OTTO", total_sent)
    selected_accounts = tuple(accounts)
    LOG.info("Journal states: %s", dict(journal.count_states(selected_accounts)))
    if plan:
        planned = [i for i in plan["items"] if i["account"] in selected_accounts]
        bad_statuses = []
        for entry in planned:
            state = journal.get(entry["account"], entry["sku"])
            if state and state["status"] != "succeeded":
                bad_statuses.append(state["status"])
            elif not state and not args.max_updates:
                bad_statuses.append("not_sent")
        if bad_statuses:
            LOG.warning("Plan not fully applied: %s", dict(Counter(bad_statuses)))
            return 3
    elif any(journal.unresolved_tasks(a) for a in selected_accounts):
        return 3
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Low-load Ganasi Published prices: Aftercool -> OTTO (standardPrice only)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Send ONLY price-only updates using --plan; never queries Aftercool")
    mode.add_argument("--resume-only", action="store_true", help="Poll accepted pending tasks; never sends a price update or queries Aftercool")
    p.add_argument("--dry-run", action="store_true", help="Explicit alias for default: generate plan, no OTTO calls")
    p.add_argument("--plan", type=Path, help="JSON plan produced by dry-run (mandatory with --execute)")
    p.add_argument("--account", choices=("ALL", "XL", "JV"), default="ALL")
    p.add_argument("--sku-is-ean", action="store_true", help="Explicitly confirm SKU == EAN if mapping has no explicit SKU")
    p.add_argument("--aftercool-delay", type=float, default=1.5, help="Pause between Aftercool batches (default 1.5s)")
    p.add_argument("--otto-gap", type=float, default=3.0, help="Minimum pause between OTTO requests per account (default 3s)")
    p.add_argument("--batch-size", type=int, default=25, help="Price rows per OTTO POST (default 25, maximum 100 for safety)")
    p.add_argument("--poll-interval", type=float, default=15.0, help="Seconds between status requests (default 15s)")
    p.add_argument("--poll-timeout", type=float, default=300.0, help="Max seconds to poll a single task (default 300s)")
    p.add_argument("--max-updates", type=int, default=0, help="Limit sent SKUs, e.g. 5 for a trial; 0=all")
    p.add_argument("--max-change-pct", type=float, default=50.0, help="Skip price jumps larger than this percent (default 50)")
    p.add_argument("--max-plan-age-minutes", type=int, default=60, help="Maximum age of plan for --execute (default 60min)")
    p.add_argument("--out-dir", type=Path, default=ROOT / "reports" / "ganasi_prices")
    p.add_argument("--journal", type=Path, default=ROOT / "var" / "ganasi_price_sync.sqlite")
    args = p.parse_args(argv)
    if args.batch_size < 1 or args.batch_size > 100 or args.aftercool_delay < 0 or args.otto_gap < 1 or args.poll_interval < 5 or args.poll_timeout < 15 or args.max_updates < 0 or args.max_change_pct < 0 or args.max_plan_age_minutes < 1:
        p.error("Invalid limits. Batch 1-100; OTTO >=1s; poll >=5s; prices and durations must be non-negative")
    if args.execute and not args.plan:
        p.error("--execute requires --plan from a fresh dry-run (no repeat Aftercool calls)")
    if args.dry_run and (args.execute or args.resume_only):
        p.error("--dry-run cannot be combined with --execute / --resume-only")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with exclusive_run_lock(args.journal):
            if not args.execute and not args.resume_only:
                # Never open/modify safety journal or call OTTO in default mode.
                from database import SessionLocal
                db = SessionLocal()
                try:
                    plan = build_plan(db, args)
                finally:
                    db.close()
                out = write_plan(plan, args.out_dir)
                LOG.info("DRY-RUN: plan saved to %s", out)
                LOG.info("Summary: %s", json.dumps(plan["stats"], ensure_ascii=False, sort_keys=True))
                if plan["stats"].get("sku_needs_confirmation"):
                    LOG.warning("Plan has %s SKUs assumed equal to EAN. Confirm the OTTO SKU convention before execution!", plan["stats"]["sku_needs_confirmation"])
                LOG.info("No OTTO API calls; no DB changes. Use --execute --plan '%s' after verifying SKU mapping", out)
                return 0
            plan = None if args.resume_only else load_plan(args.plan, args.max_plan_age_minutes, args.account)
            from database import SessionLocal
            db = SessionLocal()
            journal = Journal(args.journal)
            try:
                result = perform(db, journal, plan, args)
            finally:
                journal.export(args.out_dir / "ganasi_price_sync_results.csv")
                journal.close()
                db.close()
            LOG.info("Journal kept at %s; results CSV at %s", args.journal, args.out_dir / "ganasi_price_sync_results.csv")
            return result
    except Exception:
        LOG.exception("Execution stopped for safety. Review journal/results; do not delete journal")
        return 2


if __name__ == "__main__":
    sys.exit(main())
