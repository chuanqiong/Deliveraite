"""
交付物文档操作工具

提供一组工具用于操作交付物文档结构，包括：
- 生成章节内容
- 添加子章节
- 删除章节
- 更新章节内容

这些工具由 DeliverableAgent 调用，实现对文档的精准控制。
"""
from langchain_core.tools import tool
from typing import Literal, Optional
from src.agents.common import load_chat_model
from src import config
from src.utils.logging_config import logger
import re
import time
import hashlib
from functools import wraps


# ==================== 内容生成缓存 ====================
_content_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 600  # 缓存有效期：10分钟


def _get_cache_key(*args, **kwargs) -> str:
    """生成缓存键"""
    # 将所有参数序列化为字符串
    cache_str = str(args) + str(sorted(kwargs.items()))
    return hashlib.md5(cache_str.encode()).hexdigest()


def _get_cached_result(cache_key: str) -> dict | None:
    """从缓存中获取结果"""
    if cache_key in _content_cache:
        result, timestamp = _content_cache[cache_key]
        if time.time() - timestamp < _CACHE_TTL:
            return result
        else:
            del _content_cache[cache_key]
    return None


def _set_cached_result(cache_key: str, result: dict) -> None:
    """将结果存入缓存"""
    _content_cache[cache_key] = (result, time.time())


@tool
async def batch_generate_sections(
    sections: list[dict],
    content_focus: str = "comprehensive"
) -> dict:
    """批量为多个章节生成文字内容（并行处理，速度更快）

    ⚠️ 适用场景：
    - 在生成大纲后，需要为多个章节同时填充初始内容时
    - 用户要求为多个特定章节生成内容时

    Args:
        sections: 包含章节信息的列表，每个元素格式为：
            {"id": "章节ID", "title": "章节标题", "parent_number": "父编号", "target_words": 目标字数}
        content_focus: 内容侧重点

    Returns:
        包含所有章节生成结果的字典列表
    """
    import asyncio
    
    logger.info(f"Tool called: batch_generate_sections for {len(sections)} sections")
    
    # 限制并发数量，避免触发 API 频率限制或资源耗尽
    # 根据经验，10-20 个并发通常是安全的
    semaphore = asyncio.Semaphore(3)
    
    async def sem_task(s):
        async with semaphore:
            # 增加重试逻辑
            max_retries = 5
            last_error = None
            
            for attempt in range(max_retries):
                try:
                    logger.info(f"Generating section {s.get('id')} ({s.get('title')}), attempt {attempt + 1}/{max_retries}")
                    # 显式传递参数，避免 context 丢失或 tool 绑定问题
                    result = await generate_section_content.ainvoke({
                        "section_id": s["id"],
                        "section_title": s["title"],
                        "parent_number": s.get("parent_number", ""),
                        "content_focus": content_focus,
                        "target_words": s.get("target_words", 500)
                    })
                    
                    if result.get("operation") == "error":
                        last_error = result.get("error")
                        logger.warning(f"Attempt {attempt + 1} failed for section {s.get('id')}: {last_error}")
                        # 如果是 API 限制相关的错误，可以等待一段时间
                        if "429" in str(last_error) or "limit" in str(last_error).lower():
                            await asyncio.sleep(2 * (attempt + 1))
                        continue
                    
                    return result
                except Exception as e:
                    last_error = str(e)
                    logger.error(f"Unexpected error in sem_task for section {s.get('id')}, attempt {attempt + 1}: {last_error}")
                    await asyncio.sleep(1 * (attempt + 1))
            
            # 所有重试都失败
            logger.error(f"All {max_retries} attempts failed for section {s.get('id')}. Last error: {last_error}")
            return {
                "operation": "error",
                "section_id": s.get("id"),
                "error": f"重试 {max_retries} 次后仍然失败: {last_error}",
                "word_count": 0
            }
    
    tasks = [sem_task(s) for s in sections]
    results = await asyncio.gather(*tasks)
    
    # 统计成功和失败的数量
    success_count = sum(1 for r in results if r.get("operation") != "error")
    error_count = len(results) - success_count
    
    logger.info(f"batch_generate_sections completed: {success_count} success, {error_count} failed")
    
    return {
        "operation": "batch_content_generated",
        "results": results,
        "count": len(results),
        "success_count": success_count,
        "error_count": error_count
    }


