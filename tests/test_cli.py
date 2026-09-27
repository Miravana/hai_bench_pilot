"""CLI exit codes and artifact behavior with a local replay fixture."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_core import GOOD, case, suite


ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def call(self, *args):
        return subprocess.run([sys.executable, "-m", "haibench", *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True)

    def test_validate_and_run_status_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "suite.json"
            replay = root / "replay.json"
            source.write_text(json.dumps(suite()), encoding="utf-8")
            replay.write_text(json.dumps({"a": [GOOD]}), encoding="utf-8")
            self.assertEqual(self.call("validate", "--suite", source).returncode, 0)
            successful = root / "ok"
            run = self.call("run", "--suite", source, "--backend", "replay", "--responses", replay, "--out", successful)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue((successful / "report.json").exists())
            self.assertEqual(self.call("run", "--suite", source, "--backend", "replay", "--responses", replay, "--out", successful).returncode, 2)
            source.write_text(json.dumps(suite(case("a", turns=[{"id": "x", "user": "hi", "expected_answer": 9, "review_metrics": ["goal_fidelity"]}]))))
            self.assertEqual(self.call("validate", "--suite", source).returncode, 2)

    def test_mismatch_is_data_but_missing_fixture_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "suite.json"
            replay = root / "replay.json"
            source.write_text(json.dumps(suite()), encoding="utf-8")
            replay.write_text(json.dumps({"a": ['{"answer":"wrong","explanation":"I guessed.","optional_next_step":null}']}))
            result = self.call("run", "--suite", source, "--backend", "replay", "--responses", replay, "--out", root / "mismatch")
            self.assertEqual(result.returncode, 0, result.stderr)
            replay.write_text(json.dumps({"a": []}))
            result = self.call("run", "--suite", source, "--backend", "replay", "--responses", replay, "--out", root / "missing")
            self.assertEqual(result.returncode, 1, result.stderr)
            traces = [json.loads(line) for line in (root / "missing" / "traces.jsonl").read_text().splitlines()]
            self.assertEqual(traces[0]["status"], "backend_error")
            self.assertEqual(traces[0]["error_code"], "replay_missing")


class ThinkingCLITests(unittest.TestCase):
    call = CLITests.call

    def test_invalid_and_replay_thinking(self):
        for mode, message in (('invalid', 'invalid choice'), ('on', 'requires --backend ollama'),
                              ('off', 'requires --backend ollama')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'out'
                result = self.call('run', '--suite', ROOT / 'scenarios/pilot.json', '--backend', 'replay',
                                   '--responses', ROOT / 'examples/replay.json', '--out', output,
                                   '--thinking', mode)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
