"""Dimension verification and lossless-ratio LANCZOS upscaling for OTTO images."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import io
import ipaddress
import os
from pathlib import Path
import re
import socket
import tempfile
from typing import Any, Callable, Mapping, Sequence
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

MIN_LONG_SIDE = 1000
MIN_SHORT_SIDE = 500
OTTO_HARD_MIN_LONG = 960
OTTO_HARD_MIN_SHORT = 480
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_REDIRECTS = 3
DEFAULT_TIMEOUT_SECONDS = 12.0
PUBLIC_MEDIA_FILENAME_RE = re.compile(r"^upscaled_(JV|XL)_[a-f0-9]{12}_[a-f0-9]{12}\.jpg$")
PUBLIC_MEDIA_SUBPATH = "/restart/media"
IMAGE_NORMALIZER_SYSTEM = "OttoRestart"
IMAGE_NORMALIZER_METHOD = "IMAGE_DIMENSION_NORMALIZER"
IMAGE_NORMALIZER_RESOURCE = "upscaled-image"
IMAGE_NORMALIZER_SOURCE_REF = f"{IMAGE_NORMALIZER_SYSTEM}:{IMAGE_NORMALIZER_RESOURCE}"


class ImageNormalizationError(RuntimeError):
    """Fail-closed image validation or normalization failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class NormalizedImageResult:
    source_url: str
    normalized_url: str
    upscaled: bool
    original_size: tuple[int, int]
    final_size: tuple[int, int]
    sha256: str | None = None
    filename: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceUrl": self.source_url,
            "normalizedUrl": self.normalized_url,
            "upscaled": self.upscaled,
            "originalSize": [self.original_size[0], self.original_size[1]],
            "finalSize": [self.final_size[0], self.final_size[1]],
            "sha256": self.sha256,
            "filename": self.filename,
        }


def _pillow():
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as error:
        raise ImageNormalizationError(
            "IMAGE_PROCESSOR_UNAVAILABLE",
            "Pillow is required for automatic image normalization.",
        ) from error
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    return Image, ImageOps, UnidentifiedImageError


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_remote_image_url(url: str) -> str:
    cleaned = str(url or "").strip()
    parts = urlsplit(cleaned)
    hostname = (parts.hostname or "").strip().casefold()
    if (
        parts.scheme != "https"
        or not hostname
        or parts.username
        or parts.password
        or hostname in {"localhost"}
        or hostname.endswith((".local", ".internal", ".localhost"))
    ):
        raise ImageNormalizationError(
            "IMAGE_URL_INVALID",
            "Only public HTTPS image URLs without credentials are allowed.",
        )
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if _is_forbidden_ip(ip):
            raise ImageNormalizationError("IMAGE_URL_FORBIDDEN", "Private or loopback image hosts are forbidden.")
    return cleaned


