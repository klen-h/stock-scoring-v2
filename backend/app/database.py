"""
================================================================================
【文件作用】数据库核心模块
================================================================================

支持两种数据库：
  - SQLite（默认）：本地开发，数据文件存在 backend/data/app.db
  - PostgreSQL：生产部署（Supabase / Neon 等云数据库）

通过环境变量 DATABASE_URL 切换：
  - 不设置 / sqlite:///...  → SQLite
  - postgresql://user:pass@host/db  → PostgreSQL

使用方式：
  from app.database import db
  rows = db.fetch("SELECT * FROM flash_news ORDER BY time DESC LIMIT 10")
  db.execute("INSERT INTO flash_news (...) VALUES (...)", params)
================================================================================
"""

import os
import json
import sqlite3
import threading
from typing import List, Dict, Any, Optional, Tuple
from contextlib import contextmanager

# ── 数据库 URL 配置 ──
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


class Database:
    """
    数据库操作类。
    
    自动适配 SQLite / PostgreSQL：
      - SQLite：使用 sqlite3，线程锁保证并发安全
      - PostgreSQL：使用 psycopg2，连接池管理
    """
    
    def __init__(self):
        self._use_postgres = DATABASE_URL.startswith("postgresql://")
        self._local = threading.local()
        
        if self._use_postgres:
            print(f"[database] 使用 PostgreSQL 数据库")
        else:
            os.makedirs(DATA_DIR, exist_ok=True)
            self._db_path = os.path.join(DATA_DIR, "app.db")
            print(f"[database] 使用 SQLite 数据库: {self._db_path}")
    
    # ── 连接管理 ──
    
    def _get_sqlite_conn(self) -> sqlite3.Connection:
        """获取 SQLite 连接（线程本地单例）"""
        if not hasattr(self._local, 'sqlite_conn') or self._local.sqlite_conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")  # 并发读写优化
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.sqlite_conn = conn
        return self._local.sqlite_conn
    
    def _get_pg_conn(self):
        """获取 PostgreSQL 连接（线程本地单例）"""
        import psycopg2
        import psycopg2.extras
        if not hasattr(self._local, 'pg_conn') or self._local.pg_conn is None:
            self._local.pg_conn = psycopg2.connect(
                DATABASE_URL,
                cursor_factory=psycopg2.extras.RealDictCursor
            )
        return self._local.pg_conn
    
    @contextmanager
    def _get_conn(self):
        """获取数据库连接（上下文管理器）"""
        if self._use_postgres:
            conn = self._get_pg_conn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        else:
            conn = self._get_sqlite_conn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    
    # ── 基础操作 ──
    
    def execute(self, sql: str, params: tuple = None) -> int:
        """
        执行 SQL（INSERT/UPDATE/DELETE）。
        返回受影响的行数。
        """
        # SQLite 使用 ? 占位符，PostgreSQL 使用 %s
        if not self._use_postgres:
            sql = sql.replace("%s", "?")
        
        with self._get_conn() as conn:
            if self._use_postgres:
                with conn.cursor() as cur:
                    cur.execute(sql, params or ())
                    return cur.rowcount
            else:
                cur = conn.execute(sql, params or ())
                return cur.rowcount
    
    def fetch(self, sql: str, params: tuple = None) -> List[Dict]:
        """
        查询 SQL，返回字典列表。
        """
        if not self._use_postgres:
            sql = sql.replace("%s", "?")
        
        with self._get_conn() as conn:
            if self._use_postgres:
                with conn.cursor() as cur:
                    cur.execute(sql, params or ())
                    return [dict(row) for row in cur.fetchall()]
            else:
                cur = conn.execute(sql, params or ())
                columns = [desc[0] for desc in cur.description] if cur.description else []
                return [dict(zip(columns, row)) for row in cur.fetchall()]
    
    def fetch_one(self, sql: str, params: tuple = None) -> Optional[Dict]:
        """查询单条记录。"""
        results = self.fetch(sql, params)
        return results[0] if results else None
    
    def insert(self, table: str, data: Dict) -> int:
        """
        插入一条记录，返回 rowid。
        """
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        params = tuple(data.values())
        return self.execute(sql, params)
    
    def insert_many(self, table: str, items: List[Dict]) -> int:
        """批量插入，返回插入行数。"""
        if not items:
            return 0
        count = 0
        for item in items:
            self.insert(table, item)
            count += 1
        return count
    
    def upsert(self, table: str, data: Dict, conflict_columns: List[str]) -> int:
        """
        插入或更新（跨数据库兼容）。
        
        参数：
          table: 表名
          data: 要插入/更新的字典数据
          conflict_columns: 冲突时用于判断的列名列表
        """
        columns = list(data.keys())
        values = list(data.values())
        col_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        
        if self._use_postgres:
            conflict_cols = ", ".join(conflict_columns)
            update_cols = ", ".join(
                f"{c} = EXCLUDED.{c}" for c in columns if c not in conflict_columns
            )
            if update_cols:
                sql = (f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) "
                       f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {update_cols}")
            else:
                sql = (f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) "
                       f"ON CONFLICT ({conflict_cols}) DO NOTHING")
        else:
            sql = f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})"
        
        return self.execute(sql, tuple(values))
    
    # ── 初始化 ──
    
    def init_tables(self):
        """创建所有表（如果不存在）。"""
        schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        
        with self._get_conn() as conn:
            if self._use_postgres:
                with conn.cursor() as cur:
                    cur.execute(schema_sql)
            else:
                # SQLite 需要逐条执行
                conn.executescript(schema_sql)
        
        print("[database] 数据表初始化完成")
    
    def close(self):
        """关闭连接。"""
        if hasattr(self._local, 'sqlite_conn') and self._local.sqlite_conn:
            self._local.sqlite_conn.close()
            self._local.sqlite_conn = None
        if hasattr(self._local, 'pg_conn') and self._local.pg_conn:
            self._local.pg_conn.close()
            self._local.pg_conn = None


# ── 全局单例 ──
db = Database()
