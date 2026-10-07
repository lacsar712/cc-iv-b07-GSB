import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")

# 高照 / 低照两类曲线只能各自并联折算，不能混选
IRR_CLASSES = ("高照", "低照")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    irr_class text NOT NULL DEFAULT '高照',
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
CREATE TABLE IF NOT EXISTS iv_equiv_records (
    id serial PRIMARY KEY,
    result_scan_id integer NOT NULL REFERENCES iv_scans(id),
    irr_class text NOT NULL,
    item_count integer NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS iv_equiv_items (
    id serial PRIMARY KEY,
    record_id integer NOT NULL REFERENCES iv_equiv_records(id) ON DELETE CASCADE,
    source_scan_id integer NOT NULL REFERENCES iv_scans(id),
    weight double precision NOT NULL
);
CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""

# 存量库幂等迁移：基线库没有 irr_class 列
MIGRATIONS = (
    """ALTER TABLE iv_scans
       ADD COLUMN IF NOT EXISTS irr_class text NOT NULL DEFAULT '高照'""",
)


def init_schema(conn):
    conn.execute(SCHEMA)
    for stmt in MIGRATIONS:
        conn.execute(stmt)
