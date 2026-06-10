import sqlite3, tempfile, os
from gateway.db_connectors import get_connector

def _make_sqlite():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE clients (nom TEXT, email TEXT)")
    con.execute("INSERT INTO clients VALUES ('David Dupont','d@x.ch'),('Anna','a@y.ch')")
    con.execute("CREATE VIEW v_clients AS SELECT nom FROM clients")
    con.commit(); con.close()
    return path

def test_sqlite_list_objects_and_sample():
    path = _make_sqlite()
    conn = get_connector({"db_type": "sqlite", "options": {"sqlite_path": path}})
    cols = conn.list_objects(db="main", include_views=True)
    names = {(c.table, c.column) for c in cols}
    assert ("clients", "nom") in names
    assert ("clients", "email") in names
    assert ("v_clients", "nom") in names  # vue incluse
    ref = next(c for c in cols if c.table == "clients" and c.column == "nom")
    vals = conn.sample_values(ref, n=5)
    assert "David Dupont" in vals

def test_sqlite_exclude_views():
    path = _make_sqlite()
    conn = get_connector({"db_type": "sqlite", "options": {"sqlite_path": path}})
    cols = conn.list_objects(db="main", include_views=False)
    assert all(c.table != "v_clients" for c in cols)


def test_sample_query_qualifies_schema():
    """Régression : sample_values doit qualifier la table avec le schéma."""
    from gateway.db_connectors import Connector, ColumnRef
    ref = ColumnRef(db="d", schema="reporting", table="t", object_type="table",
                    column="c", sql_type="text")
    pg = Connector({"db_type": "postgresql"})._sample_query(ref, 5)
    assert '"reporting"."t"' in pg and '"c"' in pg and "LIMIT 5" in pg
    ss = Connector({"db_type": "sqlserver"})._sample_query(ref, 5)
    assert "[reporting].[t]" in ss and "TOP 5" in ss
    # sans schéma : pas de préfixe
    ref2 = ColumnRef(db="d", schema=None, table="t", object_type="table",
                     column="c", sql_type="text")
    assert '"reporting"' not in Connector({"db_type": "postgresql"})._sample_query(ref2, 5)
