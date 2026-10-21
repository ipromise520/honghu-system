from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import DATABASE_URL

# timeout=30：写锁等待 30s（配合 busy_timeout），避免 "database is locked"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


@event.listens_for(engine, "connect")
def _sqlite_pragma(dbapi_conn, _conn_record):
    """每个连接启用 WAL：读写并发互不阻塞；忙等待 30s；WAL 下 synchronous=NORMAL 安全且更快。
    journal_mode 为持久化设置，首次执行后入库，后续连接重复执行无副作用。"""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
