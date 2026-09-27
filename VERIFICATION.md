# Verification record

## Frozen experimental baseline — 27 September 2026 UTC

This baseline preserves explicit `--thinking {default,on,off}`, separate thinking/content recording, capability preflight before cases, and progress/heartbeat reporting. The capability parser accepts top-level `capabilities` containing `"thinking"` when detailed thinking metadata is absent; unsupported or unverified explicit modes fail before cases/output creation. Default mode still omits `think`. Scoring, scenarios, suite/schema version identifiers, and the human-review boundaries are unchanged. Historical run artifacts remain immutable.

Final validation on Python 3.12.0:

- `PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest discover -s tests -v`: **24 tests passed** (1.869s), with approved loopback access for the HTTP fixtures.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m haibench validate --suite scenarios/pilot.json`: exit 0, `Valid suite: hai_measurement_pilot`.
- `SHA256SUMS` was regenerated for the existing package inventory and verified. It excludes itself, local `runs/` outputs, and interpreter caches. Historical run files were separately checked for byte-for-byte preservation during finalization.

### Unresolved runtime-accounting limitation

Saved `runs/qwen_4b_default_v2` and `runs/qwen_4b_thinkon_v2` artifacts identify Ollama 0.33.3 and the same `qwen3.5:4b` model digest. Both manifests record `max_tokens=1024`. The adapter maps this value directly to request `options.num_predict`; it copies the response's top-level `eval_count` to `usage.eval_count` and `generation_diagnostics.generated_tokens` without summation or token estimation.

In each of these two runs, completed turns C03.t1, P03.t1, and P03.t2 report `eval_count` values of **1,072**, **1,057**, and **1,106**, respectively, with `finish_reason="stop"` and nonempty thinking and content. The other completed turn, C03.t2, reports 942. All 20 truncated turns in each run report 1,024, `finish_reason="length"`, nonempty thinking, and empty content. The off run has no above-limit counts. Separate thinking/content token counts are unavailable.

These observations establish that reported `eval_count` can exceed the configured `num_predict` in these saved runs. They do not establish why. The artifacts preserve configuration, messages, extracted response fields, and selected usage values, not full wire request/response envelopes or runtime accounting details. The runner sends one generation-limit setting, but the saved evidence cannot determine whether or how the runtime applies it across thinking and content. No explanation of Ollama internals is asserted. No new inference was performed during finalization, and no accounting, scoring, or limit behavior was changed.

The sections below are historical verification records; their test counts, metadata limitations, and checksum status describe their respective earlier stages rather than this finalized baseline.

## Explicit thinking control patch — 27 September 2026 UTC

The runtime-only patch adds `--thinking {default,on,off}`, separate thinking diagnostics, metadata discovery, and stderr progress/heartbeat reporting. Suite schema and `spec_version` remain unchanged; historical run artifacts have not been edited. The original verification record below describes the packaged baseline.

`PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest discover -s tests -v` passed all **22 test methods** on Python 3.12 (22 tests in 1.843s). The initial sandbox run could not bind either HTTP fixture (`PermissionError: [Errno 1] Operation not permitted`); the final full suite passed with approved loopback access. New tests cover exact chat payloads through the CLI for omitted/default/on/off, separate thinking/content and history isolation, metadata and artifact recording, thinking-only/mostly-thinking truncation, unsupported/unverified controls without generation, a server returning thinking despite off, malformed thinking fields, replay-option rejection, and heartbeat shutdown.

Suite validation passed. Temporary nominal and failure replays retained the baseline results: exit 0 with 24 valid turns and 10/2 correction matches/mismatches; expected exit 1 with 21 valid turns and 7/2 correction matches/mismatches. Checksums for scenarios, examples, and historical verification artifacts remain unchanged. `SHA256SUMS` remains the original package checksum inventory; changed source, tests, and documentation intentionally no longer match that baseline inventory.

No new live Ollama/Qwen inference was performed. Explicit modes require advertised boolean support from `/api/show`; older servers with missing capability metadata fail clearly rather than guessing support. Tests establish request/recording behavior against fixtures, not that every runtime honors its advertised controls. Per-channel token counts remain unknown because the supported Ollama envelope provides only combined generation counts. Metadata requests use two-second socket timeouts, not a total wall-clock deadline.

Parent review, 25 September 2026 UTC. Python 3.12.14. This record concerns software behavior and fixture consistency; it does not establish construct validity or human benefit.

## Authorship and review

The parent agent wrote the specification, scenarios, fixture keys, assumption register, scenario review, and instructions. Three agents received bounded assignments and used GPT-6 Sol with low reasoning: runner/CLI, backend adapters, and independent behavioral tests. The independent test agent did not edit production code. The parent inspected the resulting implementation, requested corrections, and reran validation.

Review found and corrected an inappropriate cross-family answer total, an incomplete backend-envelope check, missing case-initialization failure handling, ambiguous attempted-request records, and transport failure handling. Replay provenance now includes a hash of the fixture bytes. These corrections were made before the packaged demonstration runs.

## Executed checks

`python3 -W error::ResourceWarning -m unittest discover -s tests -v` passed all **16 test methods**, including additional subcases. No resource warning appeared. The packaged copy was also extracted into a fresh temporary directory for the same test command and suite validation; see `verification_artifacts/package_checks.json`.

Coverage includes:

- Strict suite/response schemas; duplicate keys, invalid types, unknown fields, and nonfinite values are rejected.
- Gold keys and future turns remain outside requests; actual earlier outputs are preserved; histories reset between cases.
- Exact matching does not extract a number from prose, and a matching answer does not validate its explanation.
- Invalid, truncated, failed, context-limited, and blocked turns retain their statuses and appropriate denominators.
- Missing backend fields and case-initialization failures produce recorded errors.
- All substantive review values remain null; trace references, suite/spec hashes, and deterministic replay content are checked.
- Existing output is preserved; CLI exit codes distinguish execution, input failures, and answer mismatches.
- A local HTTP fixture checks Ollama request fields, response extraction, endpoint restrictions, rejected redirects, timeout, oversized/malformed/truncated envelopes, noninteger usage, and safe HTTP error reporting.

The suite's arithmetic keys were recomputed. Fictional evidence keys and matched personalization conditions were inspected against their supplied records; limitations are documented in `SCENARIO_REVIEW.md`.

## Full development-suite runs

Both included runs used the final packaged specification and fixture files. Each contains all seven output artifacts and 48 blank review tasks. `integration_results.json` records the assertions, environment, and execution results.

| Run | Planned turns | Structurally valid | Other statuses | Eligible correction checks | Exact matches / mismatches | Exit |
| --- | ---: | ---: | --- | ---: | --- | ---: |
| nominal_replay | 24 | 24 | None | 12 | 10 / 2 | 0 |
| failure_replay | 24 | 21 | 1 invalid response, 1 backend error, 1 blocked | 9 | 7 / 2 | 1 |

All 12 containment turns in each run have a null substantive automatic score. The nominal fixture deliberately includes two incorrect correction answers and one response that abandons the requested topic. Those are software examples, not observations of a model. Final-answer recovery does not erase the earlier mismatches. Failure does not become a favorable review value.

## Remaining limits

Live local-model inference was **not tested**: this environment had no Ollama executable and no reachable default Ollama service. The local transport passed fixture tests; an installed model's actual behavior and compatibility remain a separate acceptance check. No remote API backend is implemented or evaluated. No model weights were downloaded.

Python 3.12.14 was exercised; Python 3.10+ is the intended compatibility range, not a tested operating-system/runtime matrix. Tests and review provide bounded evidence, not a proof that no defects remain.

The eight public, English-language, author-created cases are not a representative sample. Structured JSON, seeded assistant history, simple tasks, profile wording, and unequal context lengths constrain interpretation. Model digest/server version remain unknown in pilot metadata; supplied seeds do not guarantee generation reproducibility. The context budget is measured in characters, and the transport timeout is a socket timeout rather than a total wall-clock deadline.

No human participants, delayed transfer, cultural validation, annotation reliability, or longitudinal dependency outcomes were studied. The review queue is an annotation proposal, not a calibrated scale. Do not use these runs to rank models, infer subgroup benefits, or claim that a simulator represents adults who need more learning support.
