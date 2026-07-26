import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from storage.migrations import MIGRATIONS


class Database:
    """
    SQLite 连接、事务与迁移所有者
    """

    def __init__(self, path: Path) -> None:
        """
        初始化数据库路径

        :param path (Path): SQLite 文件路径
        """

        self._path = path

    def initialize(self) -> None:
        """
        创建数据库并按顺序应用待执行迁移

        :return None: 无返回值
        """

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied_versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            for version, script in MIGRATIONS:
                if version in applied_versions:
                    continue
                migration_script = (
                    "BEGIN IMMEDIATE;\n"
                    + script
                    + f"\nINSERT INTO schema_migrations (version, applied_at) VALUES ({version}, datetime('now'));\n"
                    + "COMMIT;"
                )
                try:
                    connection.executescript(migration_script)
                except sqlite3.Error:
                    connection.rollback()
                    raise
        os.chmod(self._path, 0o600)

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        打开启用约束和忙等待的事务连接

        :yields Connection: 自动提交或回滚的 SQLite 连接
        """

        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()
