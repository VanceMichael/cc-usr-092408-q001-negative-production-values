"""旧 SQLite 库的轻量结构演进。

- 新表（data_reviews / record_corrections / analysis_snapshots）由
  ``Base.metadata.create_all`` 自动创建；
- 旧库已有的业务表无法通过 ALTER 追加 CHECK 约束，因此负数历史行保留原样，
  由启动扫描登记为复核记录，而不是删除或重建表；
- 这里只补齐新版本引入的批次签署列。
"""

from sqlalchemy import inspect, text

# 表名 -> [(列名, 列定义)]
_ADDED_COLUMNS = {
    "batches": [
        ("signed_at", "DATETIME"),
        ("freeze_version", "INTEGER NOT NULL DEFAULT 0"),
    ],
}


def ensure_schema(engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table, columns in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue  # create_all 会按新模型建表，列已齐全
        present = {col["name"] for col in inspector.get_columns(table)}
        for column_name, column_ddl in columns:
            if column_name in present:
                continue
            with engine.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_ddl}")
                )
