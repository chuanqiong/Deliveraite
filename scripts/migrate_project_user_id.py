#!/usr/bin/env python3
"""
项目表字段迁移脚本
为现有项目添加 user_id 字段
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到Python路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from src.storage.db.manager import db_manager

def migrate_project_user_id():
    """执行项目字段迁移"""
    print("开始项目字段迁移...")

    # 获取数据库引擎
    engine = db_manager.engine
    
    with engine.connect() as conn:
        print("检查并添加 user_id 字段到 projects 表...")
        
        # 对于 SQLite，我们尝试直接添加字段
        try:
            conn.execute(text("ALTER TABLE projects ADD COLUMN user_id VARCHAR(64)"))
            conn.commit()
            print("成功为 projects 表添加 user_id 字段")
        except Exception as e:
            if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                print("projects.user_id 字段已存在")
            else:
                print(f"添加字段失败: {e}")
                return

        # 为现有项目设置默认 user_id (假设为第一个管理员或默认用户)
        print("为现有项目设置默认 user_id...")
        try:
            # 尝试获取一个有效的用户 ID
            result = conn.execute(text("SELECT user_id FROM users LIMIT 1"))
            default_user = result.fetchone()
            
            if default_user:
                default_user_id = default_user[0]
                print(f"使用默认用户 ID: {default_user_id}")
                
                # 更新所有 user_id 为 NULL 的项目
                update_result = conn.execute(
                    text("UPDATE projects SET user_id = :user_id WHERE user_id IS NULL"),
                    {"user_id": default_user_id}
                )
                conn.commit()
                print(f"已更新 {update_result.rowcount} 条项目记录")
            else:
                print("未找到任何用户，无法设置默认 user_id")
        except Exception as e:
            print(f"设置默认 user_id 失败: {e}")

        # 添加索引
        print("创建索引...")
        try:
            conn.execute(text("CREATE INDEX idx_projects_user_id ON projects(user_id)"))
            conn.commit()
            print("成功创建 idx_projects_user_id 索引")
        except Exception as e:
            print(f"创建索引失败或已存在: {e}")

    print("项目字段迁移完成")

if __name__ == "__main__":
    migrate_project_user_id()
