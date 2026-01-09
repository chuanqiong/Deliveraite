"""
交付物智能体主定义
"""
from collections.abc import Callable

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, ModelResponse, dynamic_prompt, wrap_model_call

from src import config
from src.agents.common import BaseAgent, load_chat_model, BaseContext
from src.agents.common.middlewares import context_based_model, logging_middleware
from src.utils.logging_config import logger
from .context import DeliverableContext
from .prompts import DELIVERABLE_SYSTEM_PROMPT, OUTLINE_GENERATION_PROMPT, DRAFT_GENERATION_PROMPT, POLISH_PROMPT
from .tools import get_tools


@dynamic_prompt
def deliverable_prompt_middleware(request: ModelRequest) -> str:
    """动态注入交付物生成的系统提示词和上下文

    功能：
    1. 注入基础系统提示词
    2. 根据场景注入专用提示词（outline/draft/polish）
    3. 根据上下文注入项目信息、知识库内容
    4. 在局部模式下注入当前章节信息
    """
    context = request.runtime.context
    
    # 获取智能体名称，用于日志
    agent_name = getattr(request.runtime.context, "name", "DeliverableAgent")
    
    # 从消息历史中获取最新的用户输入作为检测参考
    query = ""
    if request.messages:
        for m in reversed(request.messages):
            role = m.get("role") if isinstance(m, dict) else getattr(m, "type", None)
            if role == "user":
                query = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                break

    # 🆕 智能场景检测：如果 context 中没有 scenario，则自动检测
    from .config import detect_scenario
    scenario = getattr(context, 'scenario', None)
    if not scenario:
        scenario = detect_scenario(context, query=query)
        # 记录检测到的场景
        logger.bind(
            type="business_node",
            node="scenario_detection",
            scenario=scenario,
            projectId=getattr(context, "projectId", ""),
            deliverableId=getattr(context, "deliverableId", "")
        ).info("[{}] >>> 自动检测到场景: {}", agent_name, scenario)
    
    logger.bind(
        type="business_node",
        node="request_start",
        scenario=scenario,
        projectId=getattr(context, "projectId", ""),
        deliverableId=getattr(context, "deliverableId", "")
    ).info("[{}] >>> 开始处理请求, Scenario: {}", agent_name, scenario)

    # 基础提示词
    full_prompt = DELIVERABLE_SYSTEM_PROMPT

    # 🆕 根据场景注入专用提示词
    if scenario:
        scenario = scenario.lower()

        if scenario == 'outline':
            full_prompt += f"\n\n### 大纲生成专项指导\n{OUTLINE_GENERATION_PROMPT}"

        elif scenario == 'draft':
            full_prompt += f"\n\n### 初稿生成专项指导\n{DRAFT_GENERATION_PROMPT}"

        elif scenario == 'polish':
            full_prompt += f"\n\n### 全文润色专项指导\n{POLISH_PROMPT}"

        # writing 场景不需要额外提示词，使用基础提示词即可
        pass

    # 注入项目上下文（如果有）
    if hasattr(context, 'projectContext') and context.projectContext:
        project_info = []
        if context.projectContext.get('industry'):
            project_info.append(f"行业领域：{context.projectContext['industry']}")
        if context.projectContext.get('tech_stack'):
            project_info.append(f"技术栈：{', '.join(context.projectContext['tech_stack'])}")
        if context.projectContext.get('business_domain'):
            project_info.append(f"业务领域：{context.projectContext['business_domain']}")

        if project_info:
            full_prompt += f"\n\n### 项目背景\n{'；'.join(project_info)}"

    # 注入知识库信息（如果有）
    if hasattr(context, 'kb_files') and context.kb_files:
        kb_count = len(context.kb_files)
        full_prompt += f"\n\n知识库包含 {kb_count} 个文件，请充分利用这些文件的内容生成专业大纲。"

    # 注入交付物类型（如果有）
    if hasattr(context, 'deliverableType') and context.deliverableType:
        full_prompt += f"\n\n交付物类型：{context.deliverableType}"

    # 注入目标字数和文档规模（如果有）
    if hasattr(context, 'targetWords') and context.targetWords:
        total_words = context.targetWords
        # 确定文档规模
        if total_words >= 100000:
            scale = "超大型文档（≥10万字，最大4级）"
        elif total_words >= 50000:
            scale = "大型文档（5-10万字，最大3-4级）"
        elif total_words >= 10000:
            scale = "中型文档（1-5万字，最大2-3级）"
        else:
            scale = "小型文档（<1万字，最大2级）"

        full_prompt += f"\n\n### 文档规模信息"
        full_prompt += f"\n- 目标总字数：{total_words} 字"
        full_prompt += f"\n- 文档规模：{scale}"
        full_prompt += f"\n\n请根据上述文档规模，严格按照'智能层级生成规则'中对应规模的展开规则执行。"

    # 注入动态上下文（如当前章节、字数限制等）
    active_section = None
    if hasattr(context, 'documentStructure') and context.documentStructure:
        active_section = next(
            (s for s in context.documentStructure
             if str(s.get('id')) == str(getattr(context, 'activeSectionId', ''))),
            None
        )

    if active_section:
        full_prompt += f"\n\n当前正在协作章节：{active_section.get('title')}"
        if active_section.get('targetWords'):
            full_prompt += f"\n本章节目标字数：{active_section.get('targetWords')}字"

    # 注入用户传入的额外系统提示（如风格锚定）
    if hasattr(context, 'system_prompt') and context.system_prompt:
        full_prompt += f"\n\n额外约束条件：\n{context.system_prompt}"

    return full_prompt


