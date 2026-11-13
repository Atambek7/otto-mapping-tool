"""Internal policy for operator/prepared OttoRestart product titles.

The 70-code-point limit is an OttoRestart preparation rule. Source titles are
kept unchanged; this module only produces or validates a separate target title.
"""

from __future__ import annotations

import re
from typing import Any

MAX_PREPARED_TITLE_CODEPOINTS = 70

_LOW_SIGNAL_TERMS = {
    "design", "luxus", "luxury", "neu", "new", "exklusiv", "hochwertig",
}
_SHAPE_TERMS = {
    "sofa", "couch", "sofas", "couches", "sofagarnitur", "sofaset",
    "ohrensessel", "sessel", "garnitur", "polsterstuhl", "stuhl",
}


class PreparedTitleError(ValueError):
    def __init__(self, code: str, message: str, *, length: int, limit: int = MAX_PREPARED_TITLE_CODEPOINTS):
        self.code = code
        self.length = length
        self.limit = limit
        super().__init__(message)


def normalize_title_whitespace(value: str) -> str:
    """Collapse whitespace without truncating or changing source storage."""
    return re.sub(r"\s+", " ", value, flags=re.UNICODE).strip()


def title_codepoint_length(value: str) -> int:
    """Python len() counts Unicode code points, matching PostgreSQL char_length."""
    return len(value)


def validate_prepared_title(value: Any, *, limit: int = MAX_PREPARED_TITLE_CODEPOINTS) -> str:
    if not isinstance(value, str):
        raise PreparedTitleError("PREPARED_TITLE_MISSING", "A prepared product title is required.", length=0, limit=limit)
    normalized = normalize_title_whitespace(value)
    length = title_codepoint_length(normalized)
    if not normalized:
        raise PreparedTitleError("PREPARED_TITLE_MISSING", "A prepared product title is required.", length=length, limit=limit)
    if length > limit:
        raise PreparedTitleError(
            "PREPARED_TITLE_TOO_LONG",
            f"Prepared title has {length} Unicode code points; OttoRestart allows at most {limit}.",
            length=length,
            limit=limit,
        )
    return normalized


def _token_key(token: str) -> str:
    return re.sub(r"^\W+|\W+$", "", token, flags=re.UNICODE).casefold()


def _is_identity_token(token: str) -> bool:
    return bool(re.search(r"\d", token)) or token.startswith(("#", "@")) or (len(token) > 1 and token.isupper())


def _safe_compression_candidate(source: str) -> str:
    tokens = re.findall(r"\S+", source, flags=re.UNICODE)
    keys = [_token_key(token) for token in tokens]
    has_sofa_compound = "sofagarnitur" in keys or "sofaset" in keys
    has_product_shape = any(key in _SHAPE_TERMS for key in keys)
    kept: list[str] = []
    seen: set[str] = set()
    seen_sofa_synonym = False

    for token, key in zip(tokens, keys):
        if not key:
            continue
        if key in _LOW_SIGNAL_TERMS:
            continue
        if key == "polster" and has_product_shape:
            continue
        if has_sofa_compound and key in {"sofa", "garnitur"}:
            continue
        if key in {"sofa", "couch", "sofas", "couches"}:
            if seen_sofa_synonym:
                continue
            seen_sofa_synonym = True
        if key in seen and not _is_identity_token(token):
            continue
        kept.append(token)
        seen.add(key)

    return normalize_title_whitespace(" ".join(kept))


def prepare_title(source_title: str, manual_title: str | None = None, *, limit: int = MAX_PREPARED_TITLE_CODEPOINTS) -> dict[str, Any]:
    """Prefer an explicit changed operator title; otherwise safely compact source text.

    The compressor removes only a small allowlist of marketing filler and
    redundant product-shape words. It never slices a token or drops numeric,
    model, size, or variant identifiers. If those conservative rules cannot
    produce a valid title, preparation fails closed.
    """
    normalized_source = normalize_title_whitespace(source_title if isinstance(source_title, str) else "")
    normalized_manual = normalize_title_whitespace(manual_title) if isinstance(manual_title, str) else ""
    if normalized_manual and normalized_manual != normalized_source:
        prepared = validate_prepared_title(normalized_manual, limit=limit)
        return {
            "title": prepared,
            "sourceLength": title_codepoint_length(normalized_source),
            "preparedLength": title_codepoint_length(prepared),
            "strategy": "OPERATOR_EDIT",
        }

    if not normalized_source:
        raise PreparedTitleError("PREPARED_TITLE_MISSING", "The source title is empty.", length=0, limit=limit)
    source_length = title_codepoint_length(normalized_source)
    if source_length <= limit:
        return {"title": normalized_source, "sourceLength": source_length,
                "preparedLength": source_length, "strategy": "UNCHANGED"}

    candidate = _safe_compression_candidate(normalized_source)
    candidate_length = title_codepoint_length(candidate)
    if candidate and candidate_length <= limit:
        return {"title": candidate, "sourceLength": source_length,
                "preparedLength": candidate_length, "strategy": "SAFE_COMPRESSION"}
    raise PreparedTitleError(
        "TITLE_CANNOT_FIT_SAFELY",
        f"The source title has {source_length} code points and cannot be safely reduced to {limit} without dropping product details.",
        length=source_length,
        limit=limit,
    )
