-- Accepted actions remain immutable. Observations live in their own bounded lifecycle.
CREATE TABLE connection_test_results (
    diagnostic_id TEXT PRIMARY KEY,
    owner_epoch TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('blocked','pass','fail')),
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TRIGGER immutable_connection_test_result
BEFORE UPDATE ON connection_test_results
WHEN OLD.finished_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'completed connection test is immutable');
END;
