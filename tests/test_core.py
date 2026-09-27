"""Black-box checks of the public pilot contract in SPECIFICATION.md."""
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from haibench.core import load_suite, run_suite, validate_response
from haibench.adapters import BackendError


GOOD = '{"answer":"4","explanation":"Two plus two is four.","optional_next_step":null}'
BAD = '{"answer":"5","explanation":"I added incorrectly.","optional_next_step":null}'


def case(case_id="a", family="correction", turns=None, history=None, seeded=False):
    return {
        "id": case_id, "family": family, "condition": "control", "pair_id": None,
        "seeded_history": seeded, "initial_messages": history or [],
        "turns": turns or [{"id": "t1", "user": "What is 2 + 2?", "expected_answer": "4" if family == "correction" else None,
                            "review_metrics": ["evidence_sensitive_revision" if family == "correction" else "goal_fidelity"]}],
        "provenance": {"author": "test", "basis": "fictional arithmetic", "uncertainty": "fixture only", "revision_trigger": "counterexample"},
    }


def suite(*cases):
    return {"schema_version": "0.1", "suite_id": "test", "version": "1", "spec_version": "0.1",
            "system_prompt": "Respond with the required JSON object.", "cases": list(cases or [case()])}


class ScriptBackend:
    name = "script"
    model = "fixture"

    def __init__(self, replies):
        self.replies = replies
        self.case_id = None
        self.offset = 0
        self.calls = []

    def metadata(self):
        return {"origin": "test"}

    def start_case(self, case_id):
        self.case_id = case_id
        self.offset = 0

    def generate(self, messages, *, seed, temperature, max_tokens, timeout):
        self.calls.append((self.case_id, json.loads(json.dumps(messages))))
        if self.offset >= len(self.replies.get(self.case_id, [])):
            raise BackendError("replay_missing")
        item = self.replies[self.case_id][self.offset]
        self.offset += 1
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return item
        return {"text": item, "finish_reason": "stop", "usage": {}}


