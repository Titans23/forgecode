CREATE TABLE turn_traces(turn_id TEXT PRIMARY KEY REFERENCES turns(id), trace_id TEXT NOT NULL UNIQUE,
  span_id TEXT NOT NULL, owner_epoch TEXT NOT NULL);
CREATE TRIGGER immutable_turn_trace BEFORE UPDATE ON turn_traces BEGIN SELECT RAISE(ABORT,'turn trace is immutable'); END;
CREATE TABLE execution_spans(execution_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id),
  trace_id TEXT NOT NULL, span_id TEXT NOT NULL, parent_span_id TEXT, UNIQUE(trace_id,span_id));
CREATE TABLE recovery_traces(turn_id TEXT NOT NULL REFERENCES turns(id), owner_epoch TEXT NOT NULL,
  trace_id TEXT NOT NULL, span_id TEXT NOT NULL, original_trace_id TEXT, PRIMARY KEY(turn_id,owner_epoch));
CREATE TABLE event_provenance(event_id TEXT PRIMARY KEY REFERENCES events(event_id), source_id TEXT NOT NULL,
  source_seq INTEGER NOT NULL, record_hash TEXT NOT NULL, trust TEXT NOT NULL CHECK(trust IN ('internal','imported')));
CREATE TRIGGER immutable_execution_span BEFORE UPDATE ON execution_spans BEGIN SELECT RAISE(ABORT,'execution span is immutable'); END;
CREATE TRIGGER immutable_event_provenance BEFORE UPDATE ON event_provenance BEGIN SELECT RAISE(ABORT,'event provenance is immutable'); END;
