CREATE TABLE recovery_observations(id TEXT PRIMARY KEY,work_item_id TEXT NOT NULL REFERENCES work_items(id),
  owner_epoch TEXT NOT NULL,observed_at TEXT NOT NULL,report_json TEXT NOT NULL,UNIQUE(work_item_id,owner_epoch));
CREATE TRIGGER immutable_recovery_observation BEFORE UPDATE ON recovery_observations
  BEGIN SELECT RAISE(ABORT,'recovery observation is immutable'); END;