class CoreTests(unittest.TestCase):
    def test_strict_response_json(self):
        self.assertEqual(validate_response(GOOD)["answer"], "4")
        invalid = [
            '', ' ', '```json\n' + GOOD + '\n```',
            '{"answer":"4","answer":"5","explanation":"x","optional_next_step":null}',
            '{"answer":"4","explanation":"x","optional_next_step":null,"score":1}',
            '{"answer":4,"explanation":"x","optional_next_step":null}',
            '{"answer":"4","explanation":" ","optional_next_step":null}',
            '{"answer":"4","explanation":"x","optional_next_step":NaN}',
            '{"answer":"4","explanation":"x","optional_next_step":Infinity}',
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_response(raw)

    def test_suite_schema_rejects_malformed_and_seeded_history(self):
        cases = []
        wrong = case()
        wrong["unexpected"] = 1
        cases.append(suite(wrong))
        wrong = case(history=[{"role": "assistant", "content": GOOD}], seeded=False)
        cases.append(suite(wrong))
        wrong = case(history=[{"role": "user", "content": "hello"}], seeded=True)
        cases.append(suite(wrong))
        wrong = case()
        wrong["turns"][0]["expected_answer"] = 4
        cases.append(suite(wrong))
        wrong = case()
        wrong["turns"][0]["review_metrics"] = ["goal_fidelity", "goal_fidelity"]
        cases.append(suite(wrong))
        wrong = case()
        wrong["turns"][0]["future_evidence"] = "secret"
        cases.append(suite(wrong))
        cases.append(suite(case("duplicate"), case("duplicate")))
        for data in cases:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory:
                path = Path(directory, "suite.json")
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises((ValueError, TypeError)):
                    load_suite(path)

    def test_requests_exclude_gold_and_future_turns_and_reset_history(self):
        two = [
            {"id": "first", "user": "FIRST ONLY", "expected_answer": "4", "review_metrics": ["goal_fidelity"]},
            {"id": "later", "user": "FUTURE SECRET EVIDENCE", "expected_answer": "different GOLD", "review_metrics": ["goal_fidelity"]},
        ]
        data = suite(case("a", turns=two), case("b", history=[{"role": "user", "content": "B HISTORY"}]))
        backend = ScriptBackend({"a": [GOOD, BAD], "b": [GOOD]})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "output")
            run_suite(data, backend, output)
            first = json.dumps(backend.calls[0][1])
            self.assertIn("FIRST ONLY", first)
            for forbidden in ("FUTURE SECRET EVIDENCE", "different GOLD", "review_metrics", "provenance", "condition", "pair_id"):
                self.assertNotIn(forbidden, first)
            second = json.dumps(backend.calls[1][1])
            self.assertIn(GOOD, [m["content"] for m in backend.calls[1][1]])
            third = json.dumps(backend.calls[2][1])
            self.assertIn("B HISTORY", third)
            self.assertNotIn("FIRST ONLY", third)
            self.assertNotIn(GOOD, [m["content"] for m in backend.calls[2][1]])
            self.assertTrue((output / "specification.md").exists())
            self.assertTrue((output / "suite.json").exists())
            self.assertTrue((output / "manifest.json").exists())

    def test_invalid_response_is_preserved_and_later_turn_runs(self):
        turns = [dict(case()["turns"][0]), {"id": "t2", "user": "Try again", "expected_answer": "4", "review_metrics": ["goal_fidelity"]}]
        raw = '{"answer":"4","answer":"5","explanation":"bad","optional_next_step":null}'
        backend = ScriptBackend({"a": [raw, GOOD]})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "run")
            run_suite(suite(case(turns=turns)), backend, output)
            traces = [json.loads(line) for line in (output / "traces.jsonl").read_text().splitlines()]
            self.assertEqual([t["status"] for t in traces], ["invalid_response", "ok"])
            self.assertEqual(traces[0]["raw_response"], raw)
            self.assertIn(raw, [m["content"] for m in backend.calls[1][1]])

    def test_match_is_exact_and_containment_is_unscored(self):
        c = case("contain", family="containment")
        backend = ScriptBackend({"a": [GOOD], "contain": [GOOD]})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "run")
            run_suite(suite(case(), c), backend, output)
            traces = [json.loads(line) for line in (output / "traces.jsonl").read_text().splitlines()]
            self.assertIs(traces[0]["fixture_answer_match"], True)
            self.assertIsNone(traces[1]["fixture_answer_match"])
            queue = [json.loads(line) for line in (output / "review_queue.jsonl").read_text().splitlines()]
            self.assertEqual(len(queue), 2)
            self.assertTrue(all(task["status"] == "not_reviewed" for task in queue))
            self.assertTrue(all(task["value"] is None for task in queue))
            with (output / "metrics.csv").open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2)

    def test_truncation_context_limit_and_failure_block_remaining_turns(self):
        turns = [dict(case()["turns"][0]), {"id": "t2", "user": "again", "expected_answer": "4", "review_metrics": ["goal_fidelity"]}]
        configurations = [
            ({"a": [{"text": GOOD, "finish_reason": "length", "usage": {}}, GOOD]}, {}, ["truncated", "ok"]),
            ({"a": [GOOD]}, {"max_context_chars": 1}, ["context_limit", "blocked"]),
            ({"a": [BackendError("fixture_failure")]}, {}, ["backend_error", "blocked"]),
        ]
        for replies, options, expected in configurations:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                backend = ScriptBackend(replies)
                output = Path(directory, "run")
                run_suite(suite(case(turns=turns)), backend, output, **options)
                traces = [json.loads(line) for line in (output / "traces.jsonl").read_text().splitlines()]
                self.assertEqual([t["status"] for t in traces], expected)
                self.assertEqual(len(backend.calls), 0 if expected[0] == "context_limit" else 2 if expected[0] == "truncated" else 1)
                self.assertIsNone(traces[0]["fixture_answer_match"])
                self.assertIsNone(traces[1]["fixture_answer_match"]) if expected[1] == "blocked" else self.assertIs(traces[1]["fixture_answer_match"], True)

    def test_existing_output_refused_and_config_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory, "existing")
            destination.mkdir()
            marker = destination / "marker"
            marker.write_text("preserve")
            backend = ScriptBackend({"a": [GOOD]})
            with self.assertRaises((ValueError, FileExistsError)):
                run_suite(suite(), backend, destination)
            self.assertEqual(marker.read_text(), "preserve")
            self.assertEqual(backend.calls, [])
            for options in ({"max_tokens": 0}, {"temperature": float("nan")}, {"timeout": 0}, {"max_context_chars": 0}):
                with self.subTest(options=options), self.assertRaises((ValueError, TypeError)):
                    run_suite(suite(), backend, Path(directory, "new"), **options)

    def test_exact_match_does_not_prove_explanation_and_rejects_numeric_answers(self):
        misleading = '{"answer":"4","explanation":"Two plus two is five, so the answer is four.","optional_next_step":null}'
        approximate = '{"answer":"about 4","explanation":"Maybe four.","optional_next_step":null}'
        for nonstring in ('true', '[4]', '4'):
            with self.subTest(nonstring=nonstring), self.assertRaises(ValueError):
                validate_response('{"answer":' + nonstring + ',"explanation":"math","optional_next_step":null}')
        turns = [dict(case()["turns"][0]), {"id": "t2", "user": "Again?", "expected_answer": "4", "review_metrics": ["evidence_sensitive_revision"]}]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "run")
            report = run_suite(suite(case(turns=turns)), ScriptBackend({"a": [approximate, misleading]}), output)
            traces = [json.loads(line) for line in (output / "traces.jsonl").read_text().splitlines()]
            self.assertEqual([t["fixture_answer_match"] for t in traces], [False, True])
            self.assertEqual(report["by_family"]["correction"]["eligible_correction_checks"], 2)
            self.assertEqual(report["by_family"]["correction"]["matches"], 1)
            queue = [json.loads(line) for line in (output / "review_queue.jsonl").read_text().splitlines()]
            self.assertEqual(queue[1]["status"], "not_reviewed")
            self.assertIsNone(queue[1]["value"])
            self.assertIn("five", queue[1]["response_evidence"])

    def test_failure_denominators_and_review_refs_across_cases(self):
        turns = [dict(case()["turns"][0]), {"id": "t2", "user": "Again?", "expected_answer": "4", "review_metrics": ["goal_fidelity"]}]
        data = suite(case("bad", turns=turns), case("failed", turns=turns), case("good"), case("contain", family="containment"))
        replies = {"bad": ['{"answer":'], "failed": [BackendError("fixture_failure")], "good": [GOOD], "contain": [GOOD]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "run")
            report = run_suite(data, ScriptBackend(replies), output)
            traces = [json.loads(line) for line in (output / "traces.jsonl").read_text().splitlines()]
            counts = report["by_family"]["correction"]
            self.assertEqual(counts["planned"], 5)
            self.assertEqual(counts["eligible_correction_checks"], 1)
            self.assertEqual(counts["invalid_response"], 1)
            self.assertEqual(counts["backend_error"], 2)  # bad's second entry is missing
            self.assertEqual(counts["blocked"], 1)
            self.assertEqual(report["by_family"]["containment"]["eligible_correction_checks"], 0)
            self.assertFalse(any(key in report["overall_execution"] for key in ("matches", "mismatches", "eligible_correction_checks")))
            queue = [json.loads(line) for line in (output / "review_queue.jsonl").read_text().splitlines()]
            self.assertEqual(len(queue), len(traces))
            for task in queue:
                reference = task["trace_ref"]
                self.assertEqual(reference["file"], "traces.jsonl")
                actual = traces[reference["line"] - 1]
                self.assertEqual((task["case_id"], task["turn_id"]), (actual["case_id"], actual["turn_id"]))
                self.assertEqual(task["response_evidence"], actual["raw_response"])
                self.assertEqual(task["availability"], "available" if actual["status"] == "ok" else "unavailable")

    def test_snapshots_hashes_and_replay_are_reproducible(self):
        from haibench.adapters import ReplayBackend

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay = root / "responses.json"
            replay.write_text(json.dumps({"a": [GOOD]}), encoding="utf-8")
            reports = []
            traces = []
            for index in range(2):
                output = root / str(index)
                reports.append(run_suite(suite(), ReplayBackend(replay), output, seed=41))
                traces.append((output / "traces.jsonl").read_text())
                manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(manifest["suite_sha256"], hashlib.sha256((output / "suite.json").read_bytes().rstrip(b"\n")).hexdigest())
                self.assertEqual(manifest["spec_sha256"], hashlib.sha256((output / "specification.md").read_bytes()).hexdigest())
                self.assertTrue(manifest["synthetic_replay"])
            self.assertEqual(reports[0], reports[1])
            parsed = [[json.loads(line) for line in record.splitlines()] for record in traces]
            for record in parsed:
                for turn in record:
                    turn.pop("duration_seconds")
            self.assertEqual(parsed[0], parsed[1])

    def test_invalid_envelope_and_start_case_failure_are_recorded(self):
        turns = [dict(case()["turns"][0]), {"id": "t2", "user": "Again?", "expected_answer": "4", "review_metrics": ["goal_fidelity"]}]
        class StartFails(ScriptBackend):
            def start_case(self, case_id):
                raise BackendError("fixture_failure")

        for backend, statuses in (
            (ScriptBackend({"a": [{"text": GOOD, "usage": {}}]}), ["backend_error", "blocked"]),
            (StartFails({}), ["backend_error", "blocked"]),
        ):
            with self.subTest(statuses=statuses, backend=type(backend).__name__), tempfile.TemporaryDirectory() as directory:
                output = Path(directory, "run")
                run_suite(suite(case(turns=turns)), backend, output)
                traces = [json.loads(line) for line in (output / "traces.jsonl").read_text().splitlines()]
                self.assertEqual([t["status"] for t in traces], statuses)
                self.assertTrue(all(t["fixture_answer_match"] is None for t in traces))


class ProgressTests(unittest.TestCase):
    def test_heartbeat_stops_after_call(self):
        import threading
        from haibench.core import _heartbeat
        pulsed = threading.Event()
        messages = []
        def progress(message):
            messages.append(message)
            pulsed.set()
        with _heartbeat(progress, 'case/turn', interval=0.01):
            self.assertTrue(pulsed.wait(1))
        count = len(messages)
        self.assertGreater(count, 0)
        self.assertEqual(messages[0], 'case/turn: still waiting')
        # The context joins its worker; no callbacks can arrive after exit.
        self.assertEqual(len(messages), count)


if __name__ == "__main__":
    unittest.main()
