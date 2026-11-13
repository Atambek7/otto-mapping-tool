"""Minimal, secret-safe OpenAI Responses API transport for OttoRestart.

The endpoint and host are fixed to api.openai.com. The adapter is inert until
called and has no SDK or process-wide client side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
import re
import socket
import ssl
from typing import Callable, Mapping
from urllib.parse import urlsplit


OPENAI_HOST = "api.openai.com"
OPENAI_PATH = "/v1/responses"
MAX_RESPONSE_BYTES = 1_000_000
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class OpenAIClientError(RuntimeError):
    """Stable error code without provider response or credential details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: int = 30
    max_output_tokens: int = 1200
    max_input_bytes: int = 32_000

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "OpenAISettings":
        api_key = environment.get("OTTORESTART_OPENAI_API_KEY", "").strip()
        model = environment.get("OTTORESTART_OPENAI_MODEL", "").strip()
        if not api_key:
            raise OpenAIClientError("OPENAI_API_KEY_NOT_CONFIGURED")
        if not _MODEL.fullmatch(model):
            raise OpenAIClientError("OPENAI_MODEL_NOT_CONFIGURED")
        return cls(
            api_key=api_key,
            model=model,
            timeout_seconds=_positive_int(environment, "OTTORESTART_OPENAI_TIMEOUT_SECONDS", 30, 1, 120),
            max_output_tokens=_positive_int(environment, "OTTORESTART_OPENAI_MAX_OUTPUT_TOKENS", 1200, 1, 4096),
            max_input_bytes=_positive_int(environment, "OTTORESTART_OPENAI_MAX_INPUT_BYTES", 32_000, 512, 256_000),
        )


def _positive_int(environment: Mapping[str, str], name: str, default: int, lower: int, upper: int) -> int:
    raw = environment.get(name, str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise OpenAIClientError("OPENAI_CONFIGURATION_INVALID") from None
    if not lower <= value <= upper:
        raise OpenAIClientError("OPENAI_CONFIGURATION_INVALID")
    return value


def configuration_status(environment: Mapping[str, str], *, enabled: bool) -> dict[str, object]:
    """Return UI-safe configuration state; never return key material."""
    try:
        settings = OpenAISettings.from_environment(environment)
    except OpenAIClientError as error:
        state = "NOT_CONFIGURED" if error.code.endswith("NOT_CONFIGURED") else "INVALID"
        return {"enabled": enabled, "configured": False, "available": False, "state": state, "model": None}
    return {
        "enabled": enabled,
        "configured": True,
        "available": bool(enabled),
        "state": "READY" if enabled else "DISABLED",
        "model": settings.model,
    }


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: bytes


HTTPTransport = Callable[[str, str, Mapping[str, str], bytes, int], HTTPResponse]


def _https_transport(
    host: str, path: str, headers: Mapping[str, str], body: bytes, timeout_seconds: int
) -> HTTPResponse:
    """Make one pinned HTTPS request. No redirects or automatic retries."""
    if host != OPENAI_HOST or path != OPENAI_PATH:
        raise OpenAIClientError("OPENAI_ENDPOINT_NOT_ALLOWED")
    connection = http.client.HTTPSConnection(host, timeout=timeout_seconds, context=ssl.create_default_context())
    try:
        connection.request("POST", path, body=body, headers=dict(headers))
        response = connection.getresponse()
        result = response.read(MAX_RESPONSE_BYTES + 1)
        if len(result) > MAX_RESPONSE_BYTES:
            raise OpenAIClientError("OPENAI_RESPONSE_TOO_LARGE")
        return HTTPResponse(status=response.status, body=result)
    except (socket.timeout, TimeoutError):
        raise OpenAIClientError("OPENAI_TIMEOUT") from None
    except OpenAIClientError:
        raise
    except OSError:
        raise OpenAIClientError("OPENAI_TRANSPORT_ERROR") from None
    finally:
        connection.close()


class OpenAIResponsesClient:
    def __init__(
        self, settings: OpenAISettings, *, enabled: bool = False, transport: HTTPTransport | None = None
    ) -> None:
        self.settings = settings
        self._enabled = enabled
        self._transport = transport or _https_transport

    def create_structured_response(
        self,
        *,
        instructions: str,
        input_data: Mapping[str, object],
        schema_name: str,
        schema: Mapping[str, object],
    ) -> str:
        if not self._enabled:
            raise OpenAIClientError("OPENAI_DISABLED")
        if not isinstance(instructions, str) or not instructions.strip():
            raise OpenAIClientError("OPENAI_INVALID_REQUEST")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", schema_name):
            raise OpenAIClientError("OPENAI_INVALID_REQUEST")
        try:
            safe_input = dict(input_data)
            image_url = safe_input.pop("__image_url", None)
            input_text = json.dumps(safe_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            if image_url is not None:
                parsed_image = urlsplit(image_url) if isinstance(image_url, str) else None
                if (parsed_image is None or parsed_image.scheme != "https" or not parsed_image.hostname
                        or parsed_image.username or parsed_image.password or len(image_url) > 2048):
                    raise OpenAIClientError("OPENAI_IMAGE_URL_INVALID")
                request_input: object = [{"role": "user", "content": [
                    {"type": "input_text", "text": input_text},
                    {"type": "input_image", "image_url": image_url},
                ]}]
            else:
                request_input = input_text
            request_body = json.dumps(
                {
                    "model": self.settings.model,
                    "instructions": instructions,
                    "input": request_input,
                    "max_output_tokens": self.settings.max_output_tokens,
                    "store": False,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": schema_name,
                            "strict": True,
                            "schema": schema,
                        }
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise OpenAIClientError("OPENAI_INVALID_REQUEST") from None
        if len(request_body) > self.settings.max_input_bytes:
            raise OpenAIClientError("OPENAI_INPUT_BUDGET_EXCEEDED")

        try:
            response = self._transport(
                OPENAI_HOST,
                OPENAI_PATH,
                {
                    "Authorization": f"Bearer {self.settings.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Connection": "close",
                },
                request_body,
                self.settings.timeout_seconds,
            )
        except OpenAIClientError:
            raise
        except (socket.timeout, TimeoutError):
            raise OpenAIClientError("OPENAI_TIMEOUT") from None
        except Exception:
            # Provider/transport exception strings can contain URLs or request data.
            raise OpenAIClientError("OPENAI_TRANSPORT_ERROR") from None
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise OpenAIClientError("OPENAI_RESPONSE_TOO_LARGE")
        if not 200 <= response.status < 300:
            raise OpenAIClientError("OPENAI_HTTP_ERROR")
        try:
            document = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OpenAIClientError("OPENAI_INVALID_RESPONSE") from None
        if not isinstance(document, dict):
            raise OpenAIClientError("OPENAI_INVALID_RESPONSE")
        status = document.get("status")
        if status == "incomplete":
            raise OpenAIClientError("OPENAI_INCOMPLETE_RESPONSE")
        if status != "completed":
            raise OpenAIClientError("OPENAI_INVALID_RESPONSE")

        fragments: list[str] = []
        output = document.get("output")
        if not isinstance(output, list):
            raise OpenAIClientError("OPENAI_INVALID_RESPONSE")
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "refusal":
                    raise OpenAIClientError("OPENAI_REFUSED")
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    fragments.append(part["text"])
        if isinstance(document.get("output_text"), str) and document["output_text"].strip():
            return document["output_text"]
        if not fragments or not "".join(fragments).strip():
            raise OpenAIClientError("OPENAI_OUTPUT_MISSING")
        return "".join(fragments)
