# ForgeCode repository instructions

Develop in D:/projects/forgecode on main. Preserve unrelated changes, credentials
and session data. Do not create another routine checkout.

Use FastCtx read, grep and glob with absolute paths when available; otherwise
use rg. Use apply_patch for semantic edits.

Read docs/implementation/architecture.md and progress.json, then the relevant
task card and backlog dependencies. Audit real interfaces before changing them.
Keep the existing Python Harness and CLI; do not resume retired desktop plans.

Prepare development dependencies with uv sync --locked --all-extras --group dev,
npm ci --ignore-scripts, and uv run --no-sync python scripts/build_bridge.py.
Run checks appropriate to the change. Add behavior tests for code changes;
document-only edits need reference and consistency checks, not new tests.
During development run changed behavior, failures and related modules. Once
stable, run the relevant regression once, push and check CI. Do not repeat the
full suite on Windows, WSL and CI by default; expand it for shared-core changes
or uncovered risks. Use JUnit timings to reduce duplicate setup, retaining
persistence, permissions, cancellation, contracts and data-isolation coverage.

Keep documentation small:
- README and docs/install/terminal-quickstart.md explain installation and use.
- architecture.md explains the current design; progress.json tracks current work.
- Task cards describe scope and dependencies, not chronological work logs.
- Update existing instructions when behavior changes. Do not create per-run
  acceptance reports, handoff diaries, authorization copies or summary documents.
- Generated test/audit reports belong under ignored .local/implementation or CI
  artifacts. Do not commit them. Preserve the two frozen experiment inputs
  referenced by experiments/delivery-repair.json without rewriting their bytes.

Keep implementation checks, native platform acceptance and live model experiments
distinct. Missing resources remain unverified; never substitute fake sandbox
results. Historical test counts do not establish current-source acceptance.

Do not spend model API budget or run administrator setup without explicit user
authorization. Uploading tested development versions to GitHub is authorized;
paid experiments and system setup are not.