@wrap_model_call
async def deliverable_model_middleware(request: ModelRequest, handler: Callable) -> ModelResponse:
    """动态调整模型参数中间件

    根据当前场景（Scenario）动态调整模型参数（temperature, top_p, max_tokens 等）
    """
    context = request.runtime.context
    
    # 获取智能体名称，用于日志
    agent_name = getattr(request.runtime.context, "name", "DeliverableAgent")
    
    # 获取场景
    from .config import detect_scenario, get_scenario_params
    scenario = getattr(context, 'scenario', None)
    if not scenario:
        # 从消息历史中获取最新的用户输入作为检测参考
        query = ""
        if request.messages:
            for m in reversed(request.messages):
                role = m.get("role") if isinstance(m, dict) else getattr(m, "type", None)
                if role == "user":
                    query = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                    break
        scenario = detect_scenario(context, query=query)
        
    # 获取场景对应的参数
    scenario_params = get_scenario_params(scenario)
    
    # 动态调整模型参数
    # 优先级：context 中显式设置的参数 > 场景默认参数
    # 注意：BaseContext 的默认值（0.7/0.9）可能会覆盖场景参数，这里需要处理
    
    def get_param(name, default_val):
        # 如果 context 中有该字段，且不是 None
        val = getattr(context, name, None)
        # 如果 context 的值与 BaseContext 的硬编码默认值一致，则倾向于使用场景参数
        # 这里采用简化逻辑：如果 context 中的值与 BaseContext 默认值不同，说明是用户显式设置的，优先使用
        # 否则使用场景参数
        base_default = getattr(BaseContext(), name, None)
        
        if val is not None and val != base_default:
            return val
        return scenario_params.get(name, default_val)

    # 准备模型绑定参数
    max_tokens = get_param("max_tokens", 8192)

    # 限制 max_tokens 避免超出模型输出限制
    if max_tokens > 16384:
        logger.warning(
            f"[{agent_name}] max_tokens {max_tokens} 可能过大，调整为 16384 以提高流式输出稳定性"
        )
        max_tokens = 16384

    bind_params = {
        "temperature": get_param("temperature", 0.7),
        "top_p": get_param("top_p", 0.9),
        "max_tokens": max_tokens,
    }

    # 处理 extra_body (针对其他可能的参数)
    # 注意：某些参数在流式模式下可能导致问题，只保留安全的参数
    extra_body = {}

    if extra_body:
        bind_params["extra_body"] = extra_body

    logger.bind(
        type="business_node",
        node="model_param_adjustment",
        scenario=scenario,
        model_params=bind_params
    ).info(
        "[{}] >>> 应用场景参数 [{}]:\n"
        "  - temperature: {}\n"
        "  - top_p: {}\n"
        "  - max_tokens: {}\n"
        "  - extra_body: {}",
        agent_name, scenario,
        bind_params['temperature'],
        bind_params['top_p'],
        bind_params['max_tokens'],
        extra_body if extra_body else 'None'
    )

    # 获取当前模型并绑定参数
    current_model = request.model
    try:
        # 检查模型是否支持 bind 方法
        if hasattr(current_model, "bind"):
            logger.debug("[{}] 绑定模型参数: {}", agent_name, bind_params)
            new_model = current_model.bind(**bind_params)
            request = request.override(model=new_model)
        else:
            logger.warning("[{}] 模型不支持 bind 方法，跳过参数绑定", agent_name)
    except Exception as e:
        logger.bind(
            type="model_bind_error",
            agent=agent_name,
            params=bind_params
        ).exception("[{}] 模型参数绑定失败", agent_name)
        # 绑定失败时使用原模型继续

    return await handler(request)


