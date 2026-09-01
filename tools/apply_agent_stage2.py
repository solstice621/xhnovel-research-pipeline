from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# model_api.py: define the small executor protocol and identify the API implementation.
model_api = ROOT / "src/xhnovel_pipeline/model_api.py"
replace_once(
    model_api,
    "from typing import Any, Callable\n",
    "from typing import Any, Callable, Protocol\n",
)
replace_once(
    model_api,
    "from .canonical import canonical_dumps\nfrom .errors import ValidationError\n",
    "from .canonical import canonical_dumps\n"
    "from .constants import MODEL_EXECUTOR_BUILD_ID\n"
    "from .errors import ValidationError\n",
)
replace_once(
    model_api,
    'Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]\n\n\n',
    '''Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]

API_EXECUTOR_KIND = "API"
OPENAI_RESPONSES_FORMAT = "OPENAI_RESPONSES"


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


''',
)
replace_once(
    model_api,
    "class OpenAIResponsesClient:\n    def __init__(\n",
    "class OpenAIResponsesClient:\n"
    "    executor_kind = API_EXECUTOR_KIND\n"
    "    response_format = OPENAI_RESPONSES_FORMAT\n"
    "    executor_build_id = MODEL_EXECUTOR_BUILD_ID\n\n"
    "    def __init__(\n",
)

# agent_files.py: aggregate pending tasks and expose a stable executor build identity.
agent_files = ROOT / "src/xhnovel_pipeline/agent_files.py"
replace_once(
    agent_files,
    'AGENT_FILES_EXECUTOR_KIND = "AGENT_FILES"\nAGENT_FILES_RESPONSE_FORMAT = "RAW_JSON"\n',
    'AGENT_FILES_EXECUTOR_KIND = "AGENT_FILES"\n'
    'AGENT_FILES_RESPONSE_FORMAT = "RAW_JSON"\n'
    'AGENT_FILES_EXECUTOR_BUILD_ID = "agent-files-v1"\n',
)
replace_once(
    agent_files,
    "\n\ndef _safe_file_stem(window_id: str) -> str:\n",
    '''

class AgentResponsesPending(PipelineError):
    "Aggregate all native SceneWindows awaiting host-agent answers."

    def __init__(self, pending: list[AgentResponsePending]) -> None:
        ordered = tuple(sorted(pending, key=lambda item: item.window_id))
        if not ordered:
            raise ValueError("AgentResponsesPending requires at least one task")
        self.pending = ordered
        self.pending_count = len(ordered)
        self.tasks_dir = ordered[0].task_path.parent
        self.answers_dir = ordered[0].answer_path.parent
        super().__init__(
            "E-AGENT-RESPONSES-PENDING",
            f"{self.pending_count} SceneWindow answer(s) are pending under {self.tasks_dir}",
        )


def _safe_file_stem(window_id: str) -> str:
''',
)
replace_once(
    agent_files,
    "class AgentFileExecutor:\n    \"\"\"Thin file adapter; host code agents produce answers outside this process.\"\"\"\n\n"
    "    executor_kind = AGENT_FILES_EXECUTOR_KIND\n",
    "class AgentFileExecutor:\n"
    "    \"\"\"Thin file adapter; host code agents produce answers outside this process.\"\"\"\n\n"
    "    executor_kind = AGENT_FILES_EXECUTOR_KIND\n"
    "    executor_build_id = AGENT_FILES_EXECUTOR_BUILD_ID\n",
)

# novel_workflow.py: accept the executor protocol rather than the API concrete class.
workflow = ROOT / "src/xhnovel_pipeline/novel_workflow.py"
replace_once(
    workflow,
    "from .model_api import OpenAIResponsesClient\n",
    "from .model_api import SceneScoutExecutor\n",
)
text = workflow.read_text(encoding="utf-8")
count = text.count("extractor_client: OpenAIResponsesClient")
if count != 2:
    raise SystemExit(f"{workflow}: expected two executor annotations, found {count}")
workflow.write_text(
    text.replace("extractor_client: OpenAIResponsesClient", "extractor_client: SceneScoutExecutor"),
    encoding="utf-8",
)