def _verify_resolved_host_is_public(hostname: str, port: int | None) -> None:
    try:
        infos = socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ImageNormalizationError(
            "IMAGE_SOURCE_UNAVAILABLE",
            "Image host DNS resolution failed.",
            retryable=True,
        ) from error
    if not infos:
        raise ImageNormalizationError("IMAGE_SOURCE_UNAVAILABLE", "Image host resolved no addresses.", retryable=True)
    for info in infos:
        addr = info[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_forbidden_ip(ip):
            raise ImageNormalizationError("IMAGE_URL_FORBIDDEN", "Resolved image IP address is private or loopback.")


def _fetch_remote_image_bytes(url: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    current = validate_remote_image_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        _verify_resolved_host_is_public(parts.hostname or "", parts.port)
        request = urllib.request.Request(
            current,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; OttoRestartMediaCheck/1.0)",
                "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8",
            },
            method="GET",
        )
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                status = getattr(response, "status", response.getcode())
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise ImageNormalizationError("IMAGE_SOURCE_UNAVAILABLE", "Image redirect lacked a target.", retryable=True)
                    current = validate_remote_image_url(urljoin(current, location))
                    continue
                if status != 200:
                    retryable = status in {408, 429, 500, 502, 503, 504}
                    raise ImageNormalizationError(
                        "IMAGE_SOURCE_UNAVAILABLE",
                        f"Source image returned HTTP {status}.",
                        retryable=retryable,
                    )
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(65_536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        raise ImageNormalizationError("IMAGE_TOO_LARGE", "Source image exceeds maximum allowed size.")
                    chunks.append(chunk)
                data = b"".join(chunks)
                if not data:
                    raise ImageNormalizationError("IMAGE_SOURCE_EMPTY", "Source image returned empty bytes.", retryable=True)
                return data
        except urllib.error.HTTPError as error:
            if error.code in {301, 302, 303, 307, 308}:
                location = error.headers.get("Location") if error.headers else None
                if not location:
                    raise ImageNormalizationError("IMAGE_SOURCE_UNAVAILABLE", "Image redirect lacked a target.", retryable=True) from None
                current = validate_remote_image_url(urljoin(current, location))
                continue
            retryable = error.code in {408, 429, 500, 502, 503, 504}
            raise ImageNormalizationError(
                "IMAGE_SOURCE_UNAVAILABLE",
                f"Source image returned HTTP {error.code}.",
                retryable=retryable,
            ) from None
        except urllib.error.URLError as error:
            raise ImageNormalizationError(
                "IMAGE_SOURCE_UNAVAILABLE",
                "Source image download failed.",
                retryable=True,
            ) from error
        except TimeoutError as error:
            raise ImageNormalizationError(
                "IMAGE_SOURCE_UNAVAILABLE",
                "Source image download timed out.",
                retryable=True,
            ) from error
    raise ImageNormalizationError("IMAGE_REDIRECT_LIMIT", "Too many redirects while fetching source image.")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _flatten_to_rgb(img: Any, image_cls: Any) -> Any:
    if img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = image_cls.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def ensure_image_min_dimensions(
    img: Any,
    *,
    min_long_side: int = MIN_LONG_SIDE,
    min_short_side: int = MIN_SHORT_SIDE,
    target_long_side: int = MIN_LONG_SIDE,
    target_short_side: int = MIN_SHORT_SIDE,
) -> tuple[Any, bool, tuple[int, int]]:
    """Return (image, upscaled, original_size). Leave compliant images untouched."""
    image_cls, image_ops, _ = _pillow()
    oriented = image_ops.exif_transpose(img)
    w, h = oriented.size
    if w <= 0 or h <= 0:
        raise ImageNormalizationError("IMAGE_DIMENSIONS_INVALID", "Image has zero width or height.")
    if max(w, h) >= min_long_side and min(w, h) >= min_short_side:
        return oriented, False, (w, h)

    scale = max(target_long_side / max(w, h), target_short_side / min(w, h))
    new_w = max(int(round(w * scale)), min_long_side if w >= h else min_short_side)
    new_h = max(int(round(h * scale)), min_long_side if h > w else min_short_side)
    rgb = _flatten_to_rgb(oriented, image_cls)
    resized = rgb.resize((new_w, new_h), image_cls.Resampling.LANCZOS)
    return resized, True, (w, h)


def build_upscaled_filename(account: str, source_url: str, content_sha256: str) -> str:
    normalized_account = str(account or "").strip().upper()
    if normalized_account not in {"JV", "XL"}:
        raise ImageNormalizationError("INVALID_ACCOUNT", "Account must be JV or XL.")
    url_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:12]
    content_hash = str(content_sha256 or "").strip().casefold()[:12]
    filename = f"upscaled_{normalized_account}_{url_hash}_{content_hash}.jpg"
    if not PUBLIC_MEDIA_FILENAME_RE.fullmatch(filename):
        raise ImageNormalizationError("IMAGE_FILENAME_INVALID", "Generated image filename violates safe format.")
    return filename


