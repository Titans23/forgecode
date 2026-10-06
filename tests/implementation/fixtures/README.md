# ForgeCode offline fixtures

Run `python -m forge.testing.demo` from the repository using its Python environment. The driver copies fix-python-add into a new private directory, launches the real Engine and runs real tools/unittest. It preserves a demo-report.json, native Journal and SQLite data. `--output-dir` must name a new directory.

Only model responses are scripted. Mode is explicitly local-trusted, with no OS sandbox isolation; native acceptance is blocked until the sandbox tasks. Results have origin=scripted and eligible_for_benchmark=false. They are not external model measurements or leaderboard grades.

Six versioned manifests describe supplied inputs and required assertions. F06 executes fix-python-add and validates all fixture structures. Other fixture acceptance remains pending its listed tasks; no placeholder case receives pass. Scripts are versioned, tool-allowlisted, step-bounded and loaded only in test profile. Test approvals cover only calls listed in that script; production approval is a separate trusted host flow.
