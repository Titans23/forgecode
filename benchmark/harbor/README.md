# ForgeCode Harbor evaluation

This integration evaluates the current ForgeCode checkout as an installed
Harbor agent. The adapter uses this workflow:

- stage only the package source needed to run the agent;
- initialize an isolated Git baseline without exposing verifier files;
- run one non-interactive ForgeCode turn in the task workspace;
- let Harbor's independent verifier assign the reward;
- for Aider Polyglot only, allow one same-session repair turn after a real
  verifier failure, matching Aider's two-attempt protocol.

The same installed agent can also be run on the other registered Harbor
datasets that match ForgeCode's terminal interface:

| Benchmark | Dataset | Runner | Repair protocol |
| --- | --- | --- | --- |
| Aider Polyglot | `aider-polyglot` | `python -m benchmark.harbor.run_aider` | one Aider feedback repair |
| SWE-bench Verified | `swe-bench/swe-bench-verified` | `python -m benchmark.harbor.run_swebench` | single attempt |
| Terminal-Bench 2 | `terminal-bench/terminal-bench-2` | `python -m benchmark.harbor.run_terminal` | single attempt |

Use [`../README.md`](../README.md) for the capability matrix and the required
report fields. The generic Dataset runner deliberately does not install the
Aider feedback plugin for SWE-bench or Terminal-Bench.

## Frozen public benchmark runs

