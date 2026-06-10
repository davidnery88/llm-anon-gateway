import sqlite3, tempfile, os, pytest
from gateway.db_connectors import get_connector
from gateway.scanner import run_scan

class FakeLabels:
    def __init__(self): self.rows = []; self.active = set()
    async def exists_active(self, h, db, t): return (h, db, t) in self.active
    async def upsert_ctx(self, header, label, source, confidence, status, db_name, table_name, sample_values=None):
        self.rows.append((header, label, status, db_name, table_name))
        if status == "active": self.active.add((header, db_name, table_name))
        return {}

class FakeJobs:
    def __init__(self): self.updates = []; self.finished = None
    async def update_job(self, job_id, **f): self.updates.append(f)
    async def finish_job(self, job_id, status, error=None): self.finished = (status, error)

class FakeClassifier:
    async def classify(self, table, column, sql_type, values=None, value_metadata=None):
        return ("EMAIL" if column == "email" else "PERSONNE", 0.95, None)

def _sqlite():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd)
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE clients (nom TEXT, email TEXT)")
    con.execute("INSERT INTO clients VALUES ('David','d@x.ch')")
    con.commit(); con.close(); return p

@pytest.mark.asyncio
async def test_run_scan_populates_labels():
    path = _sqlite()
    conn = get_connector({"db_type": "sqlite", "options": {"sqlite_path": path}})
    labels, jobs = FakeLabels(), FakeJobs()
    await run_scan(connector=conn, classifier=FakeClassifier(), labels=labels, jobs=jobs,
                   job_id=1, dbs=["main"], include_views=True, threshold=0.7)
    cols = {r[0] for r in labels.rows}
    assert "nom" in cols and "email" in cols
    assert jobs.finished == ("done", None)

@pytest.mark.asyncio
async def test_run_scan_skips_active():
    path = _sqlite()
    conn = get_connector({"db_type": "sqlite", "options": {"sqlite_path": path}})
    labels, jobs = FakeLabels(), FakeJobs()
    labels.active.add(("nom", "main", "clients"))
    await run_scan(connector=conn, classifier=FakeClassifier(), labels=labels, jobs=jobs,
                   job_id=1, dbs=["main"], include_views=True, threshold=0.7)
    assert all(r[0] != "nom" for r in labels.rows)