@tool
async def generate_section_content(
    section_id: str,
    section_title: str,
    parent_number: str = "",
    content_focus: str = "comprehensive",
    target_words: int = 500
) -> dict:
    """为指定章节生成文字内容（不修改文档结构）

    ⚠️ 适用场景（仅在以下情况使用）：
    - 用户明确要求"生成内容"、"撰写"、"扩写"、"写"某章节
    - 用户只是想为某章节写文字，不涉及结构变化
    - 用户选中了某个章节并要求生成其内容

    ❌ 不要使用此工具当用户要求：
    - "添加子章节"、"新增章节"、"加个小节" → 请用 add_subsection
    - "删除章节"、"移除" → 请用 delete_section
    - "修改大纲"、"调整结构" → 不要用此工具

    ✅ 正确示例：
    - "为项目背景生成内容" → 调用此工具
    - "写一下技术方案" → 调用此工具
    - "扩写第一章" → 调用此工具

    ❌ 错误示例：
    - "在项目背景下加个子章节" → 不要用此工具，用 add_subsection
    - "第三章删除了" → 不要用此工具，用 delete_section

    ⚠️ **目标字数说明（CRITICAL）**：
    - **必须**从 context.documentStructure 中获取章节的实际 targetWords
    - **字数确定优先级**：
      1. **用户明确指定字数**（如"生成500字内容"、"润色为2000字"）→ 尊重用户意图，使用用户指定的字数
      2. **用户没有指定字数** → **必须**使用 context 中的实际 targetWords
    - **绝对禁止**：AI 自己随意猜测或生成不合理的字数（如 270、100、50 等明显不合理的数字）
    - **正确示例**：
      - 用户说"生成500字内容" → target_words=500 ✅
      - 用户说"全文润色"（未提字数）→ 从 context 获取实际 targetWords（如 15000）✅
    - **错误示例**：
      - 用户说"全文润色"（未提字数）→ AI 自己决定使用 270 字 ❌
      - 用户说"润色内容" → AI 使用 100 字 ❌
    - AI应该在工具调用时明确说明字数来源

    Args:
        section_id: 章节 ID
        section_title: 章节标题
        parent_number: 父章节编号（如 "2." 或 "2.1."）
        content_focus: 内容侧重点（如：技术细节、市场分析、风险提示、comprehensive全面）
        target_words: 目标字数（⚠️ 建议从 context 获取，如果用户提供明显不合理的字数则忽略）

    Returns:
        包含生成结果的字典：
        {
            "operation": "content_generated",
            "section_id": section_id,
            "content": "生成的 Markdown 内容",
            "word_count": 实际字数,
            "subsection_added": False,
            "target_words_used": 实际使用的目标字数（从context获取）
        }
    """
    logger.info(
        f"Tool called: generate_section_content",
        extra={
            "type": "tool_call",
            "tool": "generate_section_content",
            "section_id": section_id,
            "section_title": section_title,
            "target_words": target_words
        }
    )
    # ✅ 检查缓存
    cache_key = _get_cache_key("generate", section_id, section_title, parent_number, content_focus, target_words)
    cached_result = _get_cached_result(cache_key)
    if cached_result is not None:
        return cached_result

    # ✅ 实现真实的 LLM 调用逻辑

    # 1. 构建生成 prompt
    prompt = f"""你是专业的商务文档撰写专家。

请为以下章节生成高质量的专业内容：

**章节标题**：{section_title}
**父章节编号**：{parent_number if parent_number else '无（顶级章节）'}
**目标字数**：{target_words} 字
**内容侧重点**：{content_focus}

**撰写要求**：
1. **内容结构**：
   - 使用 Markdown 子标题（## 或 ###）组织内容
   - 每个子章节应该包含 3-5 个段落
   - 段落之间逻辑清晰，过渡自然

2. **内容质量**：
   - 专业、严谨、符合商务文档风格
   - 使用准确的行业术语和专业表达
   - 避免空洞和重复，注重实质性内容
   - 适当使用数据和案例支撑观点

3. **字数控制**：
   - 严格控制总字数在 {target_words} 字左右（误差 ±10%）
   - 合理分配各部分的字数比例

4. **格式要求**：
   - 所有内容必须放在 <content> 标签内
   - 使用 Markdown 格式
   - 不要添加章节外的其他说明文字

**输出示例**：
<content>
## 2.1 行业现状分析

近年来，随着数字技术的快速发展...

### 市场规模
根据最新数据显示...

### 发展趋势
未来三年将呈现以下趋势...

## 2.2 存在的主要问题

### 2.2.1 流程效率问题
当前流程中存在...

### 2.2.2 技术架构瓶颈
技术层面面临的主要挑战是...
</content>
"""

    # 2. 加载 LLM - 使用 fast_model 提高生成速度
    llm = load_chat_model(
        config.fast_model,
        temperature=0.7,
        top_p=0.9
    )

    # 3. 调用 LLM 生成内容
    try:
        logger.info(f"Invoking LLM for section: {section_id} ({section_title}), target_words: {target_words}")
        response = await llm.ainvoke(prompt)

        # 提取生成的内容
        if hasattr(response, 'content'):
            generated_content = response.content
        else:
            generated_content = str(response)

        # 记录原始响应长度，方便排查截断问题
        logger.debug(f"LLM raw response length for {section_id}: {len(generated_content)}")

        # 清理内容（移除可能的多余标记）
        content_match = re.search(r'<content>([\s\S]*?)(?:</content>|$)', generated_content)
        if content_match:
            clean_content = content_match.group(1).strip()
        else:
            # 如果没有找到标签，记录警告
            logger.warning(f"No <content> tags found in LLM response for section {section_id}")
            clean_content = generated_content.strip()

        # 统计字数（中文字符+英文单词）
        word_count = len(clean_content)
        logger.info(f"Successfully generated content for {section_id}, word_count: {word_count}")

    except Exception as e:
        # 如果 LLM 调用失败，记录详细错误并返回
        logger.error(f"LLM invocation failed for section {section_id}: {str(e)}", exc_info=True)
        return {
            "operation": "error",
            "section_id": section_id,
            "error": f"LLM 调用失败: {str(e)}",
            "word_count": 0
        }

    # 4. 返回结果（并缓存）
    result = {
        "operation": "content_generated",
        "section_id": section_id,
        "content": clean_content,
        "word_count": word_count,
        "subsection_added": False,
        "target_words_used": target_words
    }
    _set_cached_result(cache_key, result)
    return result


