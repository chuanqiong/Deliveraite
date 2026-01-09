"""
交付物智能体场景参数配置

定义不同场景（大纲生成、章节撰写、内容润色等）的模型参数配置
"""

# 场景参数配置表
SCENARIO_MODEL_PARAMS = {
    "outline": {
        "temperature": 0.6,  # 降低温度以提高工具调用稳定性
        "top_p": 0.9,
        "max_tokens": 8192,  # 进一步提高上限
        "description": "大纲生成场景 - 需要深度思考规划文档结构",
        "enable_thinking": False,
        "enable_search": True
    },
    "writing": {
        "temperature": 0.75,  # 章节撰写：中高温度，平衡准确性和创造性
        "top_p": 0.9,
        "max_tokens": 8192,
        "description": "章节撰写场景 - 平衡准确性和表达灵活性",
        "enable_thinking": True,
        "enable_search": True
    },
    "polish": {
        "temperature": 0.65,  # 内容润色：中等温度，保持准确性
        "top_p": 0.85,
        "max_tokens": 8192,
        "description": "内容润色场景 - 适度优化表达，保持原意",
        "enable_thinking": True,
        "enable_search": False
    },
    "draft": {
        "temperature": 0.65,   # 生成初稿：标准配置
        "top_p": 0.9,
        "max_tokens": 8192,
        "description": "生成初稿场景 - 快速生成完整内容",
        "enable_thinking": True,
        "enable_search": True
    },
    "default": {
        "temperature": 0.7,
        "top_p": 0.9,
        "description": "默认配置",
        "enable_thinking": False,
        "enable_search": False
    }
}


def detect_scenario(context, query: str = None) -> str:
    """
    根据 context 信息自动检测当前场景

    Args:
        context: DeliverableContext 实例
        query: 用户当前的输入查询

    Returns:
        str: 场景标识 (outline/writing/polish/draft/default)
    """
    # 1. 优先检查是否明确指定了 scenario
    if hasattr(context, "scenario") and context.scenario:
        return context.scenario

    # 获取大纲数据（兼容两个字段）
    has_outline = False
    if hasattr(context, "existingOutline") and context.existingOutline:
        has_outline = True
    elif hasattr(context, "documentStructure") and context.documentStructure:
        has_outline = True

    # 2. 检查是否有明确的润色/初稿标识（通过系统提示词或 Query）
    system_prompt = getattr(context, "system_prompt", "") or ""
    
    # 整合文本用于检测
    text_to_check = (system_prompt + (query or "")).lower()
    
    if any(keyword in text_to_check for keyword in ["润色", "优化", "polish", "refine", "改进"]):
        return "polish"
    if any(keyword in text_to_check for keyword in ["生成初稿", "draft", "自动生成"]):
        return "draft"
    if any(keyword in text_to_check for keyword in ["标题", "修改标题", "重命名", "改成", "改为"]):
        return "writing"

    # 3. 优先根据 mode 判断基础场景，但不直接返回，而是作为 fallback
    mode = getattr(context, "mode", "global")
    status = getattr(context, "status", "未撰写")
    
    # 🆕 智能 fallback：如果已撰写或已有大纲，不再默认 fallback 到 outline
    if mode == "global":
        mode_fallback = "writing" if (has_outline or status == "已撰写") else "outline"
    else:
        mode_fallback = "writing"
    
    # 4. 全局模式下的逻辑
    if mode == "global":
        # 只有在未撰写且没有大纲的情况下，才默认进入大纲生成模式
        if not has_outline and status == "未撰写":
            return "outline"
        
        # 如果已经撰写完成，或者已有大纲：
        # 除非用户明确要求"重新生成大纲"或"重写大纲"，否则不进入 outline 模式
        re_generate_keywords = ["重新生成", "重写", "重新大纲", "重排"]
        is_re_generate = any(kw in text_to_check for kw in re_generate_keywords)
        
        if (status == "已撰写" or has_outline) and "大纲" in text_to_check:
            if is_re_generate:
                return "outline"
            else:
                # 已经有大纲了，用户只是提到大纲，可能是想修改或查看
                return "writing"
        
        if status == "已撰写":
            return "polish"
            
    # 5. 检查是否有选中的章节
    if hasattr(context, "activeSectionId") and context.activeSectionId:
        # 有选中章节 → 章节撰写 (除非前面已经匹配到 polish 或 draft)
        return "writing"

    # 6. 默认场景
    return mode_fallback


def get_scenario_params(scenario: str) -> dict:
    """
    获取指定场景的模型参数

    Args:
        scenario: 场景标识 (outline/writing/polish/draft/default)

    Returns:
        dict: 模型参数字典
    """
    return SCENARIO_MODEL_PARAMS.get(scenario, SCENARIO_MODEL_PARAMS["default"])