Use the [direct official-image workflow](../README.md#terminal-bench-20直接使用官方镜像)
for Terminal-Bench runs. Do not pass `--prepare-only`, `--prepared-images` or
`--force-build` unless the user explicitly requests that mode. The default
installs ForgeCode inside each official task container without creating warmup
images. The Aider-specific build examples below are not this workflow.

Terminal-Bench 2.0 and SWE-bench Verified use Harbor **0.18.0** on Linux/WSL
with Docker. Choose exactly one scope: repeated `--task` IDs, `--n-tasks N`,
or `--all`. There is no implicit model or task subset. Defaults are concurrency
1, one attempt, zero task retries, 120 model requests, 240 tool calls and 1800
agent seconds per task; the official task deadline remains an upper bound.
Model settings use `ForgeConfig`: explicit options > process > project `.env`
> user `~/.forge/.env`. Unset context/output limits use the shared defaults
of 262144/65536 tokens; `--context-window` and `--max-output-tokens` override them. `--dry-run` shows effective settings
and prerequisites without downloading tasks or calling a model.

Runs and cache default to `~/.local/share/forgecode/evaluation/` (or XDG data
home). `--output-dir` selects a new run directory. `experiment.json` records
the resolved dataset digest, selected task checksums, source and lock hashes,
model settings, Harness budget and official verifier identity. `harbor.json`
launches those local frozen tasks; credentials remain environment templates.
The locked install, structured result and pre-verifier patch paths are enabled.
`--agent oracle` runs official reference solutions without a model; successful
offline tests alone do not establish native or live-model acceptance.

`forge --profile evaluation status <run-dir>` reads the owning controller;
`cancel` writes an idempotent request for that controller to stop its process
group and reconcile only resources with a matching Docker ownership receipt.
Ctrl+C uses the same stop path. Unconfirmed cleanup remains `unknown`, without
restarting paid tasks. `report` retains the raw official job result and task
rewards, alongside execution/grading/termination axes, nine outcome categories,
valid grading rate, elapsed time and tokens. All frozen tasks remain in the
denominator, including tasks that never started. A recovered provider error or
an ordinary nonzero tool exit does not replace the final terminal outcome.

The Journal now retains visible text chunks, incomplete tool arguments and
actual request snapshots, per-attempt retry/terminal metadata, and tool timing
and exit codes. `report` indexes private Journals, external payloads, patches,
verifier results and logs with hashes. Record closure and response completeness
are independent; missing response markers, inputs or tool results remain
incomplete even after a turn end. `export <run-dir> --format atif` validates
offline ATIF v1.7 using Harbor 0.18.0, writes a redacted sharing projection under
`exports/atif` (or a new `--output-dir`), and preserves original private records.
Public benchmark trajectories remain `evaluation-only`.

The generic dataset runner freezes package bytes once under
`<output-dir>/.source-snapshots/candidate-*/source` before starting Harbor.
The adjacent manifest records the Git revision and per-file/content hashes;
every trial and retry receives the same `source_dir`. Do not edit that snapshot.
A dirty worktree is identified by its content hash, not by the commit alone.
The kernel enforces the configured task deadline. The outer watchdog allows
15 seconds only for cancellation and journal cleanup, then forces termination
after a further 10 seconds if cleanup itself hangs.

`summarize` preserves official values in `raw_rewards` while separating valid
scored trials, observed timeouts, verifier bootstrap failures, and unfinished
trials. `job_finished=false` is not proof of a live Agent: inspect processes and
timestamp freshness before reporting a stalled job as running. A partial or
verifier-invalid job cannot qualify as either final acceptance round.

## Prerequisites

```powershell
uv sync
docker version
uv run harbor --version
uv run harbor dataset download aider-polyglot --cache
```

The public benchmark runner merges user, project and process model settings. When the configured provider is
on host `localhost`, it automatically rewrites the container endpoint to
`host.docker.internal` without changing `.env`.

## Smoke or stratified run

```powershell
uv run python -m benchmark.harbor.run_aider `
  --task polyglot_python_proverb `
  --task polyglot_javascript_space-age `
  --concurrency 2 `
  --output-dir benchmark/runs/harbor/stratified
```

Omit all `--task` arguments for the complete 225-task suite. Use
`--no-feedback` only when measuring a strict single-attempt variant.
Infrastructure exceptions are retried once by default; set `--max-retries 0`
to disable that behavior.

On Windows/Docker Desktop, use `--force-build` when a cached task image has a
stale language toolchain layer (the Aider Rust images can otherwise retain a
failed `rustc` PATH layer):

```powershell
uv run python -m benchmark.harbor.run_aider `
  --force-build `
  --concurrency 2 `
  --max-retries 1 `
  --ubuntu-mirror https://mirrors.aliyun.com/ubuntu `
  --output-dir benchmark/runs/harbor/full-225-luna-fixed
```

The ForgeCode Harbor adapter uploads the task prompt into the container and
passes `--message-file` to ForgeCode. This avoids Windows' command-line length
limit for long Polyglot prompts. Scope inference also treats the benchmark's
`supplied files:` declaration as authoritative, so prose such as “within a
grade” cannot create a fake `a/**` write scope.

If the official Ubuntu archive is unreliable from the current network, pass an
HTTPS package mirror, for example `--ubuntu-mirror
https://mirrors.aliyun.com/ubuntu`. This only changes where task images download
the same pinned Ubuntu packages; it does not modify exercise sources, tests, or
verifiers. Record the mirror in the run notes when using this option.

## Metrics

```powershell
uv run python -m benchmark.harbor.summarize `
  benchmark/runs/harbor/stratified/<job-timestamp> `
  --output benchmark/runs/harbor/stratified/summary.json
```

The summary separates infrastructure failures from scored trials and reports
pass@1, pass@2, successful repairs, ForgeCode internal statuses, model/tool
calls, and provider-reported token totals. A task that only passes after test
feedback counts in pass@2 but not pass@1.

Do not use the Aider feedback plugin with benchmarks whose official protocol
does not allow a verifier-feedback repair turn.

## Full 225-task run (2026-08-11/12)

The following sections preserve historical release-report values, not current
acceptance. Their provenance is the v0.1.1 report at Git commit
[`a9f290f`](https://github.com/Titans23/forgecode/blob/a9f290f3b00270ef787f6ed4a9b2ed87dc1722bb/EVALUATION_SUMMARY_20260813.md).
The referenced job/summary JSON and individual trials were not found in the
current checkout during the documentation audit. The release commit includes
post-run fixes; it does not identify the exact source bytes used by the run.
The old adapter staged source separately for each trial without the immutable
source manifest used by the current runner. Raw results, staged source and
request/attempt records are needed to independently revalidate these numbers.

Run directory: `benchmark/runs/harbor/full-225-luna-fixed/2026-08-11__22-30-48`.
The run used Harbor 0.18.0, Docker 29.7.2, the Aider Polyglot dataset at
commit `f30b14415dd733c83627204bad0af69a89ceb46f`, and model
`gpt-5.6-luna`. ForgeCode was evaluated with `--concurrency 2`, one retry,
`--force-build`, and the Aliyun Ubuntu mirror.

The job finished all 225 task records, but the configured provider became
unavailable during the long run. Harbor recorded 189 infrastructure errors:
100 `NetworkConnectionError`, 88 `ApiRateLimitError`, and one
`NonZeroAgentExitCodeError`. Therefore Harbor's raw aggregate mean (`0.1689`,
38 reward=1 and 77 reward=0 among 115 evaluator trials) is a service-constrained
number, not a reliable ForgeCode code-accuracy estimate. Among the 36 trials that completed without an exception, final reward
was 35/36 (pass@2 = 97.22%). The same subset had first-attempt pass@1 of
11/36 (30.56%) and 24 successful feedback repairs. By language, final reward
was C++ 4/5, Go 7/7, Java 10/10, JavaScript 8/8, Python 5/5, and Rust 1/1.
These are the most useful code-quality signals from this run, but they come
from a small, non-random survivor subset and must not be presented as the
full-suite accuracy.

The recorded rerun below used the adjusted setup/retry options. It is a
historical result, not a pending experiment; no additional paid run is scheduled.
Keep the all-task denominator as well as the separately labelled scored subset.

## Full 225-task run (2026-08-12/13, GPT-5.6 Luna)

Run directory: `benchmark/runs/harbor/full-225-luna-rerun/2026-08-12__11-53-03`.
The run used Harbor 0.18.0, Docker 29.7.2, dataset commit
`f30b14415dd733c83627204bad0af69a89ceb46f`, model `gpt-5.6-luna`,
`--concurrency 1`, `--max-retries 3`, `--force-build`, setup timeout multiplier
12, and eight installation retries. All 225 task records finished; one C++
trial (`polyglot_cpp_circular-buffer`) raised `RewardFileNotFoundError`, so 224
trials were code-scored.

Final verifier reward was 211/225 (raw Harbor mean `0.93778`), or 211/224 =
94.20% after excluding that infrastructure error. Strict first-attempt pass@1
was 97/224 = 43.30%; 114 tasks passed only after the permitted Aider feedback
repair. The low pass@1 is therefore not the final repaired-task accuracy.

By language, final reward / scored trials was C++ 24/25 (96.00%), Go 37/39
(94.87%), JavaScript 49/49 (100%), Java 39/47 (82.98%), Python 32/34
(94.12%), and Rust 30/30 (100%). Strict first-attempt rates were 32.00%,
35.90%, 69.39%, 19.15%, 23.53%, and 80.00%, respectively.

The 13 code-level final failures were Python forth and go-counting; Go
hexadecimal and robot-simulator; C++ crypto-square; and Java rational-numbers,
rest-api, sgf-parsing, forth, wordy, zebra-puzzle, ocr-numbers, and ledger.
The most actionable ForgeCode defects were incorrect task-scope anchoring for
`src/main/...` files, weak recovery after unavailable verification commands,
and incomplete handling of exact hidden-test APIs and multi-step concurrency
protocols.

The historical report records 42,090,702 input tokens and 885,602 output
tokens across 3,488 model calls and 3,840 tool calls. Provider cost was not
reported by the local proxy. After the run, scope canonicalization and
`pytest`/`python` verification fallback were added. The release report states
`448 passed` and a successful `uv lock --check` for those post-run fixes;
the exact tested worktree, command output and JUnit report are not available
here. That count is not current-source acceptance or model evaluation evidence.
The 211/225 result includes the allowed feedback repair (97/224 first-attempt
passes and 114 repairs) and must be labelled as a historical reported result,
not current accuracy or a measured improvement. Current CI and separately
sealed native task conditions are tracked in `docs/implementation/progress.json`.
