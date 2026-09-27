# HAI-Bench measurement pilot

A small Python runner for eight multi-turn development scenarios: four correction/evidence cases and four matched personalization conditions. The written contract is [SPECIFICATION.md](SPECIFICATION.md). Start there for measurement assumptions, permitted claims, and failure rules.

This is a research prototype, not a validated benchmark or leaderboard. No participant data are included. Existing benchmarks informed the project but their datasets and metrics are not bundled or reproduced here.

## Quick start — no installation or API key

Unzip the package, open a terminal inside `hai_bench_pilot`, then run:

```bash
python3 --version
python3 -m unittest discover -s tests -v
python3 -m haibench validate --suite scenarios/pilot.json
python3 -m haibench run --suite scenarios/pilot.json --backend replay --responses examples/replay.json --out runs/my_demo
```

Use Python 3.10 or newer. The project uses only the standard library. Each `--out` directory must be new; existing output is never overwritten. Choose a different directory name for another run.

Replay is a scripted software demonstration. It contains intentionally bad answers and a steering example; it is not a real model evaluation. Expect 24 structurally valid turns, with 10 exact matches and 2 mismatches among the 12 correction turns. All 12 containment turns require review and receive no automatic substantive score. The deliberately steering response is therefore **not passed** just because its JSON is valid.

## Run an installed local model

On the computer where Ollama and your model are installed, start Ollama, then check:

```bash
ollama list
```

Use the exact installed local model name in the next command. For example, if `qwen3:4b` appears:

```bash
python3 -m haibench run --suite scenarios/pilot.json --backend ollama --model qwen3:4b --out runs/qwen_local_1 --timeout 120 --max-tokens 512
```

The runner connects to `http://127.0.0.1:11434`, does not install models, and does not fall back to a remote service. It requests constrained JSON from Ollama and validates it again locally. Some models may exhaust the output budget or fail to follow the schema; those events remain in the report. Thinking mode uses the runtime/model default unless explicitly selected with `--thinking on` or `--thinking off`; `--thinking default` omits the Ollama `think` field exactly as before. If you change token budgets or model options between runs, treat them as different configurations.

The manifest now attempts to record the Ollama runtime version and the selected local model name/digest. Discovery uses `/api/version`, `/api/show`, and `/api/tags`, with a two-second socket timeout per request; unavailable metadata remains null with safe error codes. Save `ollama --version` and `ollama list` separately when discovery is unavailable. Identical seeds do not guarantee identical generation. A loopback address also does not prove the underlying server cannot forward requests; use local weights and check your Ollama configuration. This package does not inspect the runtime's network behavior.

Explicit thinking controls accept a top-level `/api/show` `capabilities` array containing `"thinking"`. If detailed `thinking.values` metadata is present, it must advertise the requested boolean. Unsupported controls fail with `thinking_mode_unsupported`; older runtimes without verifiable metadata fail with `thinking_support_unverified`. No prompt-based fallback or model-name guessing is used. These configuration failures are checked once before benchmark cases or output creation (exit 2), rather than recorded as per-case backend errors. Default mode still runs when discovery fails. An `off` response containing thinking text is preserved but fails with `thinking_mode_not_honored`. Advertised support is not proof that an arbitrary server honors the option; validate the actual runtime/model before comparisons.

Traces keep `raw_thinking` separate from `raw_response`. Only response content enters scoring and subsequent history. `generation_diagnostics` records requested mode, thinking presence, character counts, and combined generated tokens. Separate thinking/content token counts are null because the Ollama response does not provide a reliable split; counts are never estimated. Thinking-only length termination remains `truncated`.

CLI progress goes to stderr: startup, case/turn status, elapsed run time, a heartbeat every 15 seconds during inference, and completion/failure. The final execution-count JSON remains on stdout. Replay accepts `--thinking default` but rejects `on/off`.

Live Ollama/Qwen inference was unavailable in the development environment. Transport correctness was tested with a local HTTP fixture, separately from the synthetic replay. See [VERIFICATION.md](VERIFICATION.md) for exact checks and limits.

## Read a result

| File | Purpose |
| --- | --- |
| `manifest.json` | Versions, hashes, settings, backend identity, replay label |
| `suite.json` and `specification.md` | Frozen inputs and measurement contract for this run |
| `traces.jsonl` | One record per planned turn, including raw outputs and failed/blocked turns |
| `report.json` | Execution coverage by case/family; exact-answer counts for correction only |
| `metrics.csv` | Compact per-turn execution and exact-answer observations |
| `review_queue.jsonl` | Blank human-review tasks linked to trace lines |

`ok` means structurally valid completed output. It does not mean helpful, truthful, autonomous, or pedagogically effective. `fixture_answer_match` checks only the `answer` field against a bounded key; false explanation text can coexist with a matching answer. All substantive rubric tasks begin with `value: null` and `status: not_reviewed`.

The seeded-history correction cases begin with an explicitly supplied assistant answer. They measure behavior conditional on that history, not the model's natural error rate. The uncertain-evidence case has `unknown` as its key because the packet does not settle the value. Every case begins with fresh history.

Do not compute a total HAI score from these outputs. No confidence intervals or population claims are justified by these eight purposively authored cases. Cases sharing `pair_id` share material and are not independent replications.

## Exercise failure handling

```bash
python3 -m haibench run --suite scenarios/pilot.json --backend replay --responses examples/replay_failures.json --out runs/my_failure_demo
```

This intentionally returns exit code 1. It includes a duplicate-key model response and a simulated transport failure. Expected coverage: 21 `ok`, 1 `invalid_response`, 1 `backend_error`, and 1 `blocked`, with 7 matches and 2 mismatches among 9 eligible correction turns. The invalid response remains in history so a later turn can show recovery; transport failure blocks the rest of that case.

Exit codes: 0 = execution completed with structurally valid responses, including wrong answers; 1 = some turns invalid, truncated, failed, or blocked; 2 = invalid configuration/input or a filesystem error. Read the report rather than interpreting exit code 0 as benchmark success.

## Extending the pilot

Add new scenarios only after writing their construct, key, provenance, and revision trigger. Schema validation rejects unknown fields. Use a new suite version when wording or keys change, and a new specification version when measurement behavior changes.

A backend implements `name`, `model`, `metadata()`, `start_case(case_id)`, and `generate(messages, *, seed, temperature, max_tokens, timeout)`. It returns raw `text`, `finish_reason`, and `usage`; optional `model` records the responding model. Return unknown usage as missing, not fabricated zero. Raise `BackendError` with a safe code for predictable failures. Gold keys are never passed to `generate`.

An API backend can be added through this interface; no paid API adapter is included. Any remote evaluation needs explicit configuration and appropriate data handling. Preserve native benchmark protocols in separately labeled future modules; do not call modified task packs reproductions.

## Package contents

- `SPECIFICATION.md`: measurement and implementation contract authored by the parent agent.
- `SCENARIO_REVIEW.md`: keys, matched conditions, and unresolved validity questions.
- `haibench/`: runner, CLI, replay, and local Ollama transport.
- `scenarios/`, `examples/`: public synthetic task data and demonstration outputs.
- `tests/`: independent behavioral and transport checks.
- `VERIFICATION.md`: actual validation results and remaining limits.
- `verification_artifacts/`: complete synthetic runs and packaging check results.

The selected project scope is research-first, with adults learning outside continuous instructor supervision as the eventual population. This population includes beginners and people needing substantial support. A simulator is not evidence that the system helps them; participant validation remains future work.
