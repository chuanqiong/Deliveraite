"""
交付物智能体上下文配置
"""
from dataclasses import dataclass, field
from typing import Annotated
from src.agents.common import BaseContext, gen_tool_info
from src.agents.common.mcp import MCP_SERVERS
from .tools import get_tools


@dataclass(kw_only=True)
class DeliverableContext(BaseContext):
    """交付物生成智能体的上下文配置

    Attributes:
        projectId: 项目 ID
        deliverableId: 交付物 ID
        mode: 协作模式（global: 全局模式, local: 局部模式）
        activeSectionId: 当前激活的章节 ID（局部模式下使用）
        documentStructure: 文档结构列表（大纲）
        kb_files: 项目关联的知识库文件列表（用于生成大纲）
        deliverableType: 交付物类型（如：投标文件、需求报告、技术方案等）
        projectContext: 项目背景信息（包含行业领域、技术栈等）
        targetWords: 目标字数
        existingOutline: 已有大纲（用于扩展或优化）
        system_prompt: 用户传入的额外系统提示（如风格要求）
    """
    tools: Annotated[list[str], {"__template_metadata__": {"kind": "tools"}}] = field(
        default_factory=list,
        metadata={
            "name": "工具",
            "options": gen_tool_info(get_tools()),  # 这里的选择是所有的工具
            "description": "工具列表",
        },
    )

    mcps: list[str] = field(
        default_factory=list,
        metadata={"name": "MCP服务器", "options": list(MCP_SERVERS.keys()), "description": "MCP服务器列表"},
    )

    # 基础字段（向后兼容）
    projectId: str = field(default=None)
    deliverableId: str = field(default=None)
    status: str = field(default=None) # 交付物状态：未撰写/已撰写
    mode: str = field(default="global")
    activeSectionId: str = field(default=None)
    documentStructure: list = field(default_factory=list)

    # 场景标识（可选，用于明确指定当前场景）
    # 如果不指定，系统会根据 context 信息自动检测
    # 支持的值：outline(大纲生成)/writing(章节撰写)/polish(内容润色)/draft(生成初稿)
    scenario: str = field(
        default=None,
        metadata={
            "name": "场景标识",
            "description": "明确指定当前场景：outline(大纲生成)/writing(章节撰写)/polish(内容润色)/draft(生成初稿)",
        }
    )

    # 扩展字段（用于增强大纲生成）
    kb_files: list = field(default_factory=list)  # 知识库文件列表
    deliverableType: str = field(default=None)  # 交付物类型
    projectContext: dict = field(default_factory=dict)  # 项目背景信息
    targetWords: int = field(default=None)  # 目标字数
    existingOutline: list = field(default_factory=list)  # 已有大纲
    system_prompt: str = field(default=None)  # 额外系统提示

    # 覆盖基类默认值，使用交付物撰写的最佳配置
    # 平衡创造性和准确性，适合专业文档生成
    temperature: float = field(default=0.7)
    # 保证一定多样性，避免重复
    top_p: float = field(default=0.9)

