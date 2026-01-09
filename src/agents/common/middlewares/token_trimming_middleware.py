"""消息历史修剪中间件 - 防止超出模型上下文限制"""

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call
from src.utils.logging_config import logger

# 默认最大字符限制（保守估计，对应约 80k-100k tokens）
# 模型限制通常为 128k tokens (131072)
# 180000 字符在全中文情况下可能接近 100k-120k tokens
DEFAULT_MAX_CHARS = 120000 

def _get_msg_len(msg: dict | Any) -> int:
    """估算单条消息的字符长度，包括 content 和 tool_calls"""
    length = 0
    if isinstance(msg, dict):
        content = msg.get("content", "")
        length += len(str(content))
        
        # 统计 tool_calls
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            length += len(str(tool_calls))
            
        # 统计 function_call (旧版 API)
        function_call = msg.get("function_call")
        if function_call:
            length += len(str(function_call))
    else:
        # 如果是 LangChain 消息对象
        content = getattr(msg, "content", "")
        length += len(str(content))
        
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            length += len(str(tool_calls))
            
    return length

@wrap_model_call
async def token_trimming_middleware(request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
    """
    对消息历史进行修剪，确保不超出模型的上下文限制。
    
    策略：
    1. 保留 System Message。
    2. 保持 tool 消息与其对应的 tool_calls 消息成对出现。
    3. 对中间的历史消息进行修剪。
    """
    messages = list(request.messages)
    if not messages:
        return await handler(request)

    # 1. 计算当前总长度
    total_len = sum(_get_msg_len(m) for m in messages)
    
    if total_len <= DEFAULT_MAX_CHARS:
        return await handler(request)

    logger.warning(
        "Message history length ({}) exceeds limit ({}). Trimming...",
        total_len, DEFAULT_MAX_CHARS
    )

    # 2. 修剪策略
    trimmed_messages = []
    
    # 找到所有 System 消息
    system_indices = []
    for idx, m in enumerate(messages):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "type", None)
        if role == "system":
            system_indices.append(idx)
    
    # 始终保留最新的 System Message (通常是最后一个系统消息)
    system_msg = None
    if system_indices:
        last_system_idx = system_indices[-1]
        system_msg = messages[last_system_idx]
        # 从待处理的消息列表中移除所有系统消息
        remaining_messages = [m for idx, m in enumerate(messages) if idx not in system_indices]
    else:
        # 如果没有明确的系统消息，保留第一个消息作为 fallback
        system_msg = messages[0]
        remaining_messages = messages[1:]

    trimmed_messages.append(system_msg)

    # 始终保留最后一个消息（通常是当前的 User 请求或最新的 Tool 响应）
    last_msg = None
    if remaining_messages:
        last_msg = remaining_messages.pop()
    
    # 计算已保留消息的长度
    current_total = _get_msg_len(system_msg) + (_get_msg_len(last_msg) if last_msg else 0)
    
    # 允许中间消息占用的剩余配额
    quota = DEFAULT_MAX_CHARS - current_total
    
    temp_middle = []
    # 从后往前取，保留最新的历史
    # 注意：这里需要处理 tool 消息对。如果保留了 role="tool" 的消息，必须也保留它前面的带 tool_calls 的消息。
    
    i = len(remaining_messages) - 1
    while i >= 0:
        msg = remaining_messages[i]
        msg_len = _get_msg_len(msg)
        
        if quota <= 0:
            i -= 1
            continue
            
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "type", None)
        
        # 如果是 tool 消息，尝试将其与前面的 tool_calls 消息作为一个整体处理
        if (role == "tool" or role == "function") and i > 0:
            prev_msg = remaining_messages[i-1]
            prev_msg_len = _get_msg_len(prev_msg)
            pair_len = msg_len + prev_msg_len
            
            # 检查前一个消息是否包含对应的 tool_calls
            has_tool_calls = False
            if isinstance(prev_msg, dict):
                has_tool_calls = bool(prev_msg.get("tool_calls"))
            else:
                has_tool_calls = bool(getattr(prev_msg, "tool_calls", None))
                
            if has_tool_calls:
                # 作为一个对进行处理
                if pair_len <= quota:
                    temp_middle.append(msg)
                    temp_middle.append(prev_msg)
                    quota -= pair_len
                elif quota > prev_msg_len + 100: # 如果至少能放下前一个消息和一点截断的 tool 结果
                    # 截断 tool 结果但保留 pair
                    content = str(msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", ""))
                    truncated_quota = quota - prev_msg_len
                    truncated_content = content[:truncated_quota] + "\n... (内容过长已截断) ..."
                    
                    if isinstance(msg, dict):
                        new_msg = msg.copy()
                        new_msg["content"] = truncated_content
                    else:
                        new_msg = {"role": role, "content": truncated_content}
                        if hasattr(msg, "tool_call_id"): new_msg["tool_call_id"] = msg.tool_call_id
                    
                    temp_middle.append(new_msg)
                    temp_middle.append(prev_msg)
                    quota = 0
                i -= 2 # 跳过已处理的 pair
                continue

        # 普通消息处理
        if msg_len > quota:
            # 截断普通消息
            content = str(msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", ""))
            truncated_content = content[:quota] + "..."
            
            if isinstance(msg, dict):
                new_msg = msg.copy()
                new_msg["content"] = truncated_content
            else:
                new_msg = {"role": role or "assistant", "content": truncated_content}
            
            temp_middle.append(new_msg)
            quota = 0
        else:
            temp_middle.append(msg)
            quota -= msg_len
        i -= 1

    # 将中间消息恢复顺序并组合
    trimmed_messages.extend(reversed(temp_middle))
    
    # 最终检查：确保不会出现孤立的 tool 消息
    final_messages = []
    for j, m in enumerate(trimmed_messages):
        m_role = m.get("role") if isinstance(m, dict) else getattr(m, "type", None)
        if m_role == "tool" or m_role == "function":
            # 检查前一个消息是否是对应的 assistant 消息
            if j == 0: continue # 第一个不能是 tool 消息
            
            prev_m = final_messages[-1]
            has_tc = False
            if isinstance(prev_m, dict):
                has_tc = bool(prev_m.get("tool_calls"))
            else:
                has_tc = bool(getattr(prev_m, "tool_calls", None))
            
            if not has_tc:
                # 如果前一个消息没有 tool_calls，说明这个 tool 消息是孤立的，丢弃它
                logger.warning("Dropping isolated tool message at index {}", j)
                continue
        final_messages.append(m)

    if last_msg:
        # 同样检查 last_msg 是否合法
        last_role = last_msg.get("role") if isinstance(last_msg, dict) else getattr(last_msg, "type", None)
        if last_role == "tool" or last_role == "function":
            if final_messages:
                prev_m = final_messages[-1]
                has_tc = False
                if isinstance(prev_m, dict):
                    has_tc = bool(prev_m.get("tool_calls"))
                else:
                    has_tc = bool(getattr(prev_m, "tool_calls", None))
                if has_tc:
                    final_messages.append(last_msg)
                else:
                    logger.warning("Dropping isolated last tool message")
            else:
                logger.warning("Dropping isolated last tool message (no history)")
        else:
            final_messages.append(last_msg)

    new_total = sum(_get_msg_len(m) for m in final_messages)
    logger.info("Trimmed message history from {} to {} characters", total_len, new_total)

    new_request = request.override(messages=final_messages)
    return await handler(new_request)
