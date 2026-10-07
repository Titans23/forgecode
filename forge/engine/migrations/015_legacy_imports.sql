CREATE TABLE legacy_imports(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),profile_id TEXT NOT NULL,
 source_key TEXT NOT NULL,source_path TEXT NOT NULL,source_sha256 TEXT NOT NULL,native_session_id TEXT NOT NULL,native_schema INTEGER NOT NULL,
 record_count INTEGER NOT NULL,backup_key TEXT NOT NULL,journal_state TEXT NOT NULL,imported_at_utc TEXT NOT NULL,
 UNIQUE(profile_id,source_key));
CREATE TRIGGER immutable_legacy_import BEFORE UPDATE ON legacy_imports BEGIN SELECT RAISE(ABORT,'legacy import mapping is immutable'); END;
