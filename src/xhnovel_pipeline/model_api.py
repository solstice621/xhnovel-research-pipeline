from __future__ import annotations

import errno
import http.client
import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from .canonical import canonical_dumps
from .constants import MODEL_EXECUTOR_BUILD_ID
from .errors import ValidationError
from .hashing import object_hash

OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_MODEL_RESPONSE_BYTES = 4_000_000

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]

API_EXECUTOR_KIND = "API"
OPENAI_RESPONSES_FORMAT = "OPENAI_RESPONSES"


def _retryable_transport_error(error: BaseException) -> bool:
    if isinstance(error, ValidationError):
        if error.code != "E-MODEL-UNREACHABLE":
            return False
        return error.__cause__ is None or _retryable_transport_error(error.__cause__)
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, BaseException) and _retryable_transport_error(error.reason)
    if isinstance(error, (TimeoutError, ConnectionError, http.client.IncompleteRead)):
        return True
    return isinstance(error, OSError) and error.errno in {
        errno.EAGAIN,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        errno.ETIMEDOUT,
        socket.EAI_AGAIN,
    }


class SceneScoutExecutor(Protocol):
    model: str
    endpoint: str
    timeout: float
    max_attempts: int
    executor_kind: str
    response_format: str
    executor_build_id: str

    def json_request_bytes(
        self,
        *,
        instructions: str,
        input_value: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> bytes: ...

    def generate_json(
        self,
        *,
        instructions: str,
        input_value: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> "ModelCallResult": ...


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
class ModelAttemptTrace:
    ordinal: int
    status: str
    http_status: int | None
    response_bytes: bytes
    error_code: str | None
    error_message: str | None
    response_id: str | None
    usage: dict[str, int | None]


class ModelCallError(ValidationError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        request_bytes: bytes,
        attempts: tuple[ModelAttemptTrace, ...],
    ) -> None:
        self.request_bytes = request_bytes
        self.attempts = attempts
        super().__init__(code, message)


@dataclass(frozen=True)
class ModelCallResult:
    value: dict[str, Any]
    request_bytes: bytes
    response_bytes: bytes
    response_id: str | None
    attempts: tuple[ModelAttemptTrace, ...]


def _response_usage(response: Any) -> dict[str, int | None]:
    raw = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(raw, dict):
        raw = {}
    result: dict[str, int | None] = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        value = raw.get(field)
        result[field] = value if isinstance(value, int) and not isinstance(value, bool) else None
    return result


class OpenAIResponsesClient:
    executor_kind = API_EXECUTOR_KIND
    response_format = OPENAI_RESPONSES_FORMAT
    executor_build_id = MODEL_EXECUTOR_BUILD_ID

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

    def json_request_bytes(
        self,
        *,
        instructions: str,
        input_value: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> bytes:
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
        return canonical_dumps(request_payload)

    def generate_json(
        self,
        *,
        instructions: str,
        input_value: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> ModelCallResult:
        request_bytes = self.json_request_bytes(
            instructions=instructions,
            input_value=input_value,
            schema_name=schema_name,
            schema=schema,
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        attempts: list[ModelAttemptTrace] = []
        for attempt in range(1, self.max_attempts + 1):
            try:
                status, _, response_bytes = self.transport(
                    self.endpoint, headers, request_bytes, self.timeout
                )
            except (ValidationError, OSError, urllib.error.URLError, http.client.IncompleteRead) as exc:
                code = getattr(exc, "code", "E-MODEL-UNREACHABLE")
                retryable = _retryable_transport_error(exc) and attempt < self.max_attempts
                attempts.append(
                    ModelAttemptTrace(
                        ordinal=attempt,
                        status="RETRYABLE" if retryable else "FAILED",
                        http_status=None,
                        response_bytes=b"",
                        error_code=code,
                        error_message=str(exc),
                        response_id=None,
                        usage=_response_usage(None),
                    )
                )
                if retryable:
                    time.sleep(min(2 ** (attempt - 1), 4))
                    continue
                raise ModelCallError(
                    code,
                    "model request failed before receiving a response",
                    request_bytes=request_bytes,
                    attempts=tuple(attempts),
                ) from exc
            if len(response_bytes) > MAX_MODEL_RESPONSE_BYTES:
                code = "E-MODEL-RESPONSE-SIZE"
                attempts.append(
                    ModelAttemptTrace(
                        attempt,
                        "FAILED",
                        status,
                        response_bytes,
                        code,
                        "model response exceeds size limit",
                        None,
                        _response_usage(None),
                    )
                )
                raise ModelCallError(
                    code,
                    "model response exceeds size limit",
                    request_bytes=request_bytes,
                    attempts=tuple(attempts),
                )
            if status < 200 or status >= 300:
                retryable = status in RETRYABLE_STATUSES
                code = "E-MODEL-RETRYABLE" if retryable else "E-MODEL-HTTP"
                attempts.append(
                    ModelAttemptTrace(
                        attempt,
                        "RETRYABLE" if retryable and attempt < self.max_attempts else "FAILED",
                        status,
                        response_bytes,
                        code,
                        f"model API returned HTTP {status}",
                        None,
                        _response_usage(None),
                    )
                )
                if retryable and attempt < self.max_attempts:
                    time.sleep(min(2 ** (attempt - 1), 4))
                    continue
                raise ModelCallError(
                    code,
                    f"model API returned HTTP {status}",
                    request_bytes=request_bytes,
                    attempts=tuple(attempts),
                )
            response: Any = None
            try:
                response = json.loads(response_bytes.decode("utf-8"))
                output_text = _response_output_text(response)
                value = json.loads(output_text)
                if not isinstance(value, dict):
                    raise ValidationError("E-MODEL-OUTPUT", "model output must be an object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
                code = getattr(exc, "code", "E-MODEL-RESPONSE")
                attempt_status = "REFUSED" if code == "E-MODEL-REFUSAL" else "REJECTED"
                attempts.append(
                    ModelAttemptTrace(
                        attempt,
                        attempt_status,
                        status,
                        response_bytes,
                        code,
                        str(exc),
                        response.get("id") if isinstance(response, dict) else None,
                        _response_usage(response),
                    )
                )
                raise ModelCallError(
                    code,
                    "model response could not be accepted",
                    request_bytes=request_bytes,
                    attempts=tuple(attempts),
                ) from exc
            response_id = response.get("id") if isinstance(response, dict) else None
            attempts.append(
                ModelAttemptTrace(
                    attempt,
                    "SUCCEEDED",
                    status,
                    response_bytes,
                    None,
                    None,
                    response_id,
                    _response_usage(response),
                )
            )
            return ModelCallResult(
                value=value,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                response_id=response_id,
                attempts=tuple(attempts),
            )
        raise AssertionError("model attempt loop exited without a result")


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
