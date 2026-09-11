import os
import io
import asyncio
import httpx
import logging
from typing import Any
from PIL import Image, ImageOps

import hashlib

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "mirrored")
os.makedirs(STATIC_DIR, exist_ok=True)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "cache", "images")
os.makedirs(CACHE_DIR, exist_ok=True)

def _get_url_cache_path(url: str) -> str:
    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{url_hash}.jpg")

async def mirror_image_for_xl(image_url: str, ean: str, account: str = "XL") -> str:
    """
    If account is XL, horizontally mirrors the image and saves it to static/mirrored/.
    Returns the path to the mirrored image or the original URL on failure.
    If account is JV or not XL, returns original URL untouched.
    """
    if not image_url or (account or "").upper() != "XL":
        return image_url or ""
    
    clean_ean = "".join(c for c in str(ean) if c.isalnum())
    filename = f"mirrored_XL_{clean_ean}.jpg"
    filepath = os.path.join(STATIC_DIR, filename)
    public_url = f"/static/mirrored/{filename}"

    # If already mirrored and exists, return immediately
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return public_url

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(image_url, headers=headers)
            if resp.status_code == 200:
                img = Image.open(io.BytesIO(resp.content))
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                mirrored_img = ImageOps.mirror(img)
                mirrored_img.save(filepath, format="JPEG", quality=90)
                return public_url
    except Exception as e:
        logging.warning(f"Failed to mirror image {image_url} for EAN {ean}: {e}")

    return image_url

async def fetch_image_bytes(url: str, client: httpx.AsyncClient = None) -> bytes | None:
    """
    Fetches raw image bytes from local disk cache, mirrored path, or remote URL.
    Returns None on failure.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None

    # 1. Check local static mirrored image (support both relative /static/mirrored/ and absolute URLs)
    if url.startswith("/static/mirrored/") or "/static/mirrored/" in url:
        filename = os.path.basename(url.split("?")[0])
        local_path = os.path.join(STATIC_DIR, filename)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            try:
                with open(local_path, "rb") as f:
                    return f.read()
            except Exception as e:
                logging.warning(f"Failed to read local mirrored image {local_path}: {e}")
                return None

    # 2. Check local disk cache by URL hash
    cache_path = _get_url_cache_path(url)
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        try:
            with open(cache_path, "rb") as f:
                return f.read()
        except Exception:
            pass

    # 3. Otherwise fetch via HTTP
    if not (url.startswith("http://") or url.startswith("https://")):
        return None

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }

    try:
        if client:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code == 200 and resp.content:
                # Cache on disk asynchronously/safely
                try:
                    with open(cache_path, "wb") as f:
                        f.write(resp.content)
                except Exception:
                    pass
                return resp.content
        else:
            async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as c:
                resp = await c.get(url, headers=HEADERS)
                if resp.status_code == 200 and resp.content:
                    try:
                        with open(cache_path, "wb") as f:
                            f.write(resp.content)
                    except Exception:
                        pass
                    return resp.content
    except Exception as e:
        logging.warning(f"Failed to fetch image bytes from {url}: {e}")

    return None

async def fetch_images_batch(
    urls: list[str], 
    max_concurrency: int = 30,
    timeout: float = 8.0,
    max_remote: int = 99999,
    progress_callback: Any = None
) -> dict[str, bytes]:
    """
    Concurrently fetches image bytes for a list of URLs with deduplication.
    Local files and cached files are loaded instantly from disk.
    Remote HTTP URLs are downloaded concurrently up to max_concurrency with automatic retry.
    Calls progress_callback(completed, total) if provided.
    Returns a dictionary mapping url -> bytes.
    """
    unique_urls = list(set([u for u in urls if u and isinstance(u, str) and u.strip()]))
    if not unique_urls:
        return {}

    results: dict[str, bytes] = {}
    remote_urls = []

    # 1. Check local files and disk cache first
    for u in unique_urls:
        if u.startswith("/static/mirrored/"):
            data = await fetch_image_bytes(u)
            if data:
                results[u] = data
        else:
            cache_path = _get_url_cache_path(u)
            if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
                data = await fetch_image_bytes(u)
                if data:
                    results[u] = data
                else:
                    remote_urls.append(u)
            else:
                remote_urls.append(u)

    total_unique = len(unique_urls)
    completed_counter = len(results)

    if progress_callback:
        progress_callback(completed_counter, total_unique, f"Загружено {completed_counter} из {total_unique} картинок")

    # 2. Remote network fetches for remaining URLs
    urls_to_fetch = remote_urls[:max_remote]
    if urls_to_fetch:
        sem = asyncio.Semaphore(max_concurrency)
        limits = httpx.Limits(max_connections=max_concurrency * 2, max_keepalive_connections=max_concurrency)
        HEADERS = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        }
        lock = asyncio.Lock()

        async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True, headers=HEADERS) as client:
            async def worker(u: str):
                nonlocal completed_counter
                async with sem:
                    try:
                        data = await fetch_image_bytes(u, client=client)
                        if not data:
                            # 1 quick retry for slow CDNs
                            await asyncio.sleep(0.3)
                            data = await fetch_image_bytes(u, client=client)
                        if data:
                            results[u] = data
                    except Exception:
                        pass
                    finally:
                        async with lock:
                            completed_counter += 1
                            if progress_callback and (completed_counter % 10 == 0 or completed_counter == total_unique):
                                progress_callback(
                                    completed_counter, 
                                    total_unique, 
                                    f"Загрузка картинок: {completed_counter} из {total_unique}"
                                )

            tasks = [worker(u) for u in urls_to_fetch]
            await asyncio.gather(*tasks)

    if progress_callback:
        progress_callback(total_unique, total_unique, f"Загрузка картинок завершена: {len(results)}/{total_unique}")

    return results
