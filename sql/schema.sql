-- Idempotent schema. Safe to run more than once.

CREATE TABLE IF NOT EXISTS departments (
    id         INTEGER PRIMARY KEY,
    department TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id  INTEGER PRIMARY KEY,
    job TEXT    NOT NULL
);

-- `datetime` is stored as TIMESTAMPTZ (UTC) so analytics can filter by range
-- and extract the quarter without parsing strings.
CREATE TABLE IF NOT EXISTS hired_employees (
    id            INTEGER     PRIMARY KEY,
    name          TEXT        NOT NULL,
    datetime      TIMESTAMPTZ NOT NULL,
    department_id INTEGER     NOT NULL REFERENCES departments (id),
    job_id        INTEGER     NOT NULL REFERENCES jobs (id)
);

CREATE INDEX IF NOT EXISTS idx_hired_employees_datetime   ON hired_employees (datetime);
CREATE INDEX IF NOT EXISTS idx_hired_employees_department ON hired_employees (department_id);
CREATE INDEX IF NOT EXISTS idx_hired_employees_job        ON hired_employees (job_id);

-- Every record that fails validation is stored here (and logged) instead of
-- being inserted.
CREATE TABLE IF NOT EXISTS rejected_records (
    id          BIGSERIAL   PRIMARY KEY,
    table_name  TEXT        NOT NULL,
    source      TEXT        NOT NULL,   -- csv | api | restore
    raw_record  JSONB       NOT NULL,
    reason      TEXT        NOT NULL,
    rejected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rejected_records_table ON rejected_records (table_name, rejected_at);
