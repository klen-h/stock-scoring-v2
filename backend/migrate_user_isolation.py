"""
================================================================================
【文件作用】用户隔离迁移脚本
================================================================================

将现有的 user_watchlist / user_trade_plans / user_portfolio 表迁移为按 user_id 隔离。

迁移逻辑：
  1. 创建 users 表（如果不存在）
  2. 创建默认用户 "admin"（密码: admin123，首次登录后请修改）
  3. 给三张用户数据表添加 user_id 列（如果不存在）
  4. 将现有数据分配给默认用户
  5. 重建唯一约束

运行方式：
  python migrate_user_isolation.py
================================================================================
"""

import os
import sys

# 确保可以导入 backend 目录
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()


def migrate():
    from app.database import db
    from app.auth import hash_password
    
    print("[迁移] 开始用户隔离迁移...")
    
    # 1. 创建 users 表
    print("[迁移] 创建 users 表...")
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 2. 创建默认用户
    default_user = db.fetch_one("SELECT id FROM users WHERE username = 'admin'")
    if not default_user:
        print("[迁移] 创建默认用户 admin (密码: admin123)...")
        db.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
            ("admin", hash_password("admin123"))
        )
        default_user = db.fetch_one("SELECT id FROM users WHERE username = 'admin'")
    
    default_user_id = default_user["id"]
    print(f"[迁移] 默认用户 ID: {default_user_id}")
    
    # 3. 检查并添加 user_id 列
    tables_to_migrate = [
        ("user_watchlist", "code"),
        ("user_trade_plans", "id"),
        ("user_portfolio", "code"),
    ]
    
    for table, pk_col in tables_to_migrate:
        print(f"[迁移] 检查表 {table}...")
        
        # 检查 user_id 列是否存在
        try:
            if db._use_postgres:
                col_check = db.fetch_one("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = %s AND column_name = 'user_id'
                """, (table,))
            else:
                col_check = db.fetch_one(f"PRAGMA table_info({table})")
                # SQLite 需要遍历结果
            
            has_user_id = col_check is not None
            
        except Exception:
            has_user_id = False
        
        if not has_user_id:
            print(f"[迁移] 表 {table} 添加 user_id 列...")
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
            except Exception as e:
                print(f"[迁移] 添加 user_id 列失败（可能已存在）: {e}")
        
        # 4. 将现有数据的 user_id 设为默认用户
        print(f"[迁移] 将 {table} 现有数据分配给默认用户...")
        try:
            db.execute(f"UPDATE {table} SET user_id = %s WHERE user_id IS NULL", (default_user_id,))
        except Exception as e:
            print(f"[迁移] 更新 user_id 失败: {e}")
    
    # 5. 设置 user_id 为 NOT NULL（PostgreSQL）
    if db._use_postgres:
        for table, _ in tables_to_migrate:
            try:
                db.execute(f"ALTER TABLE {table} ALTER COLUMN user_id SET NOT NULL")
            except Exception as e:
                print(f"[迁移] 设置 NOT NULL 失败（可能已设置）: {e}")
    
    # 6. 删除旧的唯一约束并创建新的
    if db._use_postgres:
        # user_watchlist: 旧约束 code UNIQUE，新约束 (user_id, code) UNIQUE
        try:
            db.execute("ALTER TABLE user_watchlist DROP CONSTRAINT IF EXISTS user_watchlist_code_key")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE user_watchlist ADD CONSTRAINT user_watchlist_user_id_code_key UNIQUE (user_id, code)")
        except Exception as e:
            print(f"[迁移] 创建 user_watchlist 唯一约束失败: {e}")
        
        # user_portfolio: 旧约束 code UNIQUE，新约束 (user_id, code) UNIQUE
        try:
            db.execute("ALTER TABLE user_portfolio DROP CONSTRAINT IF EXISTS user_portfolio_code_key")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE user_portfolio ADD CONSTRAINT user_portfolio_user_id_code_key UNIQUE (user_id, code)")
        except Exception as e:
            print(f"[迁移] 创建 user_portfolio 唯一约束失败: {e}")
    
    print("[迁移] 用户隔离迁移完成！")
    print(f"[迁移] 默认用户: admin / admin123 （请尽快修改密码）")


if __name__ == "__main__":
    migrate()
