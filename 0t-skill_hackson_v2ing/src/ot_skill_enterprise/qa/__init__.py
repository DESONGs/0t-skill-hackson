"""QA and evaluation layer."""

from .evaluator import QAEvaluator, build_qa_evaluator
from .feedback import normalize_feedback_target
from .models import (
    ContractEvaluationResult,
    EvaluationRecord,
    RuntimeEvaluationResult,
    TaskMatchEvaluationResult,
)
from .store import EvaluationStore, build_evaluation_store

__all__ = [
    "ContractEvaluationResult",
    "EvaluationRecord",
    "EvaluationStore",
    "QAEvaluator",
    "build_evaluation_store",
    "build_qa_evaluator",
    "normalize_feedback_target",
    "RuntimeEvaluationResult",
    "TaskMatchEvaluationResult",
]
