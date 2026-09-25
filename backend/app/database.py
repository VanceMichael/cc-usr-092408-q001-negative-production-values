from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from pathlib import Path

SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./aquaculture.db"
)

_IS_SQLITE = SQLALCHEMY_DATABASE_URL.startswith("sqlite")

if _IS_SQLITE:
    db_path = SQLALCHEMY_DATABASE_URL.replace("sqlite:///", "", 1)
    if db_path and db_path not in (":memory:",):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} if _IS_SQLITE else {},
)

if _IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        # 重启后复核进度与计算结果仍可重放：WAL 保证写入落盘且读写并发不丢。
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        # 并发认领/冲正抢锁时等待而非立即 database is locked。
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