@tool
async def add_subsection(
    parent_section_id: str,
    parent_section_title: str,
    parent_number: str,
    subsection_titles: list[str],
    target_words_per_section: int = 500
) -> dict:
    """在指定章节下添加子章节（修改文档结构）

    ⚠️ 适用场景（仅在以下情况使用）：
    - 用户明确要求"添加子章节"、"新增章节"、"加个小节"
    - 用户要求"展开"、"细分"、"详细说明"某章节
    - 用户要求为某章节创建更详细的子结构

    ❌ 不要使用此工具当用户要求：
    - "生成内容"、"撰写"、"扩写"某章节 → 请用 generate_section_content
    - "删除章节" → 请用 delete_section
    - "重写"、"修改内容" → 请用 update_section_content

    ✅ 正确示例：
    - "在项目背景下添加市场分析子章节" → 调用此工具
    - "为第二章添加两个子章节：现状分析和问题诊断" → 调用此工具
    - "展开细说技术方案" → 调用此工具

    ❌ 错误示例：
    - "为项目背景写点内容" → 不要用此工具，用 generate_section_content
    - "修改第三章的内容" → 不要用此工具，用 update_section_content

    Args:
        parent_section_id: 父章节 ID
        parent_section_title: 父章节标题
        parent_number: 父章节编号（如 "2." 或 "2.1."）
        subsection_titles: 子章节标题列表（如 ["市场分析", "竞争格局"]）
        target_words_per_section: 每个子章节的目标字数

    Returns:
        包含新创建的子章节信息的字典：
        {
            "operation": "subsection_added",
            "parent_section_id": parent_section_id,
            "subsections": [
                {"id": "2.1", "title": "市场分析", "target_words": 500},
                {"id": "2.2", "title": "竞争格局", "target_words": 500}
            ],
            "next_number": "2.3"  # 下一个可用的编号
        }
    """
    # TODO: 实现实际的编号生成逻辑
    # 需要继承父章节编号，并生成连续的子编号

    # 计算子章节编号
    subsections = []
    base_number = parent_number.rstrip('.') if parent_number else "1"
    start_index = 1

    for idx, title in enumerate(subsection_titles, start=start_index):
        # 如果父编号是 "2."，子编号是 "2.1", "2.2"
        # 如果父编号是 "2.1."，子编号是 "2.1.1", "2.1.2"
        if parent_number and parent_number.endswith('.'):
            sub_id = f"{parent_number}{idx}"
        else:
            sub_id = f"{base_number}.{idx}"

        subsections.append({
            "id": sub_id,
            "title": title,
            "target_words": target_words_per_section,
            "content": "",
            "status": "pending"
        })

    # 计算下一个可用编号
    next_number = f"{base_number}.{len(subsection_titles) + 1}"

    return {
        "operation": "subsection_added",
        "parent_section_id": parent_section_id,
        "parent_section_title": parent_section_title,
        "subsections": subsections,
        "next_number": next_number,
        "message": f"已为【{parent_section_title}】添加 {len(subsection_titles)} 个子章节"
    }


