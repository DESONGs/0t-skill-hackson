from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping

from ot_skill_enterprise.shared.contracts.common import ServiceError, utc_now

from .execution import RuntimeExecutionRequest, RuntimeExecutionResult
from .transcript import RuntimeTranscript


_DEFAULT_RUNTIME_TIMEOUT_SECONDS = 300.0


def _failure_type_for_runtime_error(message: str, *, returncode: int | None = None, timed_out: bool = False) -> str:
    lowered = message.lower()
    if timed_out or "timed out" in lowered or "timeout" in lowered:
        return "runtime_timeout"
    if any(marker in lowered for marker in ("api key", "unauthorized", "forbidden", "provider unavailable", "provider auth", "auth")):
        return "provider_unavailable"
    if any(marker in lowered for marker in ("json", "parse", "not valid json", "stdout is not valid json")):
        return "json_parse_failed"
    if returncode is not None and returncode < 0:
        return "runtime_abort"
    return "runtime_abort"


def _optional_timeout_seconds(request: RuntimeExecutionRequest) -> float:
    raw = (
        request.metadata.get("runtime_timeout_seconds")
        or request.launch_spec.metadata.get("timeout_seconds")
        or os.environ.get("OT_RUNTIME_EXEC_TIMEOUT_SECONDS")
    )
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_RUNTIME_TIMEOUT_SECONDS
    return timeout if timeout > 0 else _DEFAULT_RUNTIME_TIMEOUT_SECONDS


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mock_transcript(request: RuntimeExecutionRequest) -> RuntimeTranscript | None:
    mock_response = _mapping(request.metadata.get("mock_response"))
    if not mock_response:
        return None
    raw_text = json.dumps(mock_response, ensure_ascii=False, indent=2)
    attempts = [
        {
            "attempt_index": 1,
            "provider": "mock",
            "model_id": "mock",
            "model": "mock/mock",
            "api": "mock",
            "raw_text": raw_text,
            "raw_text_salvaged": True,
        }
    ]
    return RuntimeTranscript(
        runtime_id=request.runtime_id,
        session_id=request.session_id,
        invocation_id=request.invocation_id,
        ok=True,
        status="succeeded",
        summary="reflection mock response injected",
        input_payload=dict(request.input_payload),
        output_payload={
            "review_backend": "pi-reflection-mock",
            "raw_output": {
                "text": raw_text,
                "raw_text": raw_text,
                "stop_reason": "mock",
                "content_blocks": [{"type": "mock"}],
                "model": {
                    "provider": "mock",
                    "model_id": "mock",
                    "api": "mock",
                },
                "attempts": attempts,
                "failure_type": None,
                "raw_text_salvaged": True,
            },
            "normalized_output": mock_response,
            "attempts": attempts,
        },
        events=[
            {
                "type": "message_start",
                "message": "reflection mock response injected",
                "status": "succeeded",
                "metadata": {"mock_response": True},
            }
        ],
        provider_ids=["mock"],
        metadata={"mock_response": True},
        stdout=raw_text,
        stderr="",
    )


