from typing import Any

from src.agents.common import get_buildin_tools
from .document_tools import get_deliverable_tools


def get_tools() -> list[Any]:
    """获取交付物生成所需的所有工具"""
    tools = get_buildin_tools()
    tools.extend(get_deliverable_tools())
    return tools
