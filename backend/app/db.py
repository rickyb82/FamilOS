from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()

def _json_dumps(o) -> str:
    """Le colonne JSON accettano anche datetime/Path ecc. (serializzati come stringa) invece di far fallire l'INSERT."""
    import json
    return json.dumps(o, ensure_ascii=False, default=str)


_is_sqlite = settings.database_url.startswith("sqlite")
# SQLite: un solo scrittore alla volta. timeout=30 (invece dei 5 s di default) fa attendere il lock invece di fallire
# subito con "database is locked" quando un altro task sta scrivendo (indicizzazione RAG, salvataggi dagli editor…).
engine = create_async_engine(settings.database_url, echo=False, future=True, json_serializer=_json_dumps,
                             connect_args={"timeout": 30} if _is_sqlite else {})

if _is_sqlite:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # per ogni connessione: foreign key ON, attesa sui lock, WAL meno sincrono
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    from . import models  # noqa: F401  (registra i modelli)

    async with engine.begin() as conn:
        if settings.database_url.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(conn) -> None:
    """Mini-migrazione: aggiunge colonne definite nei modelli ma assenti nel DB (create_all non altera tabelle)."""
    from sqlalchemy import inspect

    insp = inspect(conn)
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            ctype = col.type.compile(dialect=conn.dialect)
            default = ""
            if col.default is not None and getattr(col.default, "is_scalar", False):
                v = col.default.arg
                default = f" DEFAULT {v!r}" if isinstance(v, str) else f" DEFAULT {int(v) if isinstance(v, bool) else v}"
            conn.exec_driver_sql(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ctype}{default}')
    # create_all ignora le tabelle gia' esistenti, indici compresi: gli indici dei modelli nascono anche sui DB vecchi
    for table in Base.metadata.sorted_tables:
        have = {i["name"] for i in insp.get_indexes(table.name)}
        for index in table.indexes:
            if index.name in have:
                continue
            cols = ", ".join(f'"{c.name}"' for c in index.columns)
            unique = "UNIQUE " if index.unique else ""
            conn.exec_driver_sql(f'CREATE {unique}INDEX IF NOT EXISTS "{index.name}" ON {table.name} ({cols})')
    # il contatore di Craft Sync esiste da subito (due prime richieste insieme non devono crearlo entrambe)
    from .models import SyncCounter
    if conn.execute(SyncCounter.__table__.select().where(SyncCounter.id == 1)).first() is None:
        conn.execute(SyncCounter.__table__.insert().values(id=1, value=0))
