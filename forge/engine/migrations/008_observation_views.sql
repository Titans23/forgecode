ALTER TABLE usage_ledger ADD COLUMN currency TEXT;
ALTER TABLE usage_ledger ADD COLUMN cost_quality TEXT NOT NULL DEFAULT 'unknown' CHECK(cost_quality IN ('actual','estimated','unknown'));
CREATE TABLE request_details(request_id TEXT PRIMARY KEY REFERENCES model_requests(id), turn_id TEXT REFERENCES turns(id),
  workspace_id TEXT,session_id TEXT,run_id TEXT,trace_id TEXT,span_id TEXT,provider TEXT NOT NULL,requested_model TEXT NOT NULL,pricing_snapshot TEXT);
CREATE TRIGGER immutable_request_details BEFORE UPDATE ON request_details BEGIN SELECT RAISE(ABORT,'request pricing and attribution are immutable'); END;
CREATE TABLE span_details(trace_id TEXT NOT NULL,span_id TEXT NOT NULL,workspace_id TEXT,session_id TEXT,turn_id TEXT,run_id TEXT,
  name TEXT NOT NULL,state TEXT NOT NULL,first_chunk_at TEXT,start_monotonic TEXT,end_monotonic TEXT,metadata_json TEXT NOT NULL,
  PRIMARY KEY(trace_id,span_id),FOREIGN KEY(trace_id,span_id) REFERENCES spans(trace_id,span_id));
ALTER TABLE context_snapshots ADD COLUMN metadata_json TEXT;
ALTER TABLE context_snapshots ADD COLUMN snapshot_ref TEXT;
CREATE TABLE evidence_details(id TEXT PRIMARY KEY REFERENCES evidence_refs(id),metadata_json TEXT NOT NULL,validity TEXT NOT NULL,
  reason TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE export_queue(event_id TEXT PRIMARY KEY REFERENCES events(event_id),destination TEXT NOT NULL,body_json TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),attempts INTEGER NOT NULL CHECK(attempts>=0),retry_at REAL NOT NULL,last_error TEXT);
CREATE TABLE export_failures(reason TEXT PRIMARY KEY,count INTEGER NOT NULL,last_at REAL NOT NULL);
CREATE TABLE turn_observation_config(turn_id TEXT PRIMARY KEY REFERENCES turns(id),pricing_snapshot TEXT,capture_mode TEXT NOT NULL);
CREATE TRIGGER immutable_turn_observation_config BEFORE UPDATE ON turn_observation_config BEGIN SELECT RAISE(ABORT,'turn observation configuration is immutable'); END;
INSERT INTO request_details(request_id,turn_id,workspace_id,session_id,run_id,trace_id,span_id,provider,requested_model)
 SELECT m.id,json_extract(e.body_json,'$.turn_id'),json_extract(e.body_json,'$.workspace_id'),json_extract(e.body_json,'$.session_id'),
 json_extract(e.body_json,'$.run_id'),json_extract(e.body_json,'$.trace_id'),json_extract(e.body_json,'$.span_id'),
 COALESCE(json_extract(e.body_json,'$.attributes.provider'),'unreported'),COALESCE(json_extract(e.body_json,'$.attributes.requested_model'),'unreported')
 FROM model_requests m JOIN events e ON json_extract(e.body_json,'$.attributes.model_request_id')=m.id
 WHERE json_extract(e.body_json,'$.event_type')='model.request.started' AND json_extract(e.body_json,'$.origin')='trusted_engine';
INSERT INTO span_details(trace_id,span_id,workspace_id,session_id,turn_id,run_id,name,state,metadata_json)
 SELECT s.trace_id,s.span_id,json_extract(e.body_json,'$.workspace_id'),json_extract(e.body_json,'$.session_id'),
 json_extract(e.body_json,'$.turn_id'),json_extract(e.body_json,'$.run_id'),json_extract(e.body_json,'$.event_type'),
 CASE WHEN s.end IS NULL THEN 'running' WHEN json_extract(s.attributes,'$."forge.event.type"') LIKE '%.failed' THEN 'error' ELSE 'ok' END,
 json_extract(e.body_json,'$.attributes') FROM spans s JOIN events e ON e.store_seq=(SELECT MIN(first.store_seq) FROM events first
 WHERE json_extract(first.body_json,'$.trace_id')=s.trace_id AND json_extract(first.body_json,'$.span_id')=s.span_id
 AND json_extract(first.body_json,'$.origin') IN ('trusted_engine','trusted_bridge','grader_adapter'));
