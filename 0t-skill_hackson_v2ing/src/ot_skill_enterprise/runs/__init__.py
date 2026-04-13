"""Run recording layer for agent-integrated executions."""

from .models import ArtifactRecord, RunRecord, RunTrace, RuntimeEvent, TraceEvent, TraceRecord
from .recorder import RunRecorder
from .store import RunStore, build_run_store


def record_external_run(*args, **kwargs):
    from .intake import record_external_run as _record_external_run

    return _record_external_run(*args, **kwargs)

__all__ = [
    "ArtifactRecord",
    "RunRecord",
    "RunRecorder",
    "RunStore",
    "RunTrace",
    "RuntimeEvent",
    "TraceEvent",
    "TraceRecord",
    "build_run_store",
    "record_external_run",
]