# scene_scout.py: share execution, validation, merge, and replay across both modes.
scene = ROOT / "src/xhnovel_pipeline/scene_scout.py"
replace_once(
    scene,
    "from jsonschema import Draft202012Validator\n\n",
    '''from jsonschema import Draft202012Validator

from .agent_files import (
    AGENT_FILES_EXECUTOR_BUILD_ID,
    AGENT_FILES_EXECUTOR_KIND,
    AGENT_FILES_RESPONSE_FORMAT,
    AgentResponsePending,
    AgentResponsesPending,
    agent_task_bytes,
    decode_agent_answer,
)

''',
)
replace_once(
    scene,
    "from .constants import MODEL_EXECUTOR_BUILD_ID, PROFILE_ID, SCHEMA_VERSION\n",
    "from .constants import PROFILE_ID, SCHEMA_VERSION\n",
)
replace_once(
    scene,
    '''from .model_api import (
    ModelAttemptTrace,
    ModelCallError,
    ModelCallResult,
    OpenAIResponsesClient,
    _response_output_text,
)
''',
    '''from .model_api import (
    API_EXECUTOR_KIND,
    OPENAI_RESPONSES_FORMAT,
    ModelAttemptTrace,
    ModelCallError,
    ModelCallResult,
    SceneScoutExecutor,
    _response_output_text,
)
''',
)
scene_text = scene.read_text(encoding="utf-8")
count = scene_text.count("client: OpenAIResponsesClient")
if count != 3:
    raise SystemExit(f"{scene}: expected three executor annotations, found {count}")
