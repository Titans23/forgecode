CREATE TABLE connections(id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision>=1), configuration_json TEXT NOT NULL);
CREATE TABLE budget_profiles(id TEXT PRIMARY KEY, configuration_json TEXT NOT NULL);
CREATE TABLE session_configurations(session_id TEXT PRIMARY KEY REFERENCES sessions(id), snapshot_id TEXT NOT NULL REFERENCES configuration_snapshots(id));
CREATE TABLE turn_results(turn_id TEXT PRIMARY KEY REFERENCES turns(id), native_outcome_json TEXT NOT NULL);
CREATE TRIGGER immutable_budget BEFORE UPDATE ON budget_profiles BEGIN SELECT RAISE(ABORT,'budget profile is immutable'); END;
CREATE TRIGGER immutable_native_outcome BEFORE UPDATE ON turn_results BEGIN SELECT RAISE(ABORT,'native outcome is immutable'); END;
