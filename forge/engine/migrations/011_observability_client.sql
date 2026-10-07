ALTER TABLE turn_messages ADD COLUMN execution_id TEXT;
ALTER TABLE span_details ADD COLUMN first_chunk_monotonic TEXT;
CREATE TABLE turn_profiles(turn_id TEXT PRIMARY KEY REFERENCES turns(id),profile_id TEXT NOT NULL);
INSERT INTO turn_profiles SELECT t.id,MIN(a.profile_id) FROM turns t JOIN actions a
 ON json_extract(a.result_json,'$.turn_id')=t.id AND a.method IN ('session.start_turn','session.submit')
 GROUP BY t.id HAVING COUNT(DISTINCT a.profile_id)=1;
CREATE TRIGGER immutable_turn_profile BEFORE UPDATE ON turn_profiles BEGIN SELECT RAISE(ABORT,'turn profile is immutable'); END;
CREATE TABLE tool_output_views(execution_id TEXT PRIMARY KEY REFERENCES execution_spans(execution_id),source_sha256 TEXT NOT NULL,
 artifact_id TEXT NOT NULL REFERENCES artifacts(id));
