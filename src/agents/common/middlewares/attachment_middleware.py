"""附件注入中间件 - 使用 LangChain 标准中间件实现"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from src.utils import logger


class AttachmentState(AgentState):
    """扩展 AgentState 以支持附件"""

    attachments: NotRequired[list[dict]]


# 单个会话中所有附件合并后的最大字符限制
# 设为 64k 字符，防止占用过多上下文空间
MAX_TOTAL_ATTACHMENT_CHARS = 64000

def _build_attachment_prompt(attachments: Sequence[dict]) -> str | None:
    """Render attachments into a single system prompt block."""
    if not attachments:
        return None

    chunks: list[str] = []
    current_chars = 0
    
    for idx, attachment in enumerate(attachments, 1):
        if attachment.get("status") != "parsed":
            continue

        markdown = attachment.get("markdown")
        if not markdown:
            continue

        file_name = attachment.get("file_name") or f"附件 {idx}"
        
        # 如果已经超出总配额，则跳过后续附件
        if current_chars >= MAX_TOTAL_ATTACHMENT_CHARS:
            logger.warning(f"Attachment '{file_name}' skipped due to total size limit.")
            continue

        # 计算剩余配额
        remaining_quota = MAX_TOTAL_ATTACHMENT_CHARS - current_chars
        
        # 如果单个附件过长，进行截断
        is_truncated = attachment.get("truncated", False)
        if len(markdown) > remaining_quota:
            markdown = markdown[:remaining_quota] + "\n... (附件内容过长已截断) ..."
            is_truncated = True
        
        truncated_label = "（已截断）" if is_truncated else ""
        header = f"### 附件 {idx}: {file_name}{truncated_label}"
        chunk = f"{header}\n\n{markdown}".strip()
        
        chunks.append(chunk)
        current_chars += len(chunk)

    if not chunks:
        return None

    instructions = (
        "以下为用户提供的附件内容，请综合这些文件与用户的新问题进行回答。如附件与问题无关，可忽略附件内容：\n\n"
    )
    return instructions + "\n\n".join(chunks)


class AttachmentMiddleware(AgentMiddleware[AttachmentState]):
    """
    LangChain 标准中间件：从 State 中读取附件并注入到消息中。

    根据官方文档示例：
    https://docs.langchain.com/oss/python/langchain/middleware

    从 request.state 中读取 attachments，将其转换为 SystemMessage 并注入到消息列表开头。

    NOTE: 缺点是无法命中缓存了
    """

    state_schema = AttachmentState

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        # Read from State: get uploaded files metadata
        # logger.debug(f"inject_attachment_context: request.state = {request.state}")
        attachments = request.state.get("attachments", [])

        if attachments:
            # Build attachment context
            attachment_prompt = _build_attachment_prompt(attachments)

            if attachment_prompt:
                logger.debug(f"Injecting {len(attachments)} attachments into model request")

                # Inject attachment context at the beginning (as SystemMessage)
                # 注意：这是 transient update，不会修改 state，只影响本次模型调用
                messages = [
                    {"role": "system", "content": attachment_prompt},
                    *request.messages,
                ]
                request = request.override(messages=messages)

        return await handler(request)


# 创建中间件实例，供其他模块使用
inject_attachment_context = AttachmentMiddleware()
