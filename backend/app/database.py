from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
import os
import threading
from pathlib import Path

SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./aquaculture.db"
)

if SQLALCHEMY_DATABASE_URL.startswith("sqlite:///"):
    db_path = SQLALCHEMY_DATABASE_URL.replace("sqlite:///", "")
    db_dir = Path(db_path).parent
    if str(db_dir) not in (".", ""):
        db_dir.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# 进程内写锁：FastAPI 同步端点在线程池中执行，冲正/更正/批次关闭串行化，
# 配合数据库唯一约束（幂等键、复核事实键）防止并发重复冲正。
write_lock = threading.RLock()

# 老库升级：为既有表补齐版本/签署列。SQLite 的 ALTER TABLE ADD COLUMN
# 不支持 IF NOT EXISTS，因此先查 PRAGMA，保证幂等可重复执行。
_ADDED_COLUMNS = {
    "ponds": [
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("superseded_by_id", "INTEGER"),
        ("void_reason", "VARCHAR(100)"),
    ],
    "batches": [
        ("signed_at", "DATETIME"),
        ("closed_by", "VARCHAR(100)"),
    ],
    "stocking_records": [
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("superseded_by_id", "INTEGER"),
        ("void_reason", "VARCHAR(100)"),
    ],
    "feeding_records": [
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("superseded_by_id", "INTEGER"),
        ("void_reason", "VARCHAR(100)"),
    ],
    "water_quality_records": [
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("superseded_by_id", "INTEGER"),
        ("void_reason", "VARCHAR(100)"),
    ],
    "medication_records": [
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("superseded_by_id", "INTEGER"),
        ("void_reason", "VARCHAR(100)"),
    ],
    "cost_records": [
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("superseded_by_id", "INTEGER"),
        ("void_reason", "VARCHAR(100)"),
    ],
    "harvest_sales": [
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("superseded_by_id", "INTEGER"),
        ("void_reason", "VARCHAR(100)"),
    ],
}


def bootstrap() -> None:
    """建表并对老库做幂等结构迁移。服务重启可安全重复执行。"""
    # 模型必须先导入，metadata 中才会登记新表
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl_type in columns:
                if name in present:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
