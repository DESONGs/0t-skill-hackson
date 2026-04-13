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
        completed = subprocess.run(
            command,
            cwd=str(Path(launch_spec.cwd or request.cwd).expanduser().resolve()),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, **dict(launch_spec.environment)} if launch_spec.environment else None,
        )
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

        payload = json.loads(completed.stdout)
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
