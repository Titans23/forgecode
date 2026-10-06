CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE store_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE configuration_snapshots(id TEXT PRIMARY KEY, hash TEXT NOT NULL UNIQUE, normalized_json TEXT NOT NULL);
CREATE TRIGGER immutable_configuration BEFORE UPDATE ON configuration_snapshots BEGIN SELECT RAISE(ABORT,'configuration snapshot is immutable'); END;
CREATE TABLE workspaces(id TEXT PRIMARY KEY, canonical_path TEXT NOT NULL, file_identity TEXT NOT NULL UNIQUE,
  trust TEXT NOT NULL CHECK(trust IN ('inspect_only','execution_allowed')), revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0));
CREATE TABLE sessions(id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), legacy_ref TEXT UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE actions(id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, method TEXT NOT NULL, client_action_id TEXT NOT NULL,
  params_hash TEXT NOT NULL, result_json TEXT NOT NULL, UNIQUE(profile_id,method,client_action_id));
CREATE TABLE turns(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), state TEXT NOT NULL
  CHECK(state IN ('queued','running','awaiting_approval','cancel_requested','reconciling','finished')),
  outcome TEXT CHECK(outcome IN ('completed','failed','cancelled','timed_out','budget_exhausted','blocked','indeterminate')),
  config_json TEXT NOT NULL, input_json TEXT NOT NULL, native_ref TEXT,
  CHECK((state='finished' AND outcome IS NOT NULL) OR (state!='finished' AND outcome IS NULL)));
CREATE TABLE work_items(id TEXT PRIMARY KEY, kind TEXT NOT NULL, business_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL
  CHECK(state IN ('queued','running','cancel_requested','reconciling','finished')),
  owner_epoch TEXT, deadline TEXT, version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0));
CREATE INDEX work_items_pending ON work_items(state,kind);
CREATE UNIQUE INDEX single_active_work_item ON work_items((1)) WHERE state='running';
CREATE TABLE policies(id TEXT PRIMARY KEY, hash TEXT NOT NULL UNIQUE, normalized_json TEXT NOT NULL, capability_requirement TEXT NOT NULL);
CREATE TABLE sandbox_sessions(id TEXT PRIMARY KEY, policy_hash TEXT NOT NULL REFERENCES policies(hash), owner TEXT NOT NULL,
  state TEXT NOT NULL, capabilities_json TEXT NOT NULL, cleanup_json TEXT);
CREATE TABLE approvals(id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id), execution_id TEXT NOT NULL,
  binding_hash TEXT NOT NULL, expires_at TEXT NOT NULL, decision TEXT CHECK(decision IN ('approve','deny')), consumed_at TEXT);
CREATE TABLE events(store_seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, source_id TEXT NOT NULL,
  source_seq INTEGER NOT NULL CHECK(source_seq>=1), body_json TEXT NOT NULL, hash TEXT NOT NULL,
  UNIQUE(source_id,source_seq));
CREATE INDEX events_source ON events(source_id,source_seq);
CREATE TABLE projection_offsets(source_id TEXT PRIMARY KEY, last_applied_seq INTEGER NOT NULL CHECK(last_applied_seq>=0));
CREATE TABLE producers(source_id TEXT PRIMARY KEY, producer_id TEXT NOT NULL UNIQUE);
CREATE TABLE event_conflicts(id TEXT PRIMARY KEY, event_id TEXT NOT NULL, source_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
  existing_hash TEXT NOT NULL, incoming_hash TEXT NOT NULL, observed_at TEXT NOT NULL);
CREATE TABLE artifacts(id TEXT PRIMARY KEY, relative_storage_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, size INTEGER NOT NULL CHECK(size>=0),
  origin TEXT NOT NULL, classification TEXT NOT NULL);
CREATE TABLE spans(trace_id TEXT NOT NULL, span_id TEXT NOT NULL, parent_id TEXT, start TEXT NOT NULL, end TEXT, attributes TEXT NOT NULL,
  PRIMARY KEY(trace_id,span_id));
CREATE TABLE context_snapshots(id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id), version INTEGER NOT NULL,
  before_ref TEXT REFERENCES artifacts(id), after_ref TEXT REFERENCES artifacts(id), reason TEXT NOT NULL, UNIQUE(turn_id,version));
CREATE TABLE evidence_refs(id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id), workspace_revision INTEGER NOT NULL,
  environment_epoch TEXT NOT NULL, result TEXT NOT NULL, artifact_id TEXT REFERENCES artifacts(id));
