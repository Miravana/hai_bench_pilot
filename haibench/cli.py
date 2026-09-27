"""Command-line entry point for validation and trajectory runs."""
import argparse
import json
import sys
import time

from .adapters import BackendError, OllamaBackend, ReplayBackend
from .core import ValidationError, load_suite, run_suite


def _positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(prog="haibench")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--suite", required=True)
    run = sub.add_parser("run")
    run.add_argument("--suite", required=True)
    run.add_argument("--backend", choices=("replay", "ollama"), required=True)
    run.add_argument("--responses")
    run.add_argument("--model")
    run.add_argument("--thinking", choices=("default", "on", "off"), default="default")
    run.add_argument("--base-url", default="http://127.0.0.1:11434")
    run.add_argument("--out", required=True)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument("--max-tokens", type=_positive_int, default=512)
    run.add_argument("--timeout", type=float, default=60.0)
    run.add_argument("--max-context-chars", type=_positive_int, default=30000)
    args = parser.parse_args(argv)
    started = time.monotonic()
    def progress(message):
        print(f"[{time.monotonic() - started:.1f}s] {message}", file=sys.stderr, flush=True)
    try:
        suite = load_suite(args.suite)
        if args.command == "validate":
            print(f"Valid suite: {suite['suite_id']}")
            return 0
        if args.backend == "replay":
            if args.thinking != "default":
                raise ValidationError("--thinking on/off requires --backend ollama")
            if not args.responses or args.model:
                raise ValidationError("replay requires --responses and forbids --model")
            backend = ReplayBackend(args.responses)
        else:
            if not args.model or args.responses:
                raise ValidationError("ollama requires --model and forbids --responses")
            backend = OllamaBackend(args.model, base_url=args.base_url, thinking=args.thinking)
        progress(f"Starting {backend.name}/{backend.model}: {len(suite['cases'])} cases, "
                 f"{sum(len(c['turns']) for c in suite['cases'])} turns; thinking={args.thinking}; output={args.out}")
        report = run_suite(suite, backend, args.out, seed=args.seed, temperature=args.temperature, max_tokens=args.max_tokens, timeout=args.timeout, max_context_chars=args.max_context_chars, progress=progress)
        print(json.dumps(report["overall_execution"], sort_keys=True))
        failed = any(report["overall_execution"][s] for s in ("invalid_response", "truncated", "backend_error", "context_limit", "blocked"))
        progress(f"Completed{' with execution failures' if failed else ''}: "
                 f"{report['overall_execution']['ok']}/{report['overall_execution']['planned']} structurally valid turns")
        return 1 if failed else 0
    except (ValidationError, BackendError, ValueError, OSError) as exc:
        progress(f"haibench: failed: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
