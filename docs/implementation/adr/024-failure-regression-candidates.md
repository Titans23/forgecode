# ADR024 — Evidence-bound failure history and regression candidates

- Status: implemented, portable/development verification passed; native/public-model acceptance blocked
- Task: F24; parent HEAD 4082833ff7a81f6555e96111239c87ea32b5a854

Preserve the existing six-column annotations table and trusted host API. Append-only details record new human edits with author, UTC timestamp and supersedes; legacy time is unknown. Imported results remain immutable and unverified; local human edits are profile-bound sidecars. Expected-head CAS and durable action IDs reject stale edits and replay accepted responses.

Finite rules inspect structured recent events and execution/cleanup facts. They emit suggestions with actual basis IDs. They never establish causes, mutate annotations, or alter independent grades. Imported event claims do not enter trusted rule evidence.

A candidate is a redacted metadata fixture with a valid single-task RunSpec, immutable configuration fingerprint, grader/artifact references and human label. It requires the original source task, frozen snapshots and an authorized compatible executor; it is not a standalone copy of private code or a screenshot. Saving calls no model. Existing evaluation/CLI execution supplies real new attempts. Checking links an execution started after saving and verifies exact frozen conditions, separate trace identity, actual independent grader hash/failure, clean cleanup and complete readable original/new artifacts. Removing an artifact immediately revokes current reproducibility. Reproduced means independent grader failure reproduced; it never proves the human root-cause classification.

The optional annotation_details.jsonl bundle extension has a strict independent schema and relationship validation, preserves old bundle compatibility, and retains imported provenance. Historical edits stay in the database/export; UI reads bounded recent history. Main owns privacy confirmation and native candidate file export, checks artifact SHA and Engine/window identity, and never replaces existing bytes.

Windows 11/Ubuntu native and installed acceptance, manual native file dialogs and public paid-model reproduction remain blocked by existing environment/authorization gates. Deterministic offline models drive real Harness/Journal/tool calls and separate grader processes in tests; their failures are protocol fixtures, not benchmark scores.
