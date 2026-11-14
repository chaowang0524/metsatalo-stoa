import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stoa import eval as evaluation
from stoa.judge import JudgeError, judge_text


def text_row(value="候选答案"):
    return {
        "qid": 1, "source": "ifc_bench", "type": "text", "question": "问题",
        "gt": "参考答案", "answer": {"value": value, "deterministic": False},
        "correct": None, "seconds": 1.0, "cost_usd": 0.01, "llm_calls": 2,
        "prompt_tokens": 100, "completion_tokens": 20, "router": "hybrid",
    }


class EvaluationTests(unittest.TestCase):
    def test_partial_credit_retains_answer_and_judge_usage(self):
        row = text_row()
        with patch.object(evaluation, "judge_text", return_value={
            "score": 0.5, "explanation": "部分正确", "cost_usd": 0.002, "llm_calls": 1,
        }):
            evaluation.grade_text(row)
        self.assertEqual(row["text_score"], 0.5)
        self.assertIsNone(row["correct"])
        self.assertEqual(row["cost_usd"], 0.01)
        self.assertEqual(row["judge"]["cost_usd"], 0.002)

    def test_missing_answer_is_zero_without_judge_request(self):
        for value in (None, "", "  "):
            with self.subTest(value=value), patch.object(evaluation, "judge_text") as judge:
                row = evaluation.grade_text(text_row(value))
                self.assertEqual(row["text_score"], 0)
                self.assertIsNone(row["judge_error"])
                judge.assert_not_called()

    def test_numeric_zero_answer_is_sent_to_judge(self):
        with patch.object(evaluation, "judge_text", return_value={"score": 1}) as judge:
            row = evaluation.grade_text(text_row(0))
        self.assertEqual(row["text_score"], 1)
        self.assertEqual(judge.call_args.args[2], "0")

    def test_judge_failure_is_not_marked_wrong(self):
        for error in (RuntimeError("rate limited"), JudgeError("invalid", {"cost_usd": 0.003})):
            with self.subTest(error=error), patch.object(evaluation, "judge_text", side_effect=error):
                row = evaluation.grade_text(text_row())
                self.assertIsNone(row["text_score"])
                self.assertTrue(row["judge_error"])
                if isinstance(error, JudgeError):
                    self.assertEqual(row["judge"]["cost_usd"], 0.003)

    def test_summary_separates_metrics_and_includes_judging_cost(self):
        row = text_row()
        row.update(text_score=0.5, judge={"score": 0.5, "cost_usd": 0.002, "llm_calls": 1})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            evaluation.print_summary([row], "hybrid")
        text = output.getvalue()
        self.assertNotIn("nan", text)
        self.assertIn("50.0%", text)
        self.assertIn("12.000", text)  # $0.012 for one question -> $12 per 1k.
        self.assertIn("0.00200", text)

    def test_existing_results_are_graded_without_answering_or_overwriting(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "original.jsonl"
            source.write_text(json.dumps(text_row(), ensure_ascii=False) + "\n", encoding="utf-8")
            original = source.read_bytes()
            with patch("sys.argv", ["stoa.eval", "--score-existing", str(source)]), \
                 patch.object(evaluation, "answer") as answer, \
                 patch.object(evaluation, "judge_text", return_value={"score": 1}), \
                 contextlib.redirect_stdout(io.StringIO()):
                evaluation.main()
            answer.assert_not_called()
            self.assertEqual(source.read_bytes(), original)
            saved = list(Path(temporary).glob("original_scored_*.jsonl"))
            self.assertEqual(len(saved), 1)
            row = json.loads(saved[0].read_text(encoding="utf-8"))
            self.assertEqual(row["text_score"], 1)
            self.assertEqual(row["question"], "问题")

    def test_already_graded_and_numeric_rows_do_not_call_judge(self):
        numeric = {**text_row(), "type": "count", "correct": True}
        graded = {**text_row(), "text_score": 0}
        for row in (numeric, graded):
            before = copy.deepcopy(row)
            with patch.object(evaluation, "judge_text") as judge:
                evaluation.grade_text(row)
            judge.assert_not_called()
            self.assertEqual(row, before)

    def test_normal_eval_grades_text_by_default(self):
        numeric = {**text_row(5), "qid": 2, "source": "swiss_dwellings", "type": "count", "correct": True}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("sys.argv", ["stoa.eval", "--workers", "1"]), \
                 patch.object(evaluation, "Path", side_effect=lambda *parts: root.joinpath(*parts)), \
                 patch.object(evaluation, "load", return_value=[{}, {}]), \
                 patch.object(evaluation, "run_one", side_effect=[text_row(), numeric]), \
                 patch.object(evaluation, "judge_text", return_value={"score": 0.5}) as judge, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                evaluation.main()
            judge.assert_called_once()
            self.assertNotIn("nan", output.getvalue())
            saved = list((root / "out").glob("eval_*.jsonl"))
            rows = [json.loads(line) for line in saved[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["text_score"], 0.5)
            self.assertTrue(rows[1]["correct"])

    def test_invalid_judge_score_retains_billed_usage(self):
        response = SimpleNamespace()
        with patch("stoa.judge.verda.chat", return_value=response), \
             patch("stoa.judge.verda.usage_of", return_value={"cost_usd": 0.003}), \
             patch("stoa.judge.verda.text_of", return_value='{"answer": 0.7}'):
            with self.assertRaises(JudgeError) as caught:
                judge_text("q", "ref", "candidate")
        self.assertEqual(caught.exception.usage["cost_usd"], 0.003)

    def test_numeric_scoring_keeps_exact_count_and_two_percent_rules(self):
        self.assertTrue(evaluation.score({"type": "count", "answer": 5}, 5))
        self.assertFalse(evaluation.score({"type": "count", "answer": 5}, 5.1))
        self.assertTrue(evaluation.score({"type": "area", "answer": 100}, 102))
        self.assertFalse(evaluation.score({"type": "area", "answer": 100}, 102.1))


if __name__ == "__main__":
    unittest.main()
