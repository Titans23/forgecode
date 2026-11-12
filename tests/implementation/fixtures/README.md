# ForgeCode offline fixtures

Run `python scripts/harness_rpc_demo.py` from the repository using its Python environment. This development-only script copies fix-python-add into a new private directory, launches the real Engine and runs real tools/unittest. It preserves a demo-report.json, native Journal and SQLite data. `--output-dir` must name a new directory.

Only model responses are scripted. Mode is explicitly local-trusted, with no OS sandbox isolation. Results have origin=scripted and eligible_for_benchmark=false. They are not external model measurements, native-isolation acceptance or leaderboard grades.

Versioned manifests describe supplied inputs and required assertions. The corresponding tests execute or validate the relevant fixtures; a manifest alone does not establish acceptance. Scripts are tool-allowlisted, step-bounded and loaded only in the explicit legacy test profile. Test approvals cover only listed calls; production approval is a separate trusted host flow. Preserve fixture bytes and format versions independently of the ForgeCode product release.