scene.write_text(
    scene_text.replace("client: OpenAIResponsesClient", "client: SceneScoutExecutor"),
    encoding="utf-8",
)
replace_once(
    scene,
    "\n\ndef _span_key(span: dict[str, Any]) -> tuple[str, int, int]:\n",
    '''

def _decode_executor_output(response_format: str, response_bytes: bytes) -> dict[str, Any]:
    if response_format == AGENT_FILES_RESPONSE_FORMAT:
        return decode_agent_answer(response_bytes)
    if response_format == OPENAI_RESPONSES_FORMAT:
        try:
            response = json.loads(response_bytes.decode("utf-8"))
            output = json.loads(_response_output_text(response))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise ValidationError("E-MODEL-RESPONSE", "stored model response is invalid") from exc
        if not isinstance(output, dict):
            raise ValidationError("E-MODEL-OUTPUT", "model output must be an object")
        return output
    raise ValidationError(
        "E-SCENE-EXECUTOR",
        f"unsupported Scene Scout response format {response_format!r}",
    )


def _span_key(span: dict[str, Any]) -> tuple[str, int, int]:
''',
)
replace_once(
    scene,
    '''        "parameters": {
            "endpoint": client.endpoint,
            "timeout_seconds": format(client.timeout, ".17g"),
            "max_attempts": client.max_attempts,
            "structured_output": True,
''',
    '''        "parameters": {
            "executor_kind": client.executor_kind,
            "response_format": client.response_format,
            "endpoint": client.endpoint,
            "timeout_seconds": format(client.timeout, ".17g"),
            "max_attempts": client.max_attempts,
            "structured_output": True,
''',
)
replace_once(
    scene,
    '        "executor_build_id": MODEL_EXECUTOR_BUILD_ID,\n',
    '        "executor_build_id": client.executor_build_id,\n',
)
replace_once(
    scene,
    '''    pending = [window for window in windows if window["window_id"] not in state["completed"]]
    if pending:
''',
    '''    pending = [window for window in windows if window["window_id"] not in state["completed"]]
    pending_responses: dict[str, AgentResponsePending] = {}
    if pending:
''',
)
replace_once(
    scene,
    '''                try:
                    call = future.result()
                except ModelCallError as exc:
''',
    '''                try:
                    call = future.result()
                except AgentResponsePending as exc:
                    pending_responses[window_id] = exc
                except ModelCallError as exc:
''',
)
replace_once(
    scene,
    '''        raise ValidationError(
            "E-SCENE-PARTIAL",
            f"{len(state['failures'])} scene window(s) failed; first {first_window_id}: "
            f"{failure['error_code']}",
        )

    receipt_by_attempt_id: dict[str, str] = {}
''',
    '''        raise ValidationError(
            "E-SCENE-PARTIAL",
            f"{len(state['failures'])} scene window(s) failed; first {first_window_id}: "
            f"{failure['error_code']}",
        )
    if pending_responses:
        state["status"] = "WAITING_FOR_AGENT"
        write_checkpoint()
        raise AgentResponsesPending(list(pending_responses.values()))

    receipt_by_attempt_id: dict[str, str] = {}
''',
)
replace_once(
    scene,
    '''    for window, response_artifact_id in zip(windows, response_artifact_ids):
        try:
            stored_response = json.loads(store.get(response_artifact_id).decode("utf-8"))
            output_value = json.loads(_response_output_text(stored_response))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise ValidationError("E-SCENE-CHECKPOINT", "completed response is invalid") from exc
        for candidate in _validate_scout_output(
''',
    '''    response_format = build["parameters"]["response_format"]
    for window, response_artifact_id in zip(windows, response_artifact_ids):
        try:
            output_value = _decode_executor_output(
                response_format,
                store.get(response_artifact_id),
            )
        except ValidationError as exc:
            raise ValidationError("E-SCENE-CHECKPOINT", "completed response is invalid") from exc
        for candidate in _validate_scout_output(
''',
)
replace_once(
    scene,
    '''        parameters = build["parameters"]
        if (
            run["bundle_hash"] != bundle["bundle_hash"]
''',
    '''        parameters = build["parameters"]
        executor_kind = parameters.get("executor_kind")
        response_format = parameters.get("response_format")
        expected_executor_build = {
            API_EXECUTOR_KIND: (OPENAI_RESPONSES_FORMAT, "openai-responses-v1"),
            AGENT_FILES_EXECUTOR_KIND: (
                AGENT_FILES_RESPONSE_FORMAT,
                AGENT_FILES_EXECUTOR_BUILD_ID,
            ),
        }.get(executor_kind)
        if (
            expected_executor_build is None
            or response_format != expected_executor_build[0]
            or build["executor_build_id"] != expected_executor_build[1]
            or run["bundle_hash"] != bundle["bundle_hash"]
''',
)
old_replay = '''            try:
                stored_request = json.loads(store.get(request_artifact_id).decode("utf-8"))
                stored_response = json.loads(store.get(response_artifact_id).decode("utf-8"))
                input_value = json.loads(stored_request["input"])
                output_value = json.loads(_response_output_text(stored_response))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValidationError("E-SCENE-REPLAY", "stored scene exchange is invalid") from exc
            expected_input = _window_input(
                catalog, window, discovery_brief=request["discovery_brief"]
            )
            if (
                set(stored_request) != {"model", "instructions", "input", "text", "store"}
                or stored_request["model"] != build["model"]
                or stored_request["instructions"] != prompt
                or stored_request["store"] is not False
                or stored_request["input"] != canonical_dumps(expected_input).decode("utf-8")
                or input_value != expected_input
                or stored_request["text"]["format"]
                != {
                    "type": "json_schema",
                    "name": "xuanhuan_scene_candidates",
                    "strict": True,
                    "schema": output_schema,
                }
            ):
                raise ValidationError("E-SCENE-REPLAY", "stored scene request differs")
            for candidate in _validate_scout_output(
'''
new_replay = '''            request_bytes = store.get(request_artifact_id)
            response_bytes = store.get(response_artifact_id)
            expected_input = _window_input(
                catalog, window, discovery_brief=request["discovery_brief"]
            )
            try:
                if response_format == OPENAI_RESPONSES_FORMAT:
                    stored_request = json.loads(request_bytes.decode("utf-8"))
                    input_value = json.loads(stored_request["input"])
                    if (
                        set(stored_request)
                        != {"model", "instructions", "input", "text", "store"}
                        or stored_request["model"] != build["model"]
                        or stored_request["instructions"] != prompt
                        or stored_request["store"] is not False
                        or stored_request["input"]
                        != canonical_dumps(expected_input).decode("utf-8")
                        or input_value != expected_input
                        or stored_request["text"]["format"]
                        != {
                            "type": "json_schema",
                            "name": "xuanhuan_scene_candidates",
                            "strict": True,
                            "schema": output_schema,
                        }
                    ):
                        raise ValidationError(
                            "E-SCENE-REPLAY", "stored scene request differs"
                        )
                elif response_format == AGENT_FILES_RESPONSE_FORMAT:
                    expected_request = agent_task_bytes(
                        instructions=prompt,
                        input_value=expected_input,
                        schema_name="xuanhuan_scene_candidates",
                        schema=output_schema,
                    )
                    if request_bytes != expected_request:
                        raise ValidationError(
                            "E-SCENE-REPLAY", "stored agent task differs"
                        )
                else:
                    raise ValidationError(
                        "E-SCENE-REPLAY", "unknown Scene Scout response format"
                    )
                output_value = _decode_executor_output(response_format, response_bytes)
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValidationError,
            ) as exc:
                if isinstance(exc, ValidationError) and exc.code == "E-SCENE-REPLAY":
                    raise
                raise ValidationError(
                    "E-SCENE-REPLAY", "stored scene exchange is invalid"
                ) from exc
            for candidate in _validate_scout_output(
'''
replace_once(scene, old_replay, new_replay)

