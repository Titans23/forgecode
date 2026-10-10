CREATE TABLE artifact_profiles(artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id),profile_id TEXT NOT NULL,
  media_type TEXT NOT NULL,redaction_version TEXT NOT NULL);
INSERT INTO artifact_profiles SELECT x.artifact_id,d.profile_id,'application/octet-stream','metadata-v1'
  FROM artifact_attempts x JOIN attempts a ON a.id=x.attempt_id JOIN trials t ON t.id=a.trial_id JOIN run_details d ON d.run_id=t.run_id;
CREATE TABLE imported_bundles(profile_id TEXT NOT NULL,bundle_id TEXT NOT NULL,source_hash TEXT NOT NULL,
  manifest_json TEXT NOT NULL,run_ids_json TEXT NOT NULL,raw_artifact_id TEXT NOT NULL REFERENCES artifacts(id),PRIMARY KEY(profile_id,bundle_id));
CREATE TABLE imported_runs(id TEXT PRIMARY KEY,profile_id TEXT NOT NULL,source_run_id TEXT NOT NULL,
  spec_json TEXT NOT NULL,report_json TEXT NOT NULL,UNIQUE(profile_id,source_run_id));
CREATE TABLE imported_facts(profile_id TEXT NOT NULL,kind TEXT NOT NULL,source_id TEXT NOT NULL,hash TEXT NOT NULL,
  PRIMARY KEY(profile_id,kind,source_id));
CREATE TABLE bundle_conflicts(id TEXT PRIMARY KEY,profile_id TEXT NOT NULL,bundle_id TEXT NOT NULL,
  source_hash TEXT NOT NULL,existing_hash TEXT NOT NULL,incoming_hash TEXT NOT NULL,observed_at TEXT NOT NULL,
  quarantined_artifact_id TEXT NOT NULL REFERENCES artifacts(id));
CREATE TABLE bundle_exports(profile_id TEXT NOT NULL,client_action_id TEXT NOT NULL,params_hash TEXT NOT NULL,
  artifact_id TEXT NOT NULL REFERENCES artifacts(id),result_json TEXT NOT NULL,destination_json TEXT NOT NULL,
  PRIMARY KEY(profile_id,client_action_id));
CREATE TRIGGER immutable_imported_run BEFORE UPDATE ON imported_runs BEGIN SELECT RAISE(ABORT,'imported run is read-only'); END;
CREATE TRIGGER immutable_imported_fact BEFORE UPDATE ON imported_facts BEGIN SELECT RAISE(ABORT,'imported fact is immutable'); END;
