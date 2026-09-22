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

def ensure_image_min_dimensions(
    img: Image.Image,
    min_long_side: int = 1000,
    min_short_side: int = 500,
    target_long_side: int = 1000,
    target_short_side: int = 500
) -> Image.Image:
    """
    Ensures image meets OTTO's strict minimum dimension requirements:
    - Longest side >= min_long_side (1000 px)
    - Shortest side >= min_short_side (500 px)
    If smaller, upscales with aspect ratio preserved using high-quality LANCZOS resampling.
    """
    w, h = img.size
    if max(w, h) < min_long_side or min(w, h) < min_short_side:
        scale = max(target_long_side / max(w, h), target_short_side / min(w, h))
        new_w = max(int(round(w * scale)), min_long_side if w >= h else min_short_side)
        new_h = max(int(round(h * scale)), min_long_side if h > w else min_short_side)
        logging.info(f"Upscaling image from {w}x{h} to {new_w}x{new_h} (LANCZOS) for OTTO requirements")
        return img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return img

async def mirror_image_for_xl(
    image_url: str,
    ean: str,
    account: str = "XL",
    client: httpx.AsyncClient = None,
    bytes_out: dict[str, bytes] = None,
    force_refresh: bool = False
) -> str:
    """
    If account is XL, horizontally mirrors the image and saves it to static/mirrored/.
    Ensures dimensions meet OTTO standards (min 960px on long side).
    Returns the path to the mirrored image or the original URL on failure.
    If account is JV or not XL, returns original URL untouched.
    Uses content-based hash for OTTO CDN cache-busting when content changes.
    Optionally accepts a shared httpx client and a dictionary to store the resulting bytes in RAM.
    """
    if not image_url or (account or "").upper() != "XL":
        return image_url or ""
    
    clean_ean = "".join(c for c in str(ean) if c.isalnum())
    url_hash = hashlib.md5(image_url.encode("utf-8")).hexdigest()[:10]
    filename = f"mirrored_XL_{clean_ean}_{url_hash}.jpg"
    filepath = os.path.join(STATIC_DIR, filename)
    public_url = f"/static/mirrored/{filename}"

    # Phase 1: If already mirrored and exists (and not force_refresh), verify dimensions
    if not force_refresh and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        try:
            with Image.open(filepath) as cached_img:
                cw, ch = cached_img.size
                if max(cw, ch) < 1000 or min(cw, ch) < 500:
                    # Cached file is below OTTO minimum dimensions, upscale it
                    cached_img = cached_img.convert("RGB")
                    upscaled = ensure_image_min_dimensions(cached_img)
                    upscaled.save(filepath, format="JPEG", quality=90)
        except Exception as e:
            logging.debug(f"Could not open cached file {filepath} to check dimensions: {e}")

        if bytes_out is not None and public_url not in bytes_out:
            try:
                with open(filepath, "rb") as f:
                    bytes_out[public_url] = f.read()
            except Exception:
                pass
        return public_url

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        if client is not None:
            resp = await client.get(image_url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
                resp = await c.get(image_url, headers=headers)

        if resp.status_code == 200:
            raw_bytes = resp.content
            # Image Brand Guard: Never mirror foreign brand logos for XL
            from services.brand_guard import KNOWN_JV_LOGO_MD5, KNOWN_JV_LOGO_SIZES
            if len(raw_bytes) in KNOWN_JV_LOGO_SIZES and hashlib.md5(raw_bytes).hexdigest() in KNOWN_JV_LOGO_MD5:
                logging.warning(f"Detected foreign JV logo image for XL EAN {ean}, suppressing.")
                return ""

            img = Image.open(io.BytesIO(raw_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            mirrored_img = ImageOps.mirror(img)
            # Enforce OTTO dimensions (min 960px long side)
            mirrored_img = ensure_image_min_dimensions(mirrored_img)
            
            buf = io.BytesIO()
            mirrored_img.save(buf, format="JPEG", quality=90)
            mirrored_bytes = buf.getvalue()

            # Phase 2: Content-based hash for OTTO CDN cache busting
            content_hash = hashlib.md5(mirrored_bytes).hexdigest()[:10]
            content_filename = f"mirrored_XL_{clean_ean}_{content_hash}.jpg"
            content_filepath = os.path.join(STATIC_DIR, content_filename)
            content_public_url = f"/static/mirrored/{content_filename}"

            with open(content_filepath, "wb") as f:
                f.write(mirrored_bytes)

            # Also maintain url_hash cache file for fast lookups
            if filepath != content_filepath:
                try:
                    with open(filepath, "wb") as f:
                        f.write(mirrored_bytes)
                except Exception:
                    pass

            if bytes_out is not None:
                bytes_out[content_public_url] = mirrored_bytes
                bytes_out[public_url] = mirrored_bytes

            return content_public_url
    except Exception as e:
        logging.warning(f"Failed to mirror image {image_url} for EAN {ean}: {e}")

    return image_url

async def ensure_image_dimensions_url(
    image_url: str,
    ean: str,
    client: httpx.AsyncClient = None,
    bytes_out: dict[str, bytes] = None,
    force_refresh: bool = False
) -> str:
    """
    Checks if an image (local or remote) satisfies OTTO dimensions (min 960px long side, min 480px short side).
    If it's too small, scales it up via LANCZOS, caches in static/mirrored/ with content hash, and returns the static URL.
    If it already satisfies OTTO requirements, returns image_url untouched.
    """
    if not image_url or not isinstance(image_url, str):
        return image_url or ""
    
    image_url = image_url.strip()
    if not image_url:
        return ""

    clean_ean = "".join(c for c in str(ean) if c.isalnum())
    url_hash = hashlib.md5(image_url.encode("utf-8")).hexdigest()[:10]
    filename = f"mirrored_upscaled_{clean_ean}_{url_hash}.jpg"
    filepath = os.path.join(STATIC_DIR, filename)
    public_url = f"/static/mirrored/{filename}"

    # Phase 1: If already upscaled and exists (and not force_refresh)
    if not force_refresh and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        try:
            with Image.open(filepath) as cached_img:
                cw, ch = cached_img.size
                if max(cw, ch) >= 1000 and min(cw, ch) >= 500:
                    if bytes_out is not None and public_url not in bytes_out:
                        try:
                            with open(filepath, "rb") as f:
                                bytes_out[public_url] = f.read()
                        except Exception:
                            pass
                    return public_url
        except Exception:
            pass

    # If local mirrored file
    if image_url.startswith("/static/mirrored/") or "/static/mirrored/" in image_url:
        existing_fname = os.path.basename(image_url.split("?")[0])
        existing_path = os.path.join(STATIC_DIR, existing_fname)
        if os.path.exists(existing_path) and os.path.getsize(existing_path) > 0:
            try:
                with Image.open(existing_path) as loc_img:
                    w, h = loc_img.size
                    if max(w, h) < 1000 or min(w, h) < 500:
                        loc_img = loc_img.convert("RGB")
                        upscaled = ensure_image_min_dimensions(loc_img)
                        upscaled.save(existing_path, format="JPEG", quality=90)
                return image_url
            except Exception as e:
                logging.warning(f"Error validating local image {existing_path}: {e}")
                return image_url

    # Check remote image
    raw_bytes = await fetch_image_bytes(image_url, client=client)
    if not raw_bytes:
        return image_url

    try:
        img = Image.open(io.BytesIO(raw_bytes))
        w, h = img.size
        if max(w, h) >= 1000 and min(w, h) >= 500 and not force_refresh:
            # Dimension is valid
            return image_url

        # Dimension is too small or force_refresh -> upscale
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        upscaled_img = ensure_image_min_dimensions(img)

        buf = io.BytesIO()
        upscaled_img.save(buf, format="JPEG", quality=90)
        upscaled_bytes = buf.getvalue()

        content_hash = hashlib.md5(upscaled_bytes).hexdigest()[:10]
        content_filename = f"mirrored_upscaled_{clean_ean}_{content_hash}.jpg"
        content_filepath = os.path.join(STATIC_DIR, content_filename)
        content_public_url = f"/static/mirrored/{content_filename}"

        with open(content_filepath, "wb") as f:
            f.write(upscaled_bytes)

        if filepath != content_filepath:
            try:
                with open(filepath, "wb") as f:
                    f.write(upscaled_bytes)
            except Exception:
                pass

        if bytes_out is not None:
            bytes_out[content_public_url] = upscaled_bytes
            bytes_out[public_url] = upscaled_bytes

        logging.info(f"Upscaled image for EAN {ean} from {w}x{h} to {upscaled_img.size[0]}x{upscaled_img.size[1]}")
        return content_public_url
    except Exception as e:
        logging.warning(f"Failed to check/upscale image {image_url} for EAN {ean}: {e}")
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
    max_concurrency: int = 60,
    timeout: float = 8.0,
    max_remote: int = 99999,
    progress_callback: Any = None,
    preloaded_bytes: dict[str, bytes] = None
) -> dict[str, bytes]:
    """
    Concurrently fetches image bytes for a list of URLs with deduplication.
    Local files, preloaded in-memory bytes, and cached files are loaded instantly.
    Remote HTTP URLs are downloaded concurrently up to max_concurrency with automatic retry.
    Calls progress_callback(completed, total) if provided.
    Returns a dictionary mapping url -> bytes.
    """
    unique_urls = list(set([u for u in urls if u and isinstance(u, str) and u.strip()]))
    if not unique_urls:
        return {}

    results: dict[str, bytes] = {}
    if preloaded_bytes:
        for u in unique_urls:
            if u in preloaded_bytes and preloaded_bytes[u]:
                results[u] = preloaded_bytes[u]

    remote_urls = []

    # 1. Check local files and disk cache first
    for u in unique_urls:
        if u in results:
            continue
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
