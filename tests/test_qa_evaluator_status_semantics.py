from __future__ import annotations

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ot_skill_enterprise.qa import QAEvaluator, classify_review_status
from ot_skill_enterprise.qa.store import LocalFileEvaluationRepository
from ot_skill_enterprise.runs.models import RunRecord, RunTrace


def _run_record(*, ok: bool, status: str, summary: str = "style distillation review") -> RunRecord:
    trace = RunTrace(trace_id="trace-1", run_id="run-1", runtime_session_id="session-1")
    return RunRecord(
        run_id="run-1",
        runtime_id="runtime-1",
        runtime_session_id="session-1",
        agent_id="agent-1",
        flow_id="flow-1",
        status=status,
        ok=ok,
        summary=summary,
        traces=[trace],
        trace=trace,
    )


class QAEvaluatorStatusSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = QAEvaluator(store=LocalFileEvaluationRepository())

    def test_classify_review_status_routes_semantic_states(self) -> None:
        self.assertEqual(
            classify_review_status(
                status="runtime_failed",
                runtime_status="runtime_failed",
                runtime_pass=False,
                contract_pass=False,
                task_match_score=0.0,
            ),
            "runtime_failed",
        )
        self.assertEqual(
            classify_review_status(
                status="insufficient_signal",
                runtime_status="succeeded",
                runtime_pass=True,
                contract_pass=True,
                task_match_score=0.92,
            ),
            "insufficient_signal",
        )
        self.assertEqual(
            classify_review_status(
                status="no_pattern_detected",
                runtime_status="succeeded",
                runtime_pass=True,
                contract_pass=True,
                task_match_score=0.92,
            ),
            "no_pattern_detected",
        )
        self.assertEqual(
            classify_review_status(
                status="generate",
                runtime_status="succeeded",
                runtime_pass=True,
                contract_pass=True,
                task_match_score=0.92,
            ),
            "generate",
        )
        self.assertEqual(
            classify_review_status(
                status="pending",
                runtime_status="succeeded",
                runtime_pass=True,
                contract_pass=True,
                task_match_score=0.39,
                has_task_match_score=True,
            ),
            "generate_with_low_confidence",
        )

    def test_evaluate_run_keeps_runtime_failure_separate_from_generation_statuses(self) -> None:
        result = self.evaluator.evaluate_run(
            _run_record(ok=False, status="failed"),
            subject_kind="style_distillation",
            subject_id="wallet-1",
            status="runtime_failed",
            metadata={"review_status": "runtime_failed"},
        )

        self.assertEqual(result.runtime_result.status, "runtime_failed")
        self.assertEqual(result.task_match_result.status, "runtime_failed")
        self.assertEqual(result.metadata["review_status"], "runtime_failed")
        self.assertEqual(result.overall_grade, "fail")

    def test_evaluate_run_distinguishes_generate_and_low_confidence_reviews(self) -> None:
        generate_result = self.evaluator.evaluate_run(
            _run_record(ok=True, status="succeeded"),
            subject_kind="style_distillation",
            subject_id="wallet-2",
            status="generate",
            metadata={"review_status": "generate"},
        )
        low_confidence_result = self.evaluator.evaluate_run(
            _run_record(ok=True, status="succeeded"),
            subject_kind="style_distillation",
            subject_id="wallet-3",
            status="generate_with_low_confidence",
            metadata={
                "review_status": "generate_with_low_confidence",
                "task_match_score": 0.41,
                "task_match_threshold": 0.8,
            },
        )
        insufficient_result = self.evaluator.evaluate_run(
            _run_record(ok=True, status="succeeded"),
            subject_kind="style_distillation",
            subject_id="wallet-4",
            status="insufficient_signal",
            metadata={
                "review_status": "insufficient_signal",
                "task_match_score": 0.91,
                "task_match_threshold": 0.8,
            },
        )
        no_pattern_result = self.evaluator.evaluate_run(
            _run_record(ok=True, status="succeeded"),
            subject_kind="style_distillation",
            subject_id="wallet-5",
            status="no_pattern_detected",
            metadata={
                "review_status": "no_pattern_detected",
                "task_match_score": 0.91,
                "task_match_threshold": 0.8,
            },
        )

        self.assertEqual(generate_result.runtime_result.status, "succeeded")
        self.assertEqual(generate_result.task_match_result.status, "generate")
        self.assertEqual(generate_result.overall_grade, "pass")

        self.assertEqual(low_confidence_result.task_match_result.status, "generate_with_low_confidence")
        self.assertEqual(low_confidence_result.metadata["review_status"], "generate_with_low_confidence")
        self.assertEqual(low_confidence_result.overall_grade, "warn")

        self.assertEqual(insufficient_result.task_match_result.status, "insufficient_signal")
        self.assertEqual(insufficient_result.overall_grade, "warn")

        self.assertEqual(no_pattern_result.task_match_result.status, "no_pattern_detected")
        self.assertEqual(no_pattern_result.overall_grade, "warn")


if __name__ == "__main__":
    unittest.main()