def _write_bytes_atomically(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size == len(data):
        return
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_upscale_", suffix=".jpg", dir=str(destination.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, destination)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def normalize_single_image(
    *,
    account: str,
    source_url: str,
    public_base_url: str,
    storage_dir: str | Path,
    fetcher: Callable[[str], bytes] | None = None,
    min_long_side: int = MIN_LONG_SIDE,
    min_short_side: int = MIN_SHORT_SIDE,
) -> NormalizedImageResult:
    normalized_account = str(account or "").strip().upper()
    if normalized_account not in {"JV", "XL"}:
        raise ImageNormalizationError("INVALID_ACCOUNT", "Account must be JV or XL.")
    clean_url = validate_remote_image_url(source_url)
    base = str(public_base_url or "").strip().rstrip("/")
    parsed_base = urlsplit(base)
    if parsed_base.scheme != "https" or not parsed_base.hostname:
        raise ImageNormalizationError(
            "IMAGE_PUBLIC_BASE_URL_REQUIRED",
            "An explicit HTTPS OTTORESTART_PUBLIC_BASE_URL is required for image normalization.",
        )

    read_bytes = fetcher
    storage_root = Path(storage_dir)
    own_prefix = f"https://{parsed_base.netloc}{PUBLIC_MEDIA_SUBPATH}/"
    if read_bytes is None and clean_url.startswith(own_prefix):
        existing_name = clean_url[len(own_prefix):]
        if PUBLIC_MEDIA_FILENAME_RE.fullmatch(existing_name):
            local_candidate = storage_root / existing_name
            if local_candidate.is_file():
                raw_bytes = local_candidate.read_bytes()
            else:
                raw_bytes = _fetch_remote_image_bytes(clean_url)
        else:
            raw_bytes = _fetch_remote_image_bytes(clean_url)
    else:
        raw_bytes = (read_bytes or _fetch_remote_image_bytes)(clean_url)
    if not raw_bytes or len(raw_bytes) > MAX_IMAGE_BYTES:
        raise ImageNormalizationError("IMAGE_SOURCE_INVALID", "Downloaded image is empty or exceeds size limit.")

    image_cls, _, unidentified_error = _pillow()
    try:
        with image_cls.open(io.BytesIO(raw_bytes)) as opened:
            opened.load()
            processed, upscaled, orig_size = ensure_image_min_dimensions(
                opened,
                min_long_side=min_long_side,
                min_short_side=min_short_side,
                target_long_side=min_long_side,
                target_short_side=min_short_side,
            )
    except ImageNormalizationError:
        raise
    except (unidentified_error, OSError, ValueError, image_cls.DecompressionBombError) as error:
        raise ImageNormalizationError("IMAGE_CORRUPT_OR_UNREADABLE", "Source URL did not return a readable image.") from error

    if not upscaled:
        return NormalizedImageResult(
            source_url=clean_url,
            normalized_url=clean_url,
            upscaled=False,
            original_size=orig_size,
            final_size=orig_size,
        )

    final_size = processed.size
    buf = io.BytesIO()
    processed.save(buf, format="JPEG", quality=90, optimize=True)
    jpeg_bytes = buf.getvalue()
    sha256 = hashlib.sha256(jpeg_bytes).hexdigest()
    filename = build_upscaled_filename(normalized_account, clean_url, sha256)
    _write_bytes_atomically(storage_root / filename, jpeg_bytes)
    normalized_url = f"https://{parsed_base.netloc}{PUBLIC_MEDIA_SUBPATH}/{filename}"
    return NormalizedImageResult(
        source_url=clean_url,
        normalized_url=normalized_url,
        upscaled=True,
        original_size=orig_size,
        final_size=(int(final_size[0]), int(final_size[1])),
        sha256=sha256,
        filename=filename,
    )


def normalize_candidate_images(
    *,
    account: str,
    urls: Sequence[str],
    public_base_url: str,
    storage_dir: str | Path,
    fetcher: Callable[[str], bytes] | None = None,
    min_long_side: int = MIN_LONG_SIDE,
    min_short_side: int = MIN_SHORT_SIDE,
    max_workers: int = 4,
) -> dict[str, dict[str, Any]]:
    """Inspect candidate image URLs and upscale only those below OTTO minimum dimensions."""
    unique_urls: list[str] = []
    seen: set[str] = set()
    for url in urls:
        clean = str(url or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        unique_urls.append(clean)
    if not unique_urls:
        return {}

    def _run_one(target_url: str) -> NormalizedImageResult:
        return normalize_single_image(
            account=account,
            source_url=target_url,
            public_base_url=public_base_url,
            storage_dir=storage_dir,
            fetcher=fetcher,
            min_long_side=min_long_side,
            min_short_side=min_short_side,
        )

    results: dict[str, dict[str, Any]] = {}
    worker_count = max(1, min(max_workers, len(unique_urls)))
    if worker_count == 1:
        for u in unique_urls:
            item = _run_one(u)
            results[item.source_url] = item.as_dict()
        return results

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        for item in pool.map(_run_one, unique_urls):
            results[item.source_url] = item.as_dict()
    return results
