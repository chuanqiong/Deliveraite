"""
清理 project_deliverables 表 extra_metadata.outline 中的 content 字段

运行方式：
docker compose exec api uv run python scripts/cleanup_outline_content.py
"""

import asyncio
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.storage.db.models import ProjectDeliverable
from src.storage.db.manager import db_manager
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def cleanup_outline_content():
    """清理所有交付物 outline 中的 content 字段"""
    async with db_manager.get_session() as db:
        try:
            # 查询所有有 outline 的交付物
            query = select(ProjectDeliverable).where(
                ProjectDeliverable.is_deleted == 0
            )
            result = await db.execute(query)
            deliverables = result.scalars().all()

            total_count = len(deliverables)
            updated_count = 0
            skipped_count = 0

            logger.info(f"找到 {total_count} 个交付物需要检查")

            for deliverable in deliverables:
                if not deliverable.extra_metadata:
                    skipped_count += 1
                    continue

                outline = deliverable.extra_metadata.get("outline")
                if not outline or not isinstance(outline, list):
                    skipped_count += 1
                    continue

                # 检查 outline 中是否有 content 字段
                has_content = any("content" in item for item in outline)

                if not has_content:
                    skipped_count += 1
                    continue

                # 清理 outline 中的 content 字段
                cleaned_outline = []
                for item in outline:
                    new_item = {k: v for k, v in item.items() if k != "content"}
                    cleaned_outline.append(new_item)

                # 更新 extra_metadata
                new_metadata = dict(deliverable.extra_metadata)
                new_metadata["outline"] = cleaned_outline
                deliverable.extra_metadata = new_metadata

                # 标记字段已修改
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(deliverable, "extra_metadata")

                updated_count += 1
                logger.info(f"已清理交付物 [{deliverable.id}] {deliverable.name} 的 outline content 字段")

            # 提交更改
            if updated_count > 0:
                await db.commit()
                logger.info(f"✅ 成功清理 {updated_count} 个交付物的 outline content 字段")
            else:
                logger.info("ℹ️ 没有需要清理的交付物")

            logger.info(f"📊 统计: 总计 {total_count} 个, 更新 {updated_count} 个, 跳过 {skipped_count} 个")

        except Exception as e:
            await db.rollback()
            logger.error(f"❌ 清理失败: {e}")
            raise


async def main():
    """主函数"""
    logger.info("开始清理 project_deliverables.extra_metadata.outline 中的 content 字段...")
    await cleanup_outline_content()
    logger.info("清理完成")


if __name__ == "__main__":
    asyncio.run(main())
