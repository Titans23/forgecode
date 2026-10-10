# ForgeCode repository instructions

Use FastCtx read, grep and glob with absolute paths for local inspection when
available. Use FastCtx replace for mechanical replacements and apply_patch for
semantic edits. Preserve unrelated user changes, credentials and session data.

## ForgeCode V4 implementation

Read `docs/implementation/PLANS.md`, `progress.json`, `handoff.md` and chapters
0–3 of `forgecode-v4.md` before continuing. Read each task card and its referenced
chapters. Begin with the real F00 audit, then follow `backlog.json` dependencies.
Keep the existing Python Harness and CLI; adapt their actual interfaces.

For each task add behavior tests, run regression, and update progress, task,
evidence and handoff. Implementation, native Windows/Linux acceptance and live
model experiments are separate. Record unavailable resources as blocked and
continue independent work. Never substitute fake sandbox results for native tests.

Do not spend model API budget or run administrator setup without explicit user
authorization. The current user has authorized uploading tested development
versions to GitHub; this does not authorize paid experiments or system setup.
