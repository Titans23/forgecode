CREATE TABLE turn_lifecycle(
 turn_id TEXT PRIMARY KEY REFERENCES turns(id), owner_epoch TEXT NOT NULL,
 agent_deadline_utc TEXT, grader_deadline_utc TEXT, environment_deadline_utc TEXT,
 cancel_state TEXT NOT NULL CHECK(cancel_state IN ('none','requested','confirmed','indeterminate')),
 cleanup_state TEXT NOT NULL CHECK(cleanup_state IN ('pending','running','clean','residual','unknown')),
 cleanup_json TEXT, finished_at_utc TEXT
);
