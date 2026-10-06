CREATE TABLE validation_tickets(id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, spec_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL, consumed_run_id TEXT REFERENCES runs(id));
CREATE TABLE run_details(run_id TEXT PRIMARY KEY REFERENCES runs(id), profile_id TEXT NOT NULL,
  configuration_id TEXT NOT NULL REFERENCES configuration_snapshots(id), created_at TEXT NOT NULL);
CREATE TABLE attempt_details(attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), work_item_id TEXT NOT NULL UNIQUE REFERENCES work_items(id),
  owner_epoch TEXT, heartbeat_at TEXT, started_at TEXT, ended_at TEXT, elapsed_ns TEXT, start_monotonic_ns TEXT,
  trace_id TEXT, span_id TEXT, agent_outcome TEXT, authoritative_grade_id TEXT REFERENCES grades(id),
  terminal_reason TEXT, deadline_at TEXT, recovery_trace_id TEXT);
CREATE TABLE attempt_requests(request_id TEXT PRIMARY KEY REFERENCES model_requests(id), attempt_id TEXT NOT NULL REFERENCES attempts(id));
CREATE TABLE attempt_recovery(attempt_id TEXT NOT NULL REFERENCES attempts(id), owner_epoch TEXT NOT NULL,
  trace_id TEXT NOT NULL, span_id TEXT NOT NULL, original_trace_id TEXT, PRIMARY KEY(attempt_id,owner_epoch));
CREATE TRIGGER immutable_trial_plan BEFORE UPDATE OF run_id,task_id,task_revision,repeat_index ON trials
  BEGIN SELECT RAISE(ABORT,'trial plan is immutable'); END;
CREATE TRIGGER immutable_attempt_identity BEFORE UPDATE OF trial_id,attempt_no ON attempts
  BEGIN SELECT RAISE(ABORT,'attempt identity is immutable'); END;
CREATE TRIGGER immutable_final_grade BEFORE UPDATE ON grades WHEN OLD.state IN ('graded','grader_error')
  BEGIN SELECT RAISE(ABORT,'final grade is immutable'); END;
