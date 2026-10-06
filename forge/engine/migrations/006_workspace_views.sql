CREATE TABLE workspace_content (
 workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id), revision INTEGER NOT NULL,
 snapshot_json TEXT NOT NULL
);
CREATE TABLE workspace_change_log (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), revision INTEGER NOT NULL,
 relative_path TEXT NOT NULL, change TEXT NOT NULL,
 PRIMARY KEY(workspace_id,revision,relative_path)
);
CREATE TABLE turn_baselines (
 turn_id TEXT PRIMARY KEY REFERENCES turns(id), revision INTEGER NOT NULL,
 manifest_json TEXT NOT NULL, original_dirty_json TEXT NOT NULL, complete INTEGER NOT NULL
);
CREATE TRIGGER immutable_turn_baseline BEFORE UPDATE ON turn_baselines
BEGIN SELECT RAISE(ABORT,'turn baseline is immutable'); END;
CREATE TABLE turn_messages (
 turn_id TEXT NOT NULL REFERENCES turns(id), sequence INTEGER NOT NULL,
 kind TEXT NOT NULL, text TEXT NOT NULL, tool_name TEXT, status TEXT,
 PRIMARY KEY(turn_id,sequence)
);
