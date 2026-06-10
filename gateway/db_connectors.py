"""Connecteurs multi-moteur via SQLAlchemy Core (sync). Appeler depuis asyncio.to_thread."""
from __future__ import annotations
from dataclasses import dataclass
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

_SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "sys",
                   "mysql", "performance_schema", "INFORMATION_SCHEMA"}


@dataclass
class ColumnRef:
    db: str
    schema: str | None
    table: str
    object_type: str  # "table" | "view"
    column: str
    sql_type: str


def _build_url(source: dict, db: str | None):
    t = source["db_type"]
    opts = source.get("options") or {}
    if t == "sqlite":
        return f"sqlite:///{opts['sqlite_path']}"
    drivers = {"postgresql": "postgresql+psycopg2",
               "mysql": "mysql+pymysql",
               "sqlserver": "mssql+pyodbc"}
    query = {}
    if t == "sqlserver":
        query["driver"] = opts.get("odbc_driver", "ODBC Driver 18 for SQL Server")
        query["TrustServerCertificate"] = "yes"
    return URL.create(drivers[t], username=source.get("username"),
                      password=source.get("_password"), host=source.get("host"),
                      port=source.get("port"), database=db, query=query)


class Connector:
    def __init__(self, source: dict):
        self.source = source
        self.db_type = source["db_type"]

    def list_databases(self) -> list[str]:
        if self.db_type == "sqlite":
            return ["main"]
        eng = create_engine(_build_url(self.source, None))
        try:
            with eng.connect() as c:
                if self.db_type == "postgresql":
                    rows = c.execute(text("SELECT datname FROM pg_database WHERE datistemplate=false"))
                elif self.db_type == "mysql":
                    rows = c.execute(text("SHOW DATABASES"))
                else:  # sqlserver
                    rows = c.execute(text("SELECT name FROM sys.databases WHERE database_id>4"))
                return [r[0] for r in rows if r[0] not in _SYSTEM_SCHEMAS]
        finally:
            eng.dispose()

    def list_objects(self, db: str, include_views: bool = True) -> list[ColumnRef]:
        eng = create_engine(_build_url(self.source, None if self.db_type == "sqlite" else db))
        out: list[ColumnRef] = []
        try:
            insp = inspect(eng)
            schemas = [None] if self.db_type in ("sqlite", "mysql") else insp.get_schema_names()
            for schema in schemas:
                if schema in _SYSTEM_SCHEMAS:
                    continue
                tables = [(t, "table") for t in insp.get_table_names(schema=schema)]
                if include_views:
                    tables += [(v, "view") for v in insp.get_view_names(schema=schema)]
                for tname, otype in tables:
                    for col in insp.get_columns(tname, schema=schema):
                        out.append(ColumnRef(db=db, schema=schema, table=tname,
                                             object_type=otype, column=col["name"],
                                             sql_type=str(col["type"])))
        finally:
            eng.dispose()
        return out

    def _sample_query(self, ref: ColumnRef, n: int) -> str:
        # Qualifie avec le schéma si présent (sinon les colonnes hors schéma par
        # défaut — public/dbo — sont silencieusement ratées sur PG/SQL Server).
        if self.db_type == "sqlserver":
            tbl = f"[{ref.schema}].[{ref.table}]" if ref.schema else f"[{ref.table}]"
            return f'SELECT TOP {n} [{ref.column}] FROM {tbl}'
        tbl = f'"{ref.schema}"."{ref.table}"' if ref.schema else f'"{ref.table}"'
        return f'SELECT "{ref.column}" FROM {tbl} LIMIT {n}'

    def sample_values(self, ref: ColumnRef, n: int = 5) -> list[str]:
        url = _build_url(self.source, None if self.db_type == "sqlite" else ref.db)
        eng = create_engine(url)
        q = self._sample_query(ref, n)
        try:
            with eng.connect() as c:
                rows = c.execute(text(q))
                return [str(r[0]) for r in rows if r[0] is not None][:n]
        finally:
            eng.dispose()


def get_connector(source: dict) -> Connector:
    return Connector(source)
