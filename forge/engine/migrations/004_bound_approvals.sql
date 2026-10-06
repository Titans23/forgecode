CREATE TABLE approval_details(approval_id TEXT PRIMARY KEY REFERENCES approvals(id), owner_epoch TEXT NOT NULL,
  binding_json TEXT NOT NULL, database_revision INTEGER NOT NULL, environment_epoch INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','approved','denied','expired','consumed')));
CREATE TRIGGER immutable_approval_binding BEFORE UPDATE OF binding_json,owner_epoch,database_revision,environment_epoch ON approval_details
  BEGIN SELECT RAISE(ABORT,'approval binding is immutable'); END;
CREATE TABLE consumed_nonces(token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, target_id TEXT NOT NULL,
  binding_hash TEXT NOT NULL, owner_epoch TEXT NOT NULL, consumed_at_utc TEXT NOT NULL);
