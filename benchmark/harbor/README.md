# ForgeCode Harbor evaluation

This integration evaluates the current ForgeCode checkout as an installed
Harbor agent. It follows the same core protocol used by FirstCoder:

- stage only the package source needed to run the agent;
- initialize an isolated Git baseline without exposing verifier files;
- run one non-interactive ForgeCode turn in the task workspace;
- let Harbor's independent verifier assign the reward;
- for Aider Polyglot only, allow one same-session repair turn after a real
  verifier failure, matching Aider's two-attempt protocol.

## Prerequisites

```powershell
uv sync
docker version
uv run harbor --version
uv run harbor dataset download aider-polyglot --cache
```

The runner reads model settings from `.env`. When the configured provider is
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

For a publishable full-suite number, rerun after the provider cooldown
clears and quota is available, preferably with the new rate-aware setup
options:

```powershell
uv run python -m benchmark.harbor.run_aider `
  --concurrency 1 `
  --max-retries 3 `
  --agent-setup-timeout-multiplier 12 `
  --install-retries 8 `
  --install-retry-delay-seconds 30 `
  --force-build `
  --ubuntu-mirror https://mirrors.aliyun.com/ubuntu `
  --output-dir benchmark/runs/harbor/full-225-luna-rerun
```

Then report both the scored pass rates and the infrastructure-error rate. Do
not fold provider outages into code failures.

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

The run's raw token telemetry was 42,090,702 input tokens and 885,602 output
tokens across 3,488 model calls and 3,840 tool calls. Provider cost was not
reported by the local proxy. After the run, scope canonicalization and
`pytest`/`python` verification fallback were added; the local regression suite
passes 448 tests and `uv lock --check` succeeds. The 225-task number above is
the pre-rerun baseline; run a new full suite after these fixes for a direct
before/after accuracy comparison.