@dataclass(slots=True)
class SubprocessRuntimeExecutor:
    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        started_at = utc_now()
        launch_spec = request.launch_spec
        if not launch_spec.launcher:
            raise ValueError(f"runtime {request.runtime_id!r} has no launcher configured")
        mock_transcript = _mock_transcript(request)
        if mock_transcript is not None:
            finished_at = utc_now()
            return RuntimeExecutionResult(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                launch_spec=launch_spec,
                command=["mock-response"],
                returncode=0,
                transcript=mock_transcript,
                started_at=started_at,
                finished_at=finished_at,
            )

        payload = {
            "run_id": request.metadata.get("run_id"),
            "session_id": request.session_id,
            "invocation_id": request.invocation_id,
            "runtime_id": request.runtime_id,
            "workspace_dir": request.workspace_dir,
            "session_workspace": request.session_workspace,
            "cwd": request.cwd,
            "prompt": request.prompt,
            "input_payload": dict(request.input_payload),
            "metadata": dict(request.metadata),
        }

        with NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
            payload_file = Path(handle.name)

        command = [*launch_spec.launcher, "--payload-file", str(payload_file)]
        timeout_seconds = _optional_timeout_seconds(request)
        try:
            completed = subprocess.run(
                command,
                cwd=str(Path(launch_spec.cwd or request.cwd).expanduser().resolve()),
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, **dict(launch_spec.environment)} if launch_spec.environment else None,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            payload_file.unlink(missing_ok=True)
            finished_at = utc_now()
            failure_type = _failure_type_for_runtime_error(str(exc), timed_out=True)
            transcript = RuntimeTranscript(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                ok=False,
                status="failed",
                summary=f"runtime process timed out after {timeout_seconds:.0f}s",
                output_payload={
                    "stderr": str(exc),
                    "returncode": None,
                    "timeout_seconds": timeout_seconds,
                    "failure_type": failure_type,
                },
                events=[
                    {
                        "type": "error",
                        "message": f"runtime process timed out after {timeout_seconds:.0f}s",
                        "status": "failed",
                        "metadata": {"timeout_seconds": timeout_seconds, "failure_type": failure_type},
                    }
                ],
                metadata={"timeout_seconds": timeout_seconds, "failure_type": failure_type},
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
            )
            return RuntimeExecutionResult(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                launch_spec=launch_spec,
                command=command,
                returncode=-9,
                transcript=transcript,
                started_at=started_at,
                finished_at=finished_at,
                error=ServiceError(
                    code="runtime_process_timeout",
                    message=transcript.summary,
                    details={"timeout_seconds": timeout_seconds, "failure_type": failure_type},
                ),
            )
        finally:
            payload_file.unlink(missing_ok=True)
        finished_at = utc_now()

        if completed.returncode != 0:
            failure_type = _failure_type_for_runtime_error(
                completed.stderr.strip() or completed.stdout.strip() or "runtime process failed",
                returncode=completed.returncode,
            )
            transcript = RuntimeTranscript(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                ok=False,
                status="failed",
                summary=completed.stderr.strip() or "runtime process failed",
                output_payload={
                    "stderr": completed.stderr.strip(),
                    "returncode": completed.returncode,
                    "failure_type": failure_type,
                },
                events=[
                    {
                        "type": "error",
                        "message": completed.stderr.strip() or "runtime process failed",
                        "status": "failed",
                        "metadata": {"returncode": completed.returncode, "failure_type": failure_type},
                    }
                ],
                metadata={"returncode": completed.returncode, "failure_type": failure_type},
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            return RuntimeExecutionResult(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                launch_spec=launch_spec,
                command=command,
                returncode=completed.returncode,
                transcript=transcript,
                started_at=started_at,
                finished_at=finished_at,
                error=ServiceError(
                    code="runtime_process_failed",
                    message=transcript.summary,
                    details={"returncode": completed.returncode, "failure_type": failure_type},
                ),
            )

        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            failure_type = "json_parse_failed"
            transcript = RuntimeTranscript(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                ok=False,
                status="failed",
                summary=f"runtime process returned exit 0 but stdout is not valid JSON: {exc}",
                output_payload={
                    "stdout": completed.stdout[:2000],
                    "stderr": completed.stderr.strip(),
                    "failure_type": failure_type,
                },
                events=[],
                metadata={"returncode": 0, "failure_type": failure_type},
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            return RuntimeExecutionResult(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                launch_spec=launch_spec,
                command=command,
                returncode=completed.returncode,
                transcript=transcript,
                started_at=started_at,
                finished_at=finished_at,
                error=ServiceError(
                    code="runtime_stdout_parse_failed",
                    message=transcript.summary,
                    details={"returncode": 0, "failure_type": failure_type},
                ),
            )
        transcript = RuntimeTranscript.from_payload(
            payload,
            runtime_id=request.runtime_id,
            session_id=request.session_id,
            invocation_id=request.invocation_id,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        return RuntimeExecutionResult(
            runtime_id=request.runtime_id,
            session_id=request.session_id,
            invocation_id=request.invocation_id,
            launch_spec=launch_spec,
            command=command,
            returncode=completed.returncode,
            transcript=transcript,
            started_at=started_at,
            finished_at=finished_at,
        )
