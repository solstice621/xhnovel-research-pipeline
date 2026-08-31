from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from .canonical import canonical_dumps
from .errors import ValidationError
from .hashing import object_hash

OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_MODEL_RESPONSE_BYTES = 4_000_000

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _default_transport(
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(MAX_MODEL_RESPONSE_BYTES + 1)
            if len(data) > MAX_MODEL_RESPONSE_BYTES:
                raise ValidationError("E-MODEL-RESPONSE-SIZE", "model response exceeds size limit")
            return response.status, dict(response.headers.items()), data
    except urllib.error.HTTPError as exc:
        data = exc.read(MAX_MODEL_RESPONSE_BYTES + 1)
        return exc.code, dict(exc.headers.items()), data
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise ValidationError("E-MODEL-UNREACHABLE", f"model request failed: {exc}") from exc


@dataclass(frozen=True)
class ModelCallResult:
    value: dict[str, Any]
    request_bytes: bytes
    response_bytes: bytes
    response_id: str | None


class OpenAIResponsesClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        endpoint: str = OPENAI_RESPONSES_ENDPOINT,
        timeout: float = 60.0,
        max_attempts: int = 3,
        transport: Transport | None = None,
        allow_custom_endpoint: bool = False,
    ) -> None:
        if not model.strip():
            raise ValidationError("E-MODEL-CONFIG", "model is required")
        if timeout <= 0 or max_attempts < 1:
            raise ValidationError("E-MODEL-CONFIG", "timeout and max_attempts must be positive")
        if not isinstance(allow_custom_endpoint, bool):
            raise ValidationError("E-MODEL-CONFIG", "allow_custom_endpoint must be a boolean")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValidationError("E-MODEL-ENDPOINT", "model endpoint must be HTTPS")
        if not allow_custom_endpoint and endpoint != OPENAI_RESPONSES_ENDPOINT:
            raise ValidationError("E-MODEL-ENDPOINT", "custom model endpoint requires explicit opt-in")
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValidationError("E-MODEL-CREDENTIAL", "OPENAI_API_KEY is not set")
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.transport = transport or _default_transport

    def generate_json(
        self,
        *,
        instructions: str,
        input_value: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> ModelCallResult:
        request_payload = {
            "model": self.model,
            "instructions": instructions,
            "input": canonical_dumps(input_value).decode("utf-8"),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "store": False,
        }
        request_bytes = canonical_dumps(request_payload)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response_bytes = b""
        status = 0
        for attempt in range(1, self.max_attempts + 1):
            status, _, response_bytes = self.transport(
                self.endpoint, headers, request_bytes, self.timeout
            )
            if len(response_bytes) > MAX_MODEL_RESPONSE_BYTES:
                raise ValidationError("E-MODEL-RESPONSE-SIZE", "model response exceeds size limit")
            if status not in RETRYABLE_STATUSES or attempt == self.max_attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 4))
        if status < 200 or status >= 300:
            code = "E-MODEL-RETRYABLE" if status in RETRYABLE_STATUSES else "E-MODEL-HTTP"
            raise ValidationError(code, f"model API returned HTTP {status}")
        try:
            response = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("E-MODEL-RESPONSE", "model API returned invalid JSON") from exc
        output_text = _response_output_text(response)
        try:
            value = json.loads(output_text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("E-MODEL-OUTPUT", "model output is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ValidationError("E-MODEL-OUTPUT", "model output must be an object")
        return ModelCallResult(
            value=value,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            response_id=response.get("id") if isinstance(response, dict) else None,
        )


def _response_output_text(response: Any) -> str:
    if not isinstance(response, dict):
        raise ValidationError("E-MODEL-RESPONSE", "model response must be an object")
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for output in response.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise ValidationError("E-MODEL-REFUSAL", str(content.get("refusal") or "model refused"))
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise ValidationError("E-MODEL-RESPONSE", "model response contains no output_text")


def model_build_id(*, purpose: str, model: str, instructions: str, parameters: dict[str, Any]) -> str:
    digest = object_hash(
        {
            "purpose": purpose,
            "provider": "openai-responses",
            "model": model,
            "instructions": instructions,
            "parameters": parameters,
        },
        omit=(),
    ).removeprefix("sha256:")
    return f"{purpose}-{digest[:20]}"