# Integration tests exercise the native pipeline without any API credential.
integration = ROOT / "tests/test_agent_files_integration.py"
integration.write_text(r'''from __future__ import annotations

import json

import pytest

from xhnovel_pipeline.agent_files import (
    AGENT_FILES_EXECUTOR_KIND,
    AGENT_FILES_RESPONSE_FORMAT,
    AgentFileExecutor,
    AgentResponsesPending,
)
from xhnovel_pipeline.model_api import API_EXECUTOR_KIND, OpenAIResponsesClient
from xhnovel_pipeline.novel_workflow import run_novel_research
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.runtime import TEST_NOW
from xhnovel_pipeline.validate import validate_all


def _spec(source) -> dict:
    return {
        "source": {"kind": "txt", "path": str(source), "title": "Agent 仙途"},
        "rights": {
            "basis": "USER_AUTHORIZED_LOCAL_COPY",
            "may_store_full_text": True,
            "may_send_to_external_model": True,
            "may_export_excerpts": False,
        },
        "source_quality": {
            "edition_status": "USER_VERIFIED_COPY",
            "textual_completeness": "COMPLETE",
        },
        "request": {"discovery_brief": "寻找对象控制变化及其后续行动空间"},
        "limits": {"max_chapters": 10, "max_bytes": 2_000_000},
        "scene_scout": {
            "window_chars": 10_000,
            "overlap_chars": 1_800,
            "max_workers": 4,
        },
        "strict_order": False,
    }


def _unknown() -> dict:
    return {"status": "UNKNOWN", "values": [], "support_spans": []}


def _candidate(span: dict) -> dict:
    support = {
        "segment_id": span["segment_id"],
        "start": span["start"],
        "end": min(span["end"], span["start"] + 4),
    }
    known_actor = {
        "status": "KNOWN",
        "values": ["林舟"],
        "support_spans": [support],
    }
    known_action = {
        "status": "KNOWN",
        "values": ["触发机关"],
        "support_spans": [support],
    }
    known_target = {
        "status": "KNOWN",
        "values": ["天门机关"],
        "support_spans": [support],
    }
    return {
        "summary": "林舟触发天门机关",
        "source_spans": [support],
        "actors": known_actor,
        "action": known_action,
        "target": known_target,
        "precondition": _unknown(),
        "state_transition": _unknown(),
        "external_response": _unknown(),
        "immediate_feedback": _unknown(),
        "new_affordances": _unknown(),
        "persistence": _unknown(),
        "mechanic_pressure_point": _unknown(),
    }


def _write_answer(task_path, value: dict) -> bytes:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    answer_path = task_path.parents[1] / task["answer_file"]
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_bytes(raw)
    return raw


def _pending_run(tmp_path, text: str):
    source = tmp_path / "book.txt"
    source.write_text(text, encoding="utf-8")
    work_dir = tmp_path / "run"
    executor = AgentFileExecutor(work_dir / "agent-files")
    with pytest.raises(AgentResponsesPending) as caught:
        run_novel_research(
            _spec(source),
            work_dir,
            extractor_client=executor,
            repo_root=repo_root(),
            now=TEST_NOW,
        )
    return source, work_dir, caught.value


def test_agent_files_native_two_pass_run_needs_no_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, work_dir, pending = _pending_run(
        tmp_path,
        "第一章 天门\n林舟触发天门机关，山路随之开启。",
    )

    assert pending.pending_count == 1
    task_path = pending.pending[0].task_path
    task = json.loads(task_path.read_text(encoding="utf-8"))
    span = task["input"]["window"]["source_spans"][0]
    raw_answer = _write_answer(task_path, {"candidates": [_candidate(span)]})

    result = run_novel_research(
        _spec(source),
        work_dir,
        extractor_client=AgentFileExecutor(work_dir / "agent-files"),
        repo_root=repo_root(),
        now=TEST_NOW,
    )

    build = result["scout"]["build"]
    assert build["parameters"]["executor_kind"] == AGENT_FILES_EXECUTOR_KIND
    assert build["parameters"]["response_format"] == AGENT_FILES_RESPONSE_FORMAT
    assert result["scout"]["run"]["usage_ledger"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "attempts_with_unknown_usage": 1,
        "estimated_cost_microusd": None,
    }
    assert len(result["scout"]["candidates"]) == 1
    attempt = result["catalog"].all("ModelAttempt")[0]
    assert attempt["http_status"] is None
    assert attempt["provider_response_id"] is None
    assert result["store"].get(attempt["request_artifact_id"]) == task_path.read_bytes()
    assert result["store"].get(attempt["response_artifact_id"]) == raw_answer
    validate_all(result["catalog"], result["store"])


def test_agent_files_rejected_answer_can_be_corrected_and_replayed(tmp_path):
    source, work_dir, pending = _pending_run(
        tmp_path,
        "第一章 天门\n林舟触发天门机关，山路随之开启。",
    )
    task_path = pending.pending[0].task_path
    task = json.loads(task_path.read_text(encoding="utf-8"))
    span = task["input"]["window"]["source_spans"][0]
    invalid = _candidate(span)
    invalid["action"]["support_spans"] = [
        {
            "segment_id": span["segment_id"],
            "start": span["end"],
            "end": span["end"] + 1,
        }
    ]
    _write_answer(task_path, {"candidates": [invalid]})

    with pytest.raises(Exception, match="E-SCENE-PARTIAL"):
        run_novel_research(
            _spec(source),
            work_dir,
            extractor_client=AgentFileExecutor(work_dir / "agent-files"),
            repo_root=repo_root(),
            now=TEST_NOW,
        )

    _write_answer(task_path, {"candidates": [_candidate(span)]})
    result = run_novel_research(
        _spec(source),
        work_dir,
        extractor_client=AgentFileExecutor(work_dir / "agent-files"),
        repo_root=repo_root(),
        now=TEST_NOW,
    )

    attempts = result["catalog"].all("ModelAttempt")
    assert [item["status"] for item in attempts] == ["REJECTED", "SUCCEEDED"]
    assert attempts[1]["retry_of"] == attempts[0]["attempt_id"]
    validate_all(result["catalog"], result["store"])


def test_agent_files_partial_answers_resume_without_repeating_completed_windows(tmp_path):
    source, work_dir, first_pending = _pending_run(
        tmp_path,
        "第一章 长路\n" + ("甲乙丙丁" * 5_000),
    )
    assert first_pending.pending_count >= 2
    first_task = first_pending.pending[0].task_path
    _write_answer(first_task, {"candidates": []})

    with pytest.raises(AgentResponsesPending) as second:
        run_novel_research(
            _spec(source),
            work_dir,
            extractor_client=AgentFileExecutor(work_dir / "agent-files"),
            repo_root=repo_root(),
            now=TEST_NOW,
        )
    assert second.value.pending_count == first_pending.pending_count - 1

    for item in second.value.pending:
        _write_answer(item.task_path, {"candidates": []})
    result = run_novel_research(
        _spec(source),
        work_dir,
        extractor_client=AgentFileExecutor(work_dir / "agent-files"),
        repo_root=repo_root(),
        now=TEST_NOW,
    )

    attempts = result["catalog"].all("ModelAttempt")
    assert len(attempts) == len(result["scout"]["windows"])
    assert all(item["status"] == "SUCCEEDED" for item in attempts)
    validate_all(result["catalog"], result["store"])


def test_api_executor_identity_remains_explicit():
    client = OpenAIResponsesClient(model="test", api_key="test")
    assert client.executor_kind == API_EXECUTOR_KIND
    assert client.response_format == "OPENAI_RESPONSES"
''', encoding="utf-8")

# The patch workflow is intentionally one-shot; remove its implementation from the final tree.
Path(__file__).unlink()
workflow_path = ROOT / ".github/workflows/apply-agent-stage2.yml"
if workflow_path.exists():
    workflow_path.unlink()