@tool
async def delete_section(
    section_id: str,
    section_title: str,
    section_number: str = "",
    cascade: bool = True
) -> dict:
    """删除指定章节（可选级联删除子章节）

    ⚠️ 适用场景（仅在以下情况使用）：
    - 用户明确要求"删除"、"移除"某章节
    - 用户要求去掉不需要的章节

    ⚠️ 重要提示：
    - 删除操作是不可逆的，请确认用户真实意图
    - 如果用户表达犹豫，建议先询问确认

    ❌ 不要使用此工具当用户要求：
    - "生成内容"、"撰写" → 请用 generate_section_content
    - "添加子章节" → 请用 add_subsection
    - "修改内容"、"重写" → 请用 update_section_content

    ✅ 正确示例：
    - "删除第三章" → 调用此工具
    - "移除多余的章节" → 调用此工具

    ❌ 错误示例：
    - "删除第三章的内容但保留结构" → 不要用此工具，应该用 update_section_content 清空内容

    Args:
        section_id: 要删除的章节 ID
        section_title: 章节标题
        section_number: 章节编号（如 "3." 或 "3.2."）
        cascade: 是否级联删除子章节（True: 删除该章节及其所有子章节; False: 仅删除该章节，子章节提升一级）

    Returns:
        包含删除结果的字典：
        {
            "operation": "section_deleted",
            "section_id": section_id,
            "section_title": section_title,
            "cascade": cascade,
            "deleted_count": 5  # 删除的章节数量（包含子章节）
        }
    """
    # TODO: 实现实际的删除逻辑
    # 需要更新数据库中的文档结构
    # 需要处理级联删除或子章节提升

    return {
        "operation": "section_deleted",
        "section_id": section_id,
        "section_title": section_title,
        "section_number": section_number,
        "cascade": cascade,
        "deleted_count": 1,  # TODO: 实际计算
        "message": f"已删除章节【{section_title}】"
    }


