"""Explicit AI suggestions plus a deterministic, no-truncation fallback."""

from __future__ import annotations

import os
from typing import Mapping

from services.title_policy import MAX_PREPARED_TITLE_CODEPOINTS, prepare_title

MAX_TITLE_LENGTH = MAX_PREPARED_TITLE_CODEPOINTS


def generate_optimized_title(
    original_title: str,
    category: str = "",
    brand: str = "",
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    env = os.environ if environment is None else environment
    openai_key = env.get("OTTORESTART_OPENAI_API_KEY", "").strip()
    openai_model = env.get("OTTORESTART_OPENAI_MODEL", "gpt-4o-mini").strip()
    openai_enabled = env.get("OTTORESTART_OPENAI_ENABLED", "true").strip().lower() in {"true", "1", "yes"}

    if openai_enabled and openai_key:
        try:
            import json
            import urllib.request

            prompt = (
                f"You are an expert e-commerce copywriter for OTTO Market (Germany). "
                f"Given the original product title, generate a clear, optimized German product title "
                f"within OttoRestart's internal 70-code-point prepared-title policy. "
                f"Do not include quotes or explanation. Return ONLY the new title.\n\n"
                f"Original: {original_title}\n"
                f"Category: {category}\n"
                f"Brand: {brand}"
            )
            req_data = json.dumps({
                "model": openai_model,
                "messages": [
                    {"role": "system", "content": "You propose reviewed German product titles within OttoRestart's internal 70-code-point policy."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 100,
                "temperature": 0.3,
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {openai_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                choice = resp_json["choices"][0]["message"]["content"].strip().strip('"\'')
                if 1 <= len(choice) <= MAX_TITLE_LENGTH and choice.casefold() != original_title.casefold():
                    return choice
        except Exception:
            # The deterministic path below is local-only. Never cut a title to
            # a length that could remove a model or variant identifier.
            pass

    return prepare_title(original_title or "")["title"]
