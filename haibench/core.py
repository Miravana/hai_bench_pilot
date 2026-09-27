"""Strict suite validation and inspectable trajectory execution."""
import csv
import hashlib
import json
import math
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .adapters import BackendError

VERSION = "0.1"
METRICS = frozenset({"evidence_sensitive_revision", "unsupported_reversal", "uncertainty_appropriateness", "goal_fidelity", "personalization_disclosure", "rejection_respected", "accessible_support"})
STATUSES = ("ok", "invalid_response", "truncated", "backend_error", "context_limit", "blocked")


class ValidationError(ValueError):
    """An invalid suite, response, or run configuration."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValidationError("non-finite JSON value")


def _parse(text):
    try:
        return json.loads(text, object_pairs_hook=_object, parse_constant=_nonfinite)
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc


def _shape(obj, required, location):
    if type(obj) is not dict or set(obj) != set(required):
        raise ValidationError(f"{location}: expected exactly {sorted(required)}")


def _string(value, location, *, nullable=False):
    if nullable and value is None:
        return
    if type(value) is not str or not value.strip():
        raise ValidationError(f"{location}: expected nonempty string")


def validate_suite(suite):
    """Reject unknown fields and invalid values before any backend request."""
    _shape(suite, ("schema_version", "suite_id", "version", "spec_version", "system_prompt", "cases"), "suite")
    for key in ("schema_version", "spec_version"):
        if suite[key] != VERSION or type(suite[key]) is not str:
            raise ValidationError(f"{key}: unsupported version")
    for key in ("suite_id", "version", "system_prompt"):
        _string(suite[key], key)
    if type(suite["cases"]) is not list or not suite["cases"]:
        raise ValidationError("cases: expected nonempty array")
    case_ids = set()
    for case in suite["cases"]:
        _shape(case, ("id", "family", "condition", "pair_id", "seeded_history", "initial_messages", "turns", "provenance"), "case")
        for key in ("id", "condition"):
            _string(case[key], key)
        if case["id"] in case_ids:
            raise ValidationError("duplicate case id")
        case_ids.add(case["id"])
        if type(case["family"]) is not str or case["family"] not in ("correction", "containment"):
            raise ValidationError("family: expected correction or containment")
        _string(case["pair_id"], "pair_id", nullable=True)
        if type(case["seeded_history"]) is not bool:
            raise ValidationError("seeded_history: expected boolean")
        if type(case["initial_messages"]) is not list:
            raise ValidationError("initial_messages: expected array")
        assistant_seen = False
        for msg in case["initial_messages"]:
            _shape(msg, ("role", "content"), "initial message")
            if type(msg["role"]) is not str or msg["role"] not in ("system", "user", "assistant"):
                raise ValidationError("initial message: invalid role")
            if type(msg["content"]) is not str:
                raise ValidationError("initial message: content must be string")
            assistant_seen |= msg["role"] == "assistant"
        if assistant_seen != case["seeded_history"]:
            raise ValidationError("seeded_history disagrees with initial messages")
        _shape(case["provenance"], ("author", "basis", "uncertainty", "revision_trigger"), "provenance")
        for key, value in case["provenance"].items():
            _string(value, f"provenance.{key}")
        if type(case["turns"]) is not list or not case["turns"]:
            raise ValidationError("turns: expected nonempty array")
        turn_ids = set()
        for turn in case["turns"]:
            _shape(turn, ("id", "user", "expected_answer", "review_metrics"), "turn")
            for key in ("id", "user"):
                _string(turn[key], f"turn.{key}")
            if turn["id"] in turn_ids:
                raise ValidationError("duplicate turn id")
            turn_ids.add(turn["id"])
            if case["family"] == "correction":
                _string(turn["expected_answer"], "expected_answer")
            elif turn["expected_answer"] is not None:
                raise ValidationError("containment expected_answer must be null")
            metrics = turn["review_metrics"]
            if type(metrics) is not list or not metrics or any(type(m) is not str or m not in METRICS for m in metrics) or len(metrics) != len(set(metrics)):
                raise ValidationError("invalid review_metrics")
    return suite


def load_suite(path):
    """Load and validate a UTF-8 suite."""
    return validate_suite(_parse(Path(path).read_text(encoding="utf-8")))


def validate_response(text):
    """Parse the exact three-field JSON response protocol."""
    if type(text) is not str:
        raise ValidationError("response must be text")
    response = _parse(text)
    _shape(response, ("answer", "explanation", "optional_next_step"), "response")
    _string(response["answer"], "answer")
    _string(response["explanation"], "explanation")
    _string(response["optional_next_step"], "optional_next_step", nullable=True)
    return response


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _counts():
    return {"planned": 0, **{status: 0 for status in STATUSES}, "eligible_correction_checks": 0, "matches": 0, "mismatches": 0}


def _execution_counts():
    return {"planned": 0, **{status: 0 for status in STATUSES}}


@contextmanager
def _heartbeat(progress, label, interval=15.0):
    """Only the observer runs in a thread; inference stays synchronous."""
    stopped = threading.Event()
    def pulse():
        while not stopped.wait(interval):
            progress(f"{label}: still waiting")
    worker = threading.Thread(target=pulse, daemon=True) if progress else None
    if worker:
        worker.start()
    try:
        yield
    finally:
        stopped.set()
        if worker:
            worker.join()


def run_suite(suite, backend, output, *, seed=0, temperature=0.0, max_tokens=512, timeout=60.0, max_context_chars=30000, progress=None):
    """Execute independent cases and persist provenance, traces, and blank reviews."""
    validate_suite(suite)
    if type(seed) is not int or type(max_tokens) is not int or max_tokens <= 0 or type(max_context_chars) is not int or max_context_chars <= 0:
        raise ValidationError("invalid integer run option")
    if type(temperature) not in (float, int) or not math.isfinite(temperature) or temperature < 0 or type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
        raise ValidationError("invalid numeric run option")
    spec = Path(__file__).resolve().parent.parent / "SPECIFICATION.md"
    spec_bytes = spec.read_bytes()
    canonical = _canonical(suite)
    preflight = getattr(backend, "preflight", None)
    if preflight is not None:
        preflight()
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    (out / "suite.json").write_bytes(canonical + b"\n")
    (out / "specification.md").write_bytes(spec_bytes)
    manifest = {"schema_version": VERSION, "runner_version": VERSION, "spec_version": suite["spec_version"], "suite_sha256": _hash(canonical), "spec_sha256": _hash(spec_bytes), "created_at_utc": datetime.now(timezone.utc).isoformat(), "backend": backend.name, "model": backend.model, "backend_metadata": backend.metadata(), "synthetic_replay": backend.name == "replay", "structured_output_protocol": True, "config": {"seed": seed, "temperature": temperature, "max_tokens": max_tokens, "timeout": timeout, "max_context_chars": max_context_chars}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report = {"schema_version": VERSION, "suite_id": suite["suite_id"], "structured_output_protocol": True, "synthetic_replay": backend.name == "replay", "overall_execution": _execution_counts(), "by_family": {}, "by_case": {}}
    with (out / "traces.jsonl").open("w", encoding="utf-8") as traces, (out / "review_queue.jsonl").open("w", encoding="utf-8") as reviews, (out / "metrics.csv").open("w", newline="", encoding="utf-8") as metrics:
        writer = csv.DictWriter(metrics, fieldnames=["case_id", "turn_id", "family", "status", "fixture_answer_match"])
        writer.writeheader()
        trace_line = 0
        for case in suite["cases"]:
            cid, family = case["id"], case["family"]
            report["by_case"][cid] = _counts()
            report["by_family"].setdefault(family, _counts())
            history = [{"role": "system", "content": suite["system_prompt"]}, *[dict(m) for m in case["initial_messages"]]]
            blocked = None
            start_error = None
            try:
                backend.start_case(cid)
            except BackendError as exc:
                start_error = exc.code
            for index, turn in enumerate(case["turns"]):
                label = f"{cid}/{turn['id']} ({index + 1}/{len(case['turns'])})"
                if progress:
                    progress(f"{label}: starting")
                messages = [*history, {"role": "user", "content": turn["user"]}]
                trace_line += 1
                trace = {"case_id": cid, "turn_id": turn["id"], "turn_index": index, "family": family, "condition": case["condition"], "pair_id": case["pair_id"], "seeded_history": case["seeded_history"], "request_messages": messages, "request_sha256": _hash(_canonical(messages)), "request_attempted": False, "raw_response": None, "parsed_response": None, "status": None, "error_code": None, "duration_seconds": None, "usage": {}, "finish_reason": None, "fixture_answer_match": None}
                if start_error is not None and index == 0:
                    trace.update(status="backend_error", error_code=start_error)
                    blocked = start_error
                elif blocked is not None:
                    trace.update(status="blocked", error_code=blocked)
                elif sum(len(m["content"]) for m in messages) > max_context_chars:
                    trace.update(status="context_limit", error_code="context_limit")
                    blocked = "context_limit"
                else:
                    start = time.monotonic()
                    trace["request_attempted"] = True
                    try:
                        with _heartbeat(progress, label):
                            result = backend.generate(messages, seed=seed, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
                        trace["duration_seconds"] = time.monotonic() - start
                        if type(result) is not dict or not {"text", "finish_reason", "usage"} <= result.keys() or type(result["text"]) is not str or (result["finish_reason"] is not None and type(result["finish_reason"]) is not str) or type(result["usage"]) is not dict:
                            raise BackendError("backend_protocol")
                        trace.update(raw_response=result["text"], usage=result["usage"], finish_reason=result["finish_reason"])
                        trace["response_model"] = result.get("model")
                        if "thinking" in result:
                            trace["raw_thinking"] = result["thinking"]
                            trace["generation_diagnostics"] = result["diagnostics"]
                        if result.get("control_error"):
                            raise BackendError(result["control_error"])
                        history.extend(({"role": "user", "content": turn["user"]}, {"role": "assistant", "content": result["text"]}))
                        if result["finish_reason"] in ("length", "truncated"):
                            trace.update(status="truncated", error_code="truncated")
                        else:
                            try:
                                parsed = validate_response(result["text"])
                                trace.update(status="ok", parsed_response=parsed)
                                if family == "correction":
                                    trace["fixture_answer_match"] = parsed["answer"].strip() == turn["expected_answer"].strip()
                            except ValidationError:
                                trace.update(status="invalid_response", error_code="invalid_response")
                    except BackendError as exc:
                        trace.update(status="backend_error", error_code=exc.code, duration_seconds=time.monotonic() - start)
                        blocked = exc.code
                for counts in (report["overall_execution"], report["by_family"][family], report["by_case"][cid]):
                    counts["planned"] += 1
                    counts[trace["status"]] += 1
                for counts in (report["by_family"][family], report["by_case"][cid]):
                    if trace["fixture_answer_match"] is not None:
                        counts["eligible_correction_checks"] += 1
                        counts["matches" if trace["fixture_answer_match"] else "mismatches"] += 1
                traces.write(json.dumps(trace, ensure_ascii=False) + "\n")
                traces.flush()
                if progress:
                    detail = f" ({trace['error_code']})" if trace["error_code"] else ""
                    progress(f"{label}: {trace['status']}{detail}")
                writer.writerow({"case_id": cid, "turn_id": turn["id"], "family": family, "status": trace["status"], "fixture_answer_match": "" if trace["fixture_answer_match"] is None else str(trace["fixture_answer_match"]).lower()})
                for metric in turn["review_metrics"]:
                    reviews.write(json.dumps({"case_id": cid, "turn_id": turn["id"], "metric_id": metric, "status": "not_reviewed", "value": None, "availability": "available" if trace["status"] == "ok" else "unavailable", "trace_ref": {"file": "traces.jsonl", "line": trace_line}, "response_evidence": trace["raw_response"]}, ensure_ascii=False) + "\n")
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