CREATE TABLE model_requests(id TEXT PRIMARY KEY, invocation_id TEXT NOT NULL, attempt_no INTEGER NOT NULL CHECK(attempt_no>=1),
  role TEXT NOT NULL, provider_request_id TEXT, state TEXT NOT NULL, UNIQUE(invocation_id,attempt_no));
CREATE TABLE usage_ledger(request_id TEXT PRIMARY KEY REFERENCES model_requests(id), raw_usage TEXT, normalized_usage TEXT,
  price_revision TEXT, cost TEXT, quality TEXT NOT NULL CHECK(quality IN ('actual','estimated','unknown')));
CREATE TABLE experiments(id TEXT PRIMARY KEY, description TEXT NOT NULL, comparison_protocol TEXT NOT NULL);
CREATE TABLE runs(id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id), spec_hash TEXT NOT NULL,
  spec_json TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE trials(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), task_id TEXT NOT NULL, task_revision TEXT NOT NULL,
  repeat_index INTEGER NOT NULL CHECK(repeat_index>=0), selected_attempt_id TEXT REFERENCES attempts(id),
  UNIQUE(run_id,task_id,task_revision,repeat_index));
CREATE TABLE attempts(id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id), attempt_no INTEGER NOT NULL CHECK(attempt_no>=1),
  execution_state TEXT NOT NULL CHECK(execution_state IN ('planned','queued','running','finished','cancelled','error','blocked')),
  error_origin TEXT, cleanup_state TEXT NOT NULL CHECK(cleanup_state IN ('pending','running','clean','residual','unknown')), UNIQUE(trial_id,attempt_no));
CREATE TABLE artifact_attempts(artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id), attempt_id TEXT NOT NULL REFERENCES attempts(id));
CREATE INDEX artifact_attempt_owner ON artifact_attempts(attempt_id);
CREATE TABLE grades(id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id), grader_hash TEXT NOT NULL,
  artifact_hash TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('unscored','grading','graded','grader_error')), reward TEXT,
  result TEXT CHECK(result IN ('pass','fail')), UNIQUE(attempt_id,grader_hash,artifact_hash));
CREATE TABLE annotations(id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id), author TEXT NOT NULL,
  category TEXT NOT NULL, evidence_refs TEXT NOT NULL, supersedes TEXT REFERENCES annotations(id));
CREATE TABLE exports(id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL, redaction_version TEXT NOT NULL, source_refs TEXT NOT NULL);
CREATE TRIGGER immutable_turn_input BEFORE UPDATE OF config_json,input_json,session_id ON turns
  BEGIN SELECT RAISE(ABORT,'turn input/configuration is immutable'); END;
CREATE TRIGGER valid_turn_transition BEFORE UPDATE OF state,outcome ON turns
  WHEN NOT (
    (OLD.state='queued' AND NEW.state IN ('running','cancel_requested','reconciling','finished')) OR
    (OLD.state='running' AND NEW.state IN ('awaiting_approval','cancel_requested','reconciling','finished')) OR
    (OLD.state='awaiting_approval' AND NEW.state IN ('running','cancel_requested','reconciling','finished')) OR
    (OLD.state='cancel_requested' AND NEW.state IN ('reconciling','finished')) OR
    (OLD.state='reconciling' AND NEW.state IN ('running','finished')) OR
    (OLD.state=NEW.state AND OLD.state!='finished' AND OLD.outcome IS NEW.outcome))
  BEGIN SELECT RAISE(ABORT,'invalid turn state transition'); END;
CREATE TRIGGER immutable_policy BEFORE UPDATE ON policies BEGIN SELECT RAISE(ABORT,'policy is immutable'); END;
CREATE TRIGGER immutable_run BEFORE UPDATE OF spec_hash,spec_json,experiment_id ON runs BEGIN SELECT RAISE(ABORT,'run spec is immutable'); END;
CREATE TRIGGER immutable_event BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'event is immutable'); END;
CREATE TRIGGER immutable_action BEFORE UPDATE OF params_hash,result_json,profile_id,method,client_action_id ON actions
  BEGIN SELECT RAISE(ABORT,'accepted action is immutable'); END;
CREATE TRIGGER immutable_artifact BEFORE UPDATE ON artifacts BEGIN SELECT RAISE(ABORT,'artifact is immutable'); END;
