"""AI 交互日志中间件"""

from collections.abc import Callable
from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call
from src.utils import logger


@wrap_model_call
async def logging_middleware(request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
    """打印 AI 请求和响应的日志（结构化增强版）"""
    # 尝试从 context 或 config 中获取智能体信息
    agent_name = "Unknown Agent"
    scenario = None
    
    # 1. 优先从 context 获取 scenario
    if hasattr(request, "runtime") and hasattr(request.runtime, "context"):
        context = request.runtime.context
        agent_name = getattr(context, "name", agent_name)
        scenario = getattr(context, "scenario", None)

    # 2. 从 configurable 获取
    if hasattr(request, "config"):
        configurable = request.config.get("configurable", {})
        if isinstance(configurable, dict):
            if agent_name == "Unknown Agent":
                agent_name = configurable.get("agent_name", agent_name)
            
            if scenario is None:
                scenario = configurable.get("scenario")
            
            # 兼容 langchain-graph 的命名习惯
            if agent_name == "Unknown Agent":
                agent_name = configurable.get("task_id", agent_name)

    # 3. 如果识别到了 scenario，优先用 scenario 作为 agent_name 的一部分或替代
    if scenario:
        if agent_name == "Unknown Agent" or agent_name == "DeliverableAgent":
            agent_name = scenario
        else:
            # 如果已有名字，且名字里没有 scenario，可以考虑合并，但为了简洁，如果 agent_name 是通用的就替换
            if agent_name in ["交付物生成智能体", "DeliverableAgent"]:
                agent_name = scenario

    # 4. 如果还是 Unknown，尝试从环境变量或 trace_id 中提取一点线索
    if agent_name == "Unknown Agent":
        from src.utils.context_vars import trace_id_var
        tid = trace_id_var.get()
        if tid and "-" in tid:
            # 假设 trace_id 格式类似 "draft-1767269232780-2hi4wa6"
            agent_name = tid.split("-")[0]

    # 记录请求
    try:
        # 安全地获取 messages，并确保它是列表（防止迭代器被耗尽）
        messages = request.messages
        if not hasattr(messages, "__len__"):
            messages = list(messages)
        
        # 🆕 动态调整 agent_name 的显示，解决 trace_id 导致的 draft- 前缀误导问题
        display_name = agent_name
        from src.utils.context_vars import trace_id_var
        trace_id = trace_id_var.get()
        if trace_id:
            # 如果 trace_id 以 draft- 开头但场景是 polish，我们优先显示 scenario
            if "draft-" in trace_id and scenario == "polish":
                display_name = f"polish(trace:{trace_id})"
            elif scenario and scenario not in display_name:
                display_name = f"{scenario}({trace_id})"
            else:
                display_name = f"{agent_name}({trace_id})"
        
        # === 修复消息完整性：确保所有 tool_calls 都有对应的 ToolMessage ===
        # 这是为了防止 "An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'" 错误
        fixed_messages = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            fixed_messages.append(msg)
            
            # 检查是否是包含 tool_calls 的 AI 消息
            tool_calls = None
            if hasattr(msg, "tool_calls"):
                tool_calls = msg.tool_calls
            elif isinstance(msg, dict) and "tool_calls" in msg:
                tool_calls = msg["tool_calls"]
            
            if tool_calls:
                # 收集这组 tool_calls 需要的所有 ID
                required_ids = []
                for tc in tool_calls:
                    if isinstance(tc, dict) and "id" in tc:
                        required_ids.append(tc["id"])
                    elif hasattr(tc, "id"):
                        required_ids.append(tc.id)
                
                # 在后续消息中查找这些 ID 的响应
                found_ids = set()
                j = i + 1
                while j < len(messages):
                    next_msg = messages[j]
                    
                    # 获取 next_msg 的 tool_call_id
                    tc_id = None
                    if hasattr(next_msg, "tool_call_id"):
                        tc_id = next_msg.tool_call_id
                    elif isinstance(next_msg, dict) and next_msg.get("role") == "tool":
                        tc_id = next_msg.get("tool_call_id")
                    
                    if tc_id in required_ids:
                        found_ids.add(tc_id)
                        j += 1
                    elif tc_id is not None:
                        # 发现了一个不属于这组的 tool 消息
                        j += 1
                    else:
                        # 遇到了非 tool 消息，说明这组 tool 响应结束了
                        break
                
                # 检查是否有缺失的响应
                missing_ids = [rid for rid in required_ids if rid not in found_ids]
                if missing_ids:
                    logger.warning(
                        "[{}] 检测到缺失的工具响应 ID: {}，正在自动注入占位响应以修复 400 错误",
                        agent_name, missing_ids
                    )
                    from langchain_core.messages import ToolMessage
                    for mid in missing_ids:
                        placeholder = ToolMessage(
                            tool_call_id=mid,
                            content="工具调用已执行但响应内容丢失，自动修复以维持对话。"
                        )
                        fixed_messages.append(placeholder)
            
            i += 1
        
        # 如果消息列表发生了变化，更新 messages 和 request
        if len(fixed_messages) != len(messages):
            logger.info("[{}] 消息列表已修复，原长度: {}, 新长度: {}", agent_name, len(messages), len(fixed_messages))
            messages = fixed_messages
            if hasattr(request, "override"):
                request = request.override(messages=messages)
        elif not hasattr(request.messages, "__len__"):
            # 如果原始是迭代器但长度没变，也要更新回列表，防止二次迭代失效
            if hasattr(request, "override"):
                request = request.override(messages=messages)
        # === 修复结束 ===

        msg_count = len(messages)

        # 格式化消息内容以便打印（优化：当消息过多时，只打印首尾）
        formatted_messages = []
        MAX_DISPLAY_MESSAGES = 10 # 最多显示 10 条消息
        
        for i, msg in enumerate(messages):
            # 如果消息太多，中间部分用省略号代替
            if msg_count > MAX_DISPLAY_MESSAGES:
                if 2 < i < msg_count - 3:
                    if i == 3:
                        formatted_messages.append("  ... (中间历史消息已省略) ...")
                    continue

            try:
                # 更健壮的消息类型检测
                if isinstance(msg, dict):
                    # 字典格式消息
                    role = msg.get("role") or msg.get("type") or "unknown"
                    content = msg.get("content", "")
                elif hasattr(msg, 'type'):
                    # LangChain 消息对象
                    role = getattr(msg, 'type', 'unknown')
                    content = getattr(msg, 'content', '')
                else:
                    role = "unknown"
                    content = str(msg)

                if role == "human": role = "user"
                elif role == "ai": role = "assistant"

                content_str = str(content)[:500]
                formatted_messages.append(f"  [{role}]: {content_str}")
            except Exception as inner_e:
                formatted_messages.append(f"  [error]: Failed to parse message {i}: {inner_e}")

        full_messages_str = "\n".join(formatted_messages)

        # 记录关键信息
        logger.bind(
            type="ai_request",
            agent=agent_name,
            msg_count=msg_count,
        ).info("[{}] >>> 请求 AI ({} 条消息)\n{}", display_name, msg_count, full_messages_str)
    except Exception as e:
        logger.exception("Logging middleware input error")

    # 调用模型并记录响应
    try:
        response = await handler(request)
        
        content = ""
        if hasattr(response, "content"):
            content = response.content
        elif hasattr(response, "message") and hasattr(response.message, "content"):
            content = response.message.content
        elif hasattr(response, "response") and hasattr(response.response, "content"):
            content = response.response.content
        else:
            content = str(response)

        content_str = str(content)
        content_len = len(content_str)
        
        logger.bind(
            type="ai_response",
            agent=agent_name,
            content_length=content_len,
        ).info("[{}] <<< AI 响应 ({} 字符):\n{}", display_name, content_len, content_str)
        
        return response
    except Exception as e:
        logger.error("Logging middleware output error: {}", e)
        raise