@tool
async def update_section_content(
    section_id: str,
    section_title: str,
    existing_content: str = "",
    mode: Literal["replace", "append", "prepend"] = "replace",
    operation_type: Literal["rewrite", "polish", "expand", "update"] = "update",
    target_words: int = 500,
    polish_focus: Literal["clarity", "professional", "consistency", "comprehensive"] = "comprehensive"
) -> dict:
    """更新/重写/润色/补充章节内容

    ⚠️ 适用场景（仅在以下情况使用）：
    - 用户要求"重写"、"修改"、"更新"某章节
    - 用户要求"优化"、"润色"、"改进"某章节的内容
    - 用户要求"补充"、"追加"内容到某章节

    ❌ 不要使用此工具当用户要求：
    - "生成内容"、"撰写"（首次生成） → 请用 generate_section_content
    - "添加子章节" → 请用 add_subsection
    - "删除章节" → 请用 delete_section

    ✅ 正确示例：
    - "重写技术方案这一章" → 调用此工具，mode="replace"
    - "在项目背景后补充一些内容" → 调用此工具，mode="append"
    - "优化第三章的表达" → 调用此工具，mode="replace", operation_type="polish"

    ❌ 错误示例：
    - "为项目背景生成内容"（首次生成） → 不要用此工具，用 generate_section_content
    - "添加子章节：市场分析" → 不要用此工具，用 add_subsection

    ⚠️ **字数控制（CRITICAL）**：
    - 当生成用于更新/润色的内容时，**必须**遵循字数控制规则
    - **字数确定优先级**：
      1. **用户明确指定字数**（如"润色为2000字"、"重写为5000字"）→ 尊重用户意图，使用用户指定的字数
      2. **用户没有指定字数** → **必须**使用 `context.documentStructure` 中的实际 `targetWords`

    Args:
        section_id: 章节 ID
        section_title: 章节标题
        existing_content: 现有章节内容（用于润色/重写）
        mode: 更新模式
            - "replace": 替换原有内容（重写）
            - "append": 追加到原有内容后面（补充）
            - "prepend": 插入到原有内容前面
        operation_type: 操作类型
            - "rewrite": 重写（完全重新生成内容）
            - "polish": 润色（优化现有内容表达）
            - "expand": 扩写（补充更多细节）
            - "update": 更新（修改特定部分）
        target_words: 目标字数（⚠️ 必须从context获取实际值）
        polish_focus: 润色重点（当operation_type="polish"时使用）
            - "clarity": 提升表达清晰度
            - "professional": 增强专业性
            - "consistency": 统一风格
            - "comprehensive": 全面优化

    Returns:
        包含更新结果的字典：
        {
            "operation": "content_updated",
            "section_id": section_id,
            "section_title": section_title,
            "mode": mode,
            "content": 更新后的内容,
            "word_count": 字数统计,
            "target_words_used": 使用的目标字数
        }
    """
    # ✅ 检查缓存（润色和重写操作可以缓存）
    if operation_type in ["polish", "rewrite", "expand"]:
        # 使用现有内容的哈希作为缓存键的一部分
        content_hash = hashlib.md5(existing_content.encode()).hexdigest()[:16]
        cache_key = _get_cache_key("update", operation_type, section_id, section_title, content_hash, target_words, polish_focus)
        cached_result = _get_cached_result(cache_key)
        if cached_result is not None:
            return cached_result
    else:
        cache_key = None

    # ✅ 实现真实的 LLM 调用逻辑

    # 1. 根据操作类型构建不同的 prompt
    if operation_type == "polish":
        prompt = f"""你是专业的商务文档润色专家。

**任务**：润色以下章节内容，保持原意不变，提升表达质量

**章节标题**：{section_title}
**润色重点**：{polish_focus or '全面优化'}
**目标字数**：{target_words} 字（与原文保持相近）

**原文内容**：
{existing_content}

**润色要求**：
1. **保持原意**：核心观点、关键数据、重要案例完整保留
2. **提升专业性**：
   - 将口语化表达改为专业术语
   - 统一术语使用，保持全文一致
   - 使用准确的行业表达
3. **优化句式**：
   - 调整语序，使表达更流畅
   - 适当使用排比、对偶等修辞
   - 长句拆分为短句，提升可读性
4. **字数控制**：润色后字数应与原文相近（误差 ±5%）
5. **格式要求**：所有内容放在 <content> 标签内

**输出格式**：
<content>
润色后的完整内容...
</content>
"""
    elif operation_type == "rewrite":
        prompt = f"""你是专业的商务文档撰写专家。

**任务**：为以下章节重新撰写内容

**章节标题**：{section_title}
**目标字数**：{target_words} 字

**重写要求**：
1. **保持专业性**：商务、严谨、专业的语体
2. **结构清晰**：使用 Markdown 子标题组织内容
3. **内容充实**：避免空洞，注重实质性内容
4. **字数达标**：严格控制字数在 {target_words} 字左右（误差 ±10%）
5. **格式要求**：所有内容放在 <content> 标签内

**输出格式**：
<content>
## 2.1 重写后的子章节1
（内容...）

## 2.2 重写后的子章节2
（内容...）
</content>
"""
    elif operation_type == "expand":
        prompt = f"""你是专业的商务文档撰写专家。

**任务**：为以下章节扩写内容，补充更多细节

**章节标题**：{section_title}
**目标字数**：{target_words} 字

**原文内容**：
{existing_content}

**扩写要求**：
1. **保持原有结构**：在原文基础上扩充，不改变原有框架
2. **补充细节**：
   - 添加具体的数据支撑
   - 补充案例和实例
   - 深化分析维度
3. **增加逻辑层次**：
   - 添加过渡和衔接
   - 完善论证逻辑
   - 补充必要的背景说明
4. **字数达标**：扩写后字数达到 {target_words} 字左右
5. **格式要求**：所有内容放在 <content> 标签内

**输出格式**：
<content>
扩写后的完整内容...
</content>
"""
    else:  # operation_type == "update"
        prompt = f"""你是专业的商务文档撰写专家。

**任务**：更新以下章节的特定部分

**章节标题**：{section_title}
**目标字数**：{target_words} 字

**原文内容**：
{existing_content}

**更新要求**：
1. 保持专业性和逻辑性
2. 根据用户意图调整内容
3. 字数控制在 {target_words} 字左右
4. 所有内容放在 <content> 标签内

**输出格式**：
<content>
更新后的完整内容...
</content>
"""

    # 2. 加载 LLM - 使用 fast_model 提高润色速度
    llm = load_chat_model(
        config.fast_model,
        temperature=0.65,  # 润色时温度稍低，保持稳定性
        top_p=0.85
    )

    # 3. 调用 LLM 生成内容
    try:
        response = await llm.ainvoke(prompt)

        # 提取生成的内容
        if hasattr(response, 'content'):
            generated_content = response.content
        else:
            generated_content = str(response)

        # 清理内容（移除可能的多余标记）
        content_match = re.search(r'<content>([\s\S]*?)(?:</content>|$)', generated_content)
        if content_match:
            clean_content = content_match.group(1).strip()
        else:
            clean_content = generated_content.strip()

        # 统计字数
        word_count = len(clean_content)

    except Exception as e:
        # 如果 LLM 调用失败，返回错误信息
        return {
            "operation": "error",
            "section_id": section_id,
            "error": f"LLM 调用失败: {str(e)}",
            "word_count": 0
        }

    # 4. 返回结果（并缓存）
    result = {
        "operation": "content_updated",
        "section_id": section_id,
        "section_title": section_title,  # 这里的 section_title 可能是 LLM 修改后的新标题
        "mode": mode,
        "content": clean_content,
        "word_count": word_count,
        "target_words_used": target_words,
        "operation_type": operation_type
    }

    # 如果有缓存键，保存结果到缓存
    if cache_key:
        _set_cached_result(cache_key, result)

    return result


def get_deliverable_tools() -> list:
    """获取所有交付物文档操作工具"""
    return [
        batch_generate_sections,
        generate_section_content,
        add_subsection,
        delete_section,
        update_section_content,
    ]

