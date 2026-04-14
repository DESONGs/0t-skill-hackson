from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from ot_skill_enterprise.shared.contracts.common import ServiceError, utc_now

from .execution import RuntimeExecutionRequest, RuntimeExecutionResult
from .transcript import RuntimeTranscript


_DEFAULT_RUNTIME_TIMEOUT_SECONDS = 300.0


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


@dataclass(slots=True)
class SubprocessRuntimeExecutor:
    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        started_at = utc_now()
        launch_spec = request.launch_spec
        if not launch_spec.launcher:
            raise ValueError(f"runtime {request.runtime_id!r} has no launcher configured")

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
                },
                events=[
                    {
                        "type": "error",
                        "message": f"runtime process timed out after {timeout_seconds:.0f}s",
                        "status": "failed",
                        "metadata": {"timeout_seconds": timeout_seconds},
                    }
                ],
                metadata={"timeout_seconds": timeout_seconds},
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
                    details={"timeout_seconds": timeout_seconds},
                ),
            )
        finally:
            payload_file.unlink(missing_ok=True)
        finished_at = utc_now()

        if completed.returncode != 0:
            transcript = RuntimeTranscript(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                ok=False,
                status="failed",
                summary=completed.stderr.strip() or "runtime process failed",
                output_payload={"stderr": completed.stderr.strip(), "returncode": completed.returncode},
                events=[
                    {
                        "type": "error",
                        "message": completed.stderr.strip() or "runtime process failed",
                        "status": "failed",
                        "metadata": {"returncode": completed.returncode},
                    }
                ],
                metadata={"returncode": completed.returncode},
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
                error=ServiceError(code="runtime_process_failed", message=transcript.summary, details={"returncode": completed.returncode}),
            )

        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            transcript = RuntimeTranscript(
                runtime_id=request.runtime_id,
                session_id=request.session_id,
                invocation_id=request.invocation_id,
                ok=False,
                status="failed",
                summary=f"runtime process returned exit 0 but stdout is not valid JSON: {exc}",
                output_payload={"stdout": completed.stdout[:2000], "stderr": completed.stderr.strip()},
                events=[],
                metadata={"returncode": 0},
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
                    details={"returncode": 0},
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