class DeliverableAgent(BaseAgent):
    """交付物生成智能体

    专业的文档写作助手，具备大纲规划、章节扩写、风格统一与质量自检能力。
    """
    name = "交付物生成智能体"
    description = "专业的文档写作助手，具备大纲规划、章节扩写、风格统一与质量自检能力。"
    capabilities = ["planning", "rag", "reflection", "consistency", "file_upload"]
    context_schema = DeliverableContext

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def get_graph(self, input_context=None, query: str = None, **kwargs):
        # 注意：为了支持动态场景切换，这里不使用缓存
        # 每次 get_graph 调用都会根据当前的 input_context 重新创建 graph
        # 这样可以确保不同场景使用不同的参数配置

        # 从上下文获取模型参数配置（传入运行时 context 以便正确识别场景）
        context = self.context_schema.from_file(
            module_name=self.module_name,
            input_context=input_context
        )

        # 🆕 增强上下文：如果提供了 deliverableId，从数据库同步最新状态和大纲
        if hasattr(context, "deliverableId") and context.deliverableId:
            try:
                from src.storage.db.manager import db_manager
                from src.storage.db.models import ProjectDeliverable
                from sqlalchemy import select

                async with db_manager.AsyncSession() as session:
                    deliverable_id = int(context.deliverableId)
                    query_stmt = select(ProjectDeliverable).where(ProjectDeliverable.id == deliverable_id)
                    result = await session.execute(query_stmt)
                    deliverable = result.scalar_one_or_none()
                    
                    if deliverable:
                        # 同步状态
                        context.status = deliverable.status
                        
                        # 同步大纲 (如果 context 中没有大纲，或者需要强制同步)
                        if deliverable.extra_metadata and "outline" in deliverable.extra_metadata:
                            db_outline = deliverable.extra_metadata["outline"]
                            if not context.documentStructure and not context.existingOutline:
                                context.documentStructure = db_outline
                                context.existingOutline = db_outline
                                logger.debug(f"Synced {len(db_outline)} sections from DB for deliverable {deliverable_id}")
            except Exception as e:
                logger.warning(f"Failed to sync deliverable status from DB: {e}")

        # 自动检测场景并获取场景参数
        from .config import detect_scenario, get_scenario_params
        scenario = detect_scenario(context, query=query)
        scenario_params = get_scenario_params(scenario)

        # 准备模型参数：优先使用场景参数，如果 context 中明确指定了参数则覆盖
        model_params = {
            "temperature": getattr(context, "temperature", scenario_params["temperature"]),
            "top_p": getattr(context, "top_p", scenario_params["top_p"]),
            "max_tokens": getattr(context, "max_tokens", scenario_params.get("max_tokens", 4096)),
        }

        # 打印模型参数配置（便于调试）
        logger.bind(
            type="business_node",
            node="agent_init",
            scenario=scenario,
            model_params=model_params
        ).info(
            "DeliverableAgent initialized:\n"
            "  - Scenario: {} ({})\n"
            "  - temperature: {}\n"
            "  - top_p: {}\n"
            "  - max_tokens: {}",
            scenario, scenario_params['description'],
            model_params['temperature'],
            model_params['top_p'],
            model_params['max_tokens']
        )

        # 加载带参数的模型
        model = load_chat_model(config.default_model, **model_params)

        # 创建动态工具中间件实例，并传入所有可用的 MCP 服务器列表
        from src.agents.common.mcp import MCP_SERVERS
        from src.agents.common.middlewares import (
            DynamicToolMiddleware,
            inject_attachment_context,
            RobustPatchToolCallsMiddleware,
            token_trimming_middleware,
        )
        from langchain.agents.middleware import ModelRetryMiddleware

        dynamic_tool_middleware = DynamicToolMiddleware(
            base_tools=get_tools(), mcp_servers=list(MCP_SERVERS.keys())
        )
        
        # 预加载所有 MCP 工具并注册到 middleware.tools
        await dynamic_tool_middleware.initialize_mcp_tools()

        # 创建 DeliverableAgent
        graph = create_agent(
            model=model,
            tools=get_tools(),  # 使用 tools.py 中定义的工具
            middleware=[
                deliverable_prompt_middleware,  # 1. 提示词注入 (优先注入，确保修剪器能看到最新的 System Prompt)
                token_trimming_middleware,      # 2. 消息历史修剪 (置于注入之后，确保修剪的是旧历史，并保留最新的 System Prompt)
                inject_attachment_context,      # 3. 附件上下文注入
                context_based_model,            # 4. 模型选择
                deliverable_model_middleware,   # 5. 动态参数调整
                dynamic_tool_middleware,        # 6. 动态工具选择
                RobustPatchToolCallsMiddleware(),  # 7. 鲁棒修复工具调用 JSON
                ModelRetryMiddleware(),         # 8. 模型重试
                logging_middleware,             # 9. 日志记录
            ],
            checkpointer=await self._get_checkpointer(),
        )

        self.graph = graph
        return graph
