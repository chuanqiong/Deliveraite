import asyncio
import json
import re
import traceback
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from langchain.messages import AIMessageChunk, HumanMessage
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.storage.db.models import User, MessageFeedback, Message, Conversation, ProjectDeliverable, ProjectDeliverableContent, Project
from src.storage.conversation import ConversationManager
from src.storage.db.manager import db_manager
from server.routers.auth_router import get_admin_user
from server.utils.auth_middleware import get_db, get_required_user
from src import executor
from src import config as conf
from src.agents import agent_manager
from src.agents.common.tools import gen_tool_info, get_buildin_tools
from src.models import select_model
from src.plugins.guard import content_guard
from src.services.doc_converter import (
    ATTACHMENT_ALLOWED_EXTENSIONS,
    MAX_ATTACHMENT_SIZE_BYTES,
    convert_upload_to_markdown,
)
from src.utils.datetime_utils import utc_now, utc_isoformat
from src.utils.logging_config import logger
from src.utils.image_processor import process_uploaded_image


# 图片上传响应模型
class ImageUploadResponse(BaseModel):
    success: bool
    image_content: str | None = None
    thumbnail_content: str | None = None
    width: int | None = None
    height: int | None = None
    format: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    error: str | None = None


chat = APIRouter(prefix="/chat", tags=["chat"])

# =============================================================================
# > === 智能体管理分组 ===
# =============================================================================


@chat.get("/default_agent")
async def get_default_agent(current_user: User = Depends(get_required_user)):
    """获取默认智能体ID（需要登录）"""
    try:
        default_agent_id = conf.default_agent_id
        # 如果没有设置默认智能体，尝试获取第一个可用的智能体
        if not default_agent_id:
            agents = await agent_manager.get_agents_info()
            if agents:
                default_agent_id = agents[0].get("id", "")

        return {"default_agent_id": default_agent_id}
    except Exception as e:
        logger.error(f"获取默认智能体出错: {e}")
        raise HTTPException(status_code=500, detail=f"获取默认智能体出错: {str(e)}")


@chat.post("/set_default_agent")
async def set_default_agent(request_data: dict = Body(...), current_user=Depends(get_admin_user)):
    """设置默认智能体ID (仅管理员)"""
    try:
        agent_id = request_data.get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=422, detail="缺少必需的 agent_id 字段")

        # 验证智能体是否存在
        agents = await agent_manager.get_agents_info()
        agent_ids = [agent.get("id", "") for agent in agents]

        if agent_id not in agent_ids:
            raise HTTPException(status_code=404, detail=f"智能体 {agent_id} 不存在")

        # 设置默认智能体ID
        conf.default_agent_id = agent_id
        # 保存配置
        conf.save()

        return {"success": True, "default_agent_id": agent_id}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"设置默认智能体出错: {e}")
        raise HTTPException(status_code=500, detail=f"设置默认智能体出错: {str(e)}")


# =============================================================================
# > === 对话分组 ===
# =============================================================================


async def _get_langgraph_messages(agent_instance, config_dict):
    graph = await agent_instance.get_graph()
    state = await graph.aget_state(config_dict)

    if not state or not state.values:
        logger.warning("No state found in LangGraph")
        return None

    return state.values.get("messages", [])


def _extract_agent_state(values: dict) -> dict:
    if not isinstance(values, dict):
        return {}

    def _norm_list(v):
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return list(v)
        return [v]

    result = {}
    result["todos"] = _norm_list(values.get("todos"))[:20]
    result["files"] = _norm_list(values.get("files"))[:50]
    
    # 交付物智能体特有状态
    if "documentStructure" in values:
        result["documentStructure"] = values["documentStructure"]
    elif "document_structure" in values:
        result["documentStructure"] = values["document_structure"]

    return result


async def _get_existing_message_ids(conv_mgr, thread_id):
    """获取已保存的消息ID集合"""
    existing_messages = await conv_mgr.get_messages_by_thread_id(thread_id)
    return {msg.extra_metadata["id"] for msg in existing_messages if msg.extra_metadata and "id" in msg.extra_metadata}


async def _save_ai_message(conv_mgr, thread_id, msg_dict, config_dict=None):
    """保存AI message和相关的工具调用"""
    content = msg_dict.get("content", "")
    tool_calls_data = msg_dict.get("tool_calls", [])

    # 保存AI消息
    ai_msg = await conv_mgr.add_message_by_thread_id(
        thread_id=thread_id,
        role="assistant",
        content=content,
        message_type="text",
        extra_metadata=msg_dict,
    )

    if not ai_msg:
        logger.error(f"Failed to save AI message for thread_id {thread_id}: conversation not found")
        return

    # === 额外的持久化操作：处理 AI 回复中可能包含的大纲内容或章节正文 ===
    if config_dict:
        await _process_ai_extra_content(conv_mgr, thread_id, ai_msg, content, config_dict)

    # 保存工具调用
    if tool_calls_data:
        logger.debug(f"Saving {len(tool_calls_data)} tool calls from AI message")
        for tc in tool_calls_data:
            await conv_mgr.add_tool_call(
                message_id=ai_msg.id,
                tool_name=tc.get("name", "unknown"),
                tool_input=tc.get("args", {}),
                status="pending",
                langgraph_tool_call_id=tc.get("id"),
            )

    logger.debug(f"Saved AI message {ai_msg.id} with {len(tool_calls_data)} tool calls")


async def _save_tool_message(conv_mgr, thread_id, msg_dict, config_dict=None):
    """保存工具执行结果，并根据工具类型执行额外的持久化操作"""
    tool_call_id = msg_dict.get("tool_call_id")
    content = msg_dict.get("content", "")
    name = msg_dict.get("name", "")

    if not tool_call_id:
        return

    # 确保tool_output是字符串类型
    if isinstance(content, list):
        tool_output = json.dumps(content) if content else ""
    else:
        tool_output = str(content)

    # 保存工具响应为 Message 记录
    await conv_mgr.add_message_by_thread_id(
        thread_id=thread_id,
        role="tool",
        content=tool_output,
        message_type="tool_result",
        extra_metadata=msg_dict,
    )

    # 更新工具调用结果
    updated_tc = await conv_mgr.update_tool_call_output(
        langgraph_tool_call_id=tool_call_id,
        tool_output=tool_output,
        status="success",
        thread_id=thread_id,
    )

    if updated_tc:
        logger.debug(f"Updated tool_call {tool_call_id} ({name}) with output")
    else:
        logger.warning(f"Tool call {tool_call_id} not found for update")

    # === 额外的持久化操作：交付物生成相关 ===
    allowed_tools = [
        "generate_section_content", 
        "update_section_content", 
        "batch_generate_sections",
        "ai_section_generation",
        "ai_outline_generation"
    ]
    if config_dict and name in allowed_tools:
        logger.info(f"[Deliverable Persistence] Starting extra persistence for tool: {name}, thread_id: {thread_id}")
        try:
            configurable = config_dict.get("configurable", {})
            deliverable_id = configurable.get("deliverableId")
            
            if not deliverable_id:
                logger.warning(f"[Deliverable Persistence] No deliverableId found in config for tool {name}. Config: {config_dict}")
                return
            
            # 尝试转换为整数
            try:
                deliverable_id_int = int(deliverable_id)
            except (ValueError, TypeError):
                logger.error(f"[Deliverable Persistence] Invalid deliverableId type/value: {deliverable_id}")
                return

            # 解析工具输出
            try:
                if isinstance(content, str):
                    try:
                        output_data = json.loads(content)
                    except json.JSONDecodeError:
                        # 如果是 ai_section_generation 且内容不是 JSON，则视为纯文本
                        if name == "ai_section_generation":
                            output_data = {"content": content}
                        else:
                            raise
                else:
                    output_data = content
                
                logger.debug(f"[Deliverable Persistence] Parsed tool output for {name}: operation={output_data.get('operation') if isinstance(output_data, dict) else 'N/A'}")
            except Exception as e:
                logger.error(f"[Deliverable Persistence] Failed to parse tool output for {name}: {e}")
                return

            # 获取生成的文字内容
            new_content = ""
            # 调试日志：记录 output_data 类型
            logger.debug(f"[Deliverable Persistence] Processing tool {name} output_data type: {type(output_data)}")
            
            if name == "batch_generate_sections":
                # 批量生成，合并所有成功章节的内容
                results = output_data.get("results", [])
                section_contents = []
                for res in results:
                    if res.get("operation") in ["content_generated", "content_updated"] and res.get("content"):
                        section_contents.append(res.get("content"))
                new_content = "\n\n".join(section_contents)
                logger.info(f"[Deliverable Persistence] Batch generated {len(section_contents)} sections, total length: {len(new_content)}")
            elif name == "ai_outline_generation":
                # 大纲生成时提取的内容
                new_content = output_data.get("content", "")
                logger.info(f"[Deliverable Persistence] Outline generated, length: {len(new_content)}")
            elif name == "ai_section_generation":
                # 针对 ai_section_generation 的特殊处理
                new_content = output_data.get("content", "")
                # 如果 output_data 中没有 content，尝试从 text 中获取
                if not new_content and output_data.get("text"):
                    new_content = output_data.get("text")
                
                if not new_content:
                    logger.warning(f"[Deliverable Persistence] No content found in output_data for ai_section_generation: {output_data}")
                else:
                    logger.info(f"[Deliverable Persistence] AI section generated, length: {len(new_content)}")
            else:
                # 单个章节生成
                if output_data.get("operation") in ["content_generated", "content_updated"]:
                    new_content = output_data.get("content", "")
                    logger.info(f"[Deliverable Persistence] Single section generated, length: {len(new_content)}")
                
            if not new_content:
                logger.info(f"[Deliverable Persistence] No new content generated by tool {name} or operation mismatch: {output_data.get('operation') if isinstance(output_data, dict) else 'N/A'}")
                return

            logger.info(f"[Deliverable Persistence] Updating database for deliverable {deliverable_id_int}, content length: {len(new_content)}")
            logger.info(f"[Deliverable Persistence] FULL CONTENT TO BE SAVED:\n{new_content}\n[End of Full Content]")

            # 更新数据库
            db = conv_mgr.db
            # 查询交付物
            query = select(ProjectDeliverable).where(ProjectDeliverable.id == deliverable_id_int)
            from sqlalchemy.orm import selectinload
            query = query.options(selectinload(ProjectDeliverable.content_detail))
            result = await db.execute(query)
            deliverable = result.scalar_one_or_none()

            if deliverable:
                logger.debug(f"[Deliverable Persistence] Found deliverable {deliverable_id_int}, current status: {deliverable.status}")
                # 更新内容
                if not deliverable.content_detail:
                    logger.info(f"[Deliverable Persistence] Creating NEW content detail for deliverable {deliverable_id_int}")
                    deliverable.content_detail = ProjectDeliverableContent(
                        deliverable_id=deliverable.id,
                        content=new_content
                    )
                    db.add(deliverable.content_detail)
                else:
                    logger.info(f"[Deliverable Persistence] Updating EXISTING content detail for deliverable {deliverable_id_int}")
                    # 如果是 update_section_content 且 mode 是 append/prepend，可能需要处理合并
                    current_content = deliverable.content_detail.content or ""
                    
                    mode = output_data.get("mode", "replace")
                    logger.debug(f"[Deliverable Persistence] Update mode: {mode}, current content length: {len(current_content)}")
                    
                    if not new_content or new_content.strip() == "":
                        logger.warning(f"[Deliverable Persistence] New content is empty, skipping update to avoid clearing database")
                    else:
                        # 严重 BUG 修复：防止 AI 生成过程中或前端旧数据覆盖新内容
                        # 仅在 replace 模式下执行长度保护
                        if mode == "replace" and current_content:
                            current_len = len(current_content)
                            new_len = len(new_content)
                            
                            # 保护策略 1：长度保护
                            # 如果当前数据库内容已经很长（>500字），且新内容长度缩水超过 30%，则可能是异常覆盖
                            if current_len > 500 and new_len < current_len * 0.7:
                                logger.warning(f"[Deliverable Persistence] Potential content loss detected! Current len: {current_len}, New len: {new_len}. Refusing to overwrite with shorter content.")
                                return True # 视为处理成功但不执行覆盖

                        if mode == "append":
                            deliverable.content_detail.content = current_content + "\n\n" + new_content
                        elif mode == "prepend":
                            deliverable.content_detail.content = new_content + "\n\n" + current_content
                        else:
                            # 默认 replace
                            deliverable.content_detail.content = new_content
                        
                        deliverable.content_detail.updated_at = utc_now()
                
                # 如果工具返回了新的标题，更新章节标题
                new_title = output_data.get("section_title")
                if new_title:
                    # 去掉 Markdown 标题前缀（如 "## " 或 "### "）
                    clean_title = re.sub(r'^#+\s*', '', new_title).strip()
                    
                    if name == "ai_outline_generation" and clean_title and deliverable.name != clean_title:
                        logger.info(f"[Deliverable Persistence] Updating deliverable name from '{deliverable.name}' to '{clean_title}'")
                        deliverable.name = clean_title
                    else:
                        logger.debug(f"[Deliverable Persistence] Skipping deliverable name update for tool {name} with title '{clean_title}'")
                
                # 更新交付物状态
                if deliverable.status == "未撰写":
                    logger.info(f"[Deliverable Persistence] Updating status from '未撰写' to '已撰写' for deliverable {deliverable_id_int}")
                    deliverable.status = "已撰写"
                deliverable.updated_at = utc_now()
                
                # 更新关联项目的更新时间
                if deliverable.project_id:
                    logger.debug(f"[Deliverable Persistence] Updating project {deliverable.project_id} update time")
                    from sqlalchemy import update
                    await db.execute(
                        update(Project)
                        .where(Project.id == deliverable.project_id)
                        .values(updated_at=utc_now())
                    )
                
                logger.info(f"[Deliverable Persistence] Committing changes to database for deliverable {deliverable_id_int}")
                await db.commit()
                logger.info(f"[Deliverable Persistence] Successfully saved deliverable content for ID {deliverable_id_int} from tool {name}")
            else:
                logger.warning(f"[Deliverable Persistence] Deliverable {deliverable_id_int} NOT FOUND in database during persistence attempt")

        except Exception as e:
            logger.error(f"Error persisting deliverable content: {e}")
            logger.error(traceback.format_exc())
            if 'db' in locals():
                await db.rollback()
    else:
        # 如果不满足额外持久化条件（如没有 config_dict），也需要结束 try 块
        pass


async def _require_user_conversation(conv_mgr: ConversationManager, thread_id: str, user_id: str) -> Conversation:
    conversation = await conv_mgr.get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.user_id != str(user_id) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")
    return conversation


def _serialize_attachment(record: dict) -> dict:
    return {
        "file_id": record.get("file_id"),
        "file_name": record.get("file_name"),
        "file_type": record.get("file_type"),
        "file_size": record.get("file_size", 0),
        "status": record.get("status", "parsed"),
        "uploaded_at": record.get("uploaded_at"),
        "truncated": record.get("truncated", False),
    }


async def save_partial_message(conv_mgr, thread_id, full_msg=None, error_message=None, error_type="interrupted"):
    """
    统一保存AI消息到数据库的函数

    Args:
        conv_mgr: 对话管理器
        thread_id: 线程ID
        full_msg: 完整的AI消息对象（可选）
        error_message: 纯错误消息文本（当full_msg为空时使用）
        error_type: 错误类型标识
    """
    try:
        extra_metadata = {
            "error_type": error_type,
            "is_error": True,
            "error_message": error_message or f"发生错误: {error_type}",
        }
        if full_msg:
            # 保存部分生成的AI消息
            msg_dict = full_msg.model_dump() if hasattr(full_msg, "model_dump") else {}
            content = full_msg.content if hasattr(full_msg, "content") else str(full_msg)
            extra_metadata = msg_dict | extra_metadata
        else:
            content = ""

        saved_msg = await conv_mgr.add_message_by_thread_id(
            thread_id=thread_id,
            role="assistant",
            content=content,
            message_type="text",
            extra_metadata=extra_metadata,
        )

        logger.info(f"Saved message due to {error_type}: {saved_msg.id}")
        return saved_msg

    except Exception as e:
        logger.error(f"Error saving message: {e}")
        logger.error(traceback.format_exc())
        return None


async def _process_ai_extra_content(conv_mgr, thread_id, ai_msg, content, config_dict):
    """处理 AI 回复中可能包含的大纲 JSON 或是生成的章节正文"""
    if not content or not config_dict:
        return

    try:
        configurable = config_dict.get("configurable", {})
        deliverable_id = configurable.get("deliverableId")
        if not deliverable_id:
            return

        # 检查是否已经有工具调用在处理内容，避免重复保存
        msg_dict = ai_msg.extra_metadata or {}
        tool_calls = msg_dict.get("tool_calls", [])
        if tool_calls:
            content_tool_names = ["generate_section_content", "update_section_content", "batch_generate_sections", "ai_section_generation"]
            if any(tc.get("name") in content_tool_names for tc in tool_calls):
                logger.debug(f"[Deliverable Persistence] AI message has content tools ({[tc.get('name') for tc in tool_calls]}), skipping extra processing")
                return

        # 匹配 <content> 标签中的内容
        content_match = re.search(r'<content>([\s\S]*?)<\/content>', content)
        
        inner_content = ""
        if content_match:
            inner_content = content_match.group(1).strip()
            
        # 1. 尝试从中找到 JSON 数组（大纲场景）
        json_str = ""
        if inner_content:
            array_match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', inner_content)
            if array_match:
                json_str = array_match.group(0).strip()
        
        if not json_str:
            # 尝试直接在全文中找 JSON 数组
            array_match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', content)
            if array_match:
                json_str = array_match.group(0).strip()

        # 2. 如果找到了 JSON 数组，处理大纲逻辑
        if json_str:
            try:
                outline_data = json.loads(json_str)
                if isinstance(outline_data, list):
                    # 提取所有包含 content 且不为空的章节
                    all_contents = []
                    
                    def _extract_contents(items):
                        for item in items:
                            if item.get("content"):
                                all_contents.append(item.get("content"))
                            if item.get("children"):
                                _extract_contents(item.get("children"))
                    
                    _extract_contents(outline_data)
                    
                    # 保存大纲结构到数据库
                    try:
                        from src.storage.db.models import ProjectDeliverable
                        from sqlalchemy.orm.attributes import flag_modified
                        from sqlalchemy import select
                        
                        deliverable_query = select(ProjectDeliverable).where(ProjectDeliverable.id == int(deliverable_id))
                        deliverable_result = await conv_mgr.db.execute(deliverable_query)
                        deliverable = deliverable_result.scalar_one_or_none()
                        
                        if deliverable:
                            if not deliverable.extra_metadata:
                                deliverable.extra_metadata = {}
                            
                            def _clean_outline(items):
                                cleaned = []
                                for item in items:
                                    c_item = {k: v for k, v in item.items() if k != "content"}
                                    if "children" in c_item and c_item["children"]:
                                        c_item["children"] = _clean_outline(c_item["children"])
                                    cleaned.append(c_item)
                                return cleaned
                            
                            cleaned_outline = _clean_outline(outline_data)
                            deliverable.extra_metadata["outline"] = cleaned_outline
                            flag_modified(deliverable, "extra_metadata")
                            await conv_mgr.db.commit()
                            logger.info(f"Successfully saved outline structure to deliverable {deliverable_id}")
                    except Exception as db_err:
                        logger.error(f"Failed to save outline to DB: {db_err}")
                    
                    if all_contents:
                        combined_content = "\n\n".join(all_contents)
                        logger.info(f"Extracted {len(all_contents)} sections with content from AI outline")
                        
                        pseudo_msg = {
                            "name": "ai_outline_generation",
                            "content": json.dumps({
                                "operation": "content_generated",
                                "content": combined_content
                            }),
                            "tool_call_id": f"ai_outline_{ai_msg.id}"
                        }
                        await _save_tool_message(conv_mgr, thread_id, pseudo_msg, config_dict)
                    return # 处理完大纲后返回
            except:
                pass # 解析失败则继续尝试正文逻辑

        # 3. 如果没有 JSON 数组，但有 <content> 且内容较长，处理章节正文逻辑（初稿/润色场景）
        if inner_content and len(inner_content) > 100:
            logger.info(f"Detected plain text content in <content> tags for deliverable {deliverable_id}, length: {len(inner_content)}")
            
            # 模拟工具输出格式调用保存逻辑
            pseudo_msg = {
                "name": "ai_section_generation",
                "content": json.dumps({
                    "operation": "content_generated",
                    "content": inner_content
                }),
                "tool_call_id": f"ai_section_{ai_msg.id}"
            }
            await _save_tool_message(conv_mgr, thread_id, pseudo_msg, config_dict)
            
    except Exception as e:
        logger.error(f"Error processing AI extra content: {e}")
        logger.error(traceback.format_exc())


async def save_messages_from_langgraph_state(
    agent_instance,
    thread_id,
    conv_mgr,
    config_dict,
):
    """
    从 LangGraph state 中读取完整消息并保存到数据库
    这样可以获得完整的 tool_calls 参数
    """
    try:
        messages = await _get_langgraph_messages(agent_instance, config_dict)
        if messages is None:
            return

        logger.debug(f"Retrieved {len(messages)} messages from LangGraph state")
        existing_ids = await _get_existing_message_ids(conv_mgr, thread_id)

        for msg in messages:
            # 添加空值检查
            if msg is None:
                logger.warning("Skipping None message in LangGraph state")
                continue

            msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else {}
            msg_type = msg_dict.get("type", "unknown")

            msg_id = getattr(msg, 'id', None)
            # 只有当有 ID 且已存在时才跳过；如果是人类消息也跳过（由路由单独保存）
            if msg_type == "human" or (msg_id and msg_id in existing_ids):
                continue

            if msg_type == "ai":
                await _save_ai_message(conv_mgr, thread_id, msg_dict, config_dict)
            elif msg_type == "tool":
                await _save_tool_message(conv_mgr, thread_id, msg_dict, config_dict)
            else:
                logger.warning(f"Unknown message type: {msg_type}, skipping")
                continue

            logger.debug(f"Processed message type={msg_type}")

        logger.info("Saved messages from LangGraph state")

    except Exception as e:
        logger.error(f"Error saving messages from LangGraph state: {e}")
        logger.error(traceback.format_exc())


async def check_and_handle_interrupts(agent, langgraph_config, make_chunk, meta, thread_id):
    """检查并处理 LangGraph 中断状态，发送人工审批请求到前端"""
    try:
        # 获取 agent 的 graph 对象
        graph = await agent.get_graph()

        # 获取当前状态，检查是否有中断
        state = await graph.aget_state(langgraph_config)

        if not state or not state.values:
            logger.debug("No state found when checking for interrupts")
            return

        # 检查是否有中断信息
        # LangGraph 中断信息通常在 state.tasks 或 __interrupt__ 字段中
        interrupt_info = None

        # 方法1: 检查 state.tasks 中的中断
        if hasattr(state, "tasks") and state.tasks:
            for task in state.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    interrupt_info = task.interrupts[0]  # 取第一个中断
                    break

        # 方法2: 检查 state.values 中的 __interrupt__ 字段
        if not interrupt_info and state.values:
            interrupt_data = state.values.get("__interrupt__")
            if interrupt_data and isinstance(interrupt_data, list) and len(interrupt_data) > 0:
                interrupt_info = interrupt_data[0]

        # 方法3: 检查 state.next 字段，如果指向中断节点
        if not interrupt_info and hasattr(state, "next") and state.next:
            # 如果 next 指向某个需要审批的节点，可能需要额外处理
            logger.debug(f"State next nodes: {state.next}")

        if interrupt_info:
            logger.info(f"Human approval interrupt detected: {interrupt_info}")

            # 提取中断信息
            question = "是否批准以下操作？"
            operation = "需要人工审批的操作"

            if isinstance(interrupt_info, dict):
                question = interrupt_info.get("question", question)
                operation = interrupt_info.get("operation", operation)
            elif isinstance(interrupt_info, (list, tuple)) and len(interrupt_info) > 0:
                # 有些情况下中断信息可能是元组形式
                first_interrupt = interrupt_info[0]
                if isinstance(first_interrupt, dict):
                    question = first_interrupt.get("question", question)
                    operation = first_interrupt.get("operation", operation)
                else:
                    operation = str(first_interrupt)
            else:
                operation = str(interrupt_info)

            # 发送人工审批请求到前端
            logger.info(f"Sending human approval request - question: {question}, operation: {operation}")

            yield make_chunk(
                status="human_approval_required",
                thread_id=thread_id,
                interrupt_info={"question": question, "operation": operation},
            )

        else:
            logger.debug("No human approval interrupt detected")

    except Exception as e:
        logger.error(f"Error checking for interrupts: {e}")
        logger.error(traceback.format_exc())
        # 不抛出异常，避免影响主流程


# =============================================================================


@chat.post("/call")
async def call(query: str = Body(...), meta: dict = Body(None), current_user: User = Depends(get_required_user)):
    """调用模型进行简单问答（需要登录）"""
    meta = meta or {}

    # 确保 request_id 存在
    if "request_id" not in meta or not meta.get("request_id"):
        meta["request_id"] = str(uuid.uuid4())

    model = select_model(
        model_provider=meta.get("model_provider"),
        model_name=meta.get("model_name"),
        model_spec=meta.get("model_spec") or meta.get("model"),
    )

    async def call_async(query):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, model.call, query)

    response = await call_async(query)
    logger.debug({"query": query, "response": response.content})

    return {"response": response.content, "request_id": meta["request_id"]}


@chat.get("/agent")
async def get_agent(current_user: User = Depends(get_required_user)):
    """获取所有可用智能体的基本信息（需要登录）"""
    agents_info = await agent_manager.get_agents_info()

    # Return agents with basic information (without configurable_items for performance)
    agents = [
        {
            "id": agent_info["id"],
            "name": agent_info.get("name", "Unknown"),
            "description": agent_info.get("description", ""),
            "examples": agent_info.get("examples", []),
            "has_checkpointer": agent_info.get("has_checkpointer", False),
            "capabilities": agent_info.get("capabilities", []),  # 智能体能力列表
        }
        for agent_info in agents_info
    ]

    return {"agents": agents}


@chat.get("/agent/{agent_id}")
async def get_single_agent(agent_id: str, current_user: User = Depends(get_required_user)):
    """获取指定智能体的完整信息（包含配置选项）（需要登录）"""
    try:
        # 检查智能体是否存在
        if not (agent := agent_manager.get_agent(agent_id)):
            raise HTTPException(status_code=404, detail=f"智能体 {agent_id} 不存在")

        # 获取智能体的完整信息（包含 configurable_items）
        agent_info = await agent.get_info()

        return {
            "id": agent_info["id"],
            "name": agent_info.get("name", "Unknown"),
            "description": agent_info.get("description", ""),
            "examples": agent_info.get("examples", []),
            "configurable_items": agent_info.get("configurable_items", []),
            "has_checkpointer": agent_info.get("has_checkpointer", False),
            "capabilities": agent_info.get("capabilities", []),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取智能体 {agent_id} 信息出错: {e}")
        raise HTTPException(status_code=500, detail=f"获取智能体信息出错: {str(e)}")


@chat.post("/agent/{agent_id}")
async def chat_agent(
    agent_id: str,
    query: str = Body(...),
    config: dict = Body({}),
    meta: dict = Body({}),
    image_content: str | None = Body(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """使用特定智能体进行对话（需要登录）"""
    start_time = asyncio.get_event_loop().time()

    logger.info(f"agent_id: {agent_id}, query: {query}, config: {config}, meta: {meta}")
    logger.info(f"image_content present: {image_content is not None}")
    if image_content:
        logger.info(f"image_content length: {len(image_content)}")
        logger.info(f"image_content preview: {image_content[:50]}...")

    # 确保 request_id 存在
    if "request_id" not in meta or not meta.get("request_id"):
        meta["request_id"] = str(uuid.uuid4())

    meta.update(
        {
            "query": query,
            "agent_id": agent_id,
            "server_model_name": config.get("model", agent_id),
            "thread_id": config.get("thread_id"),
            "user_id": current_user.id,
            "has_image": bool(image_content),
        }
    )

    # 将meta和thread_id整合到config中
    def make_chunk(content=None, **kwargs):
        return (
            json.dumps(
                {"request_id": meta.get("request_id"), "response": content, **kwargs}, ensure_ascii=False
            ).encode("utf-8")
            + b"\n"
        )

    async def stream_messages():
        # 构建多模态消息
        if image_content:
            # 多模态消息格式
            human_message = HumanMessage(
                content=[
                    {"type": "text", "text": query},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_content}"}},
                ]
            )
            message_type = "multimodal_image"
        else:
            # 普通文本消息
            human_message = HumanMessage(content=query)
            message_type = "text"

        # 代表服务端已经收到了请求，发送前端友好的消息格式
        init_msg = {"role": "user", "content": query, "type": "human"}

        # 如果有图片，添加图片相关信息
        if image_content:
            init_msg["message_type"] = "multimodal_image"
            init_msg["image_content"] = image_content
        else:
            init_msg["message_type"] = "text"

        yield make_chunk(status="init", meta=meta, msg=init_msg)

        # Input guard
        if conf.enable_content_guard and await content_guard.check(query):
            yield make_chunk(
                status="error", error_type="content_guard_blocked", error_message="输入内容包含敏感词", meta=meta
            )
            return

        try:
            agent = agent_manager.get_agent(agent_id)
        except Exception as e:
            logger.error(f"Error getting agent {agent_id}: {e}, {traceback.format_exc()}")
            yield make_chunk(
                status="error",
                error_type="agent_error",
                error_message=f"智能体 {agent_id} 获取失败: {str(e)}",
                meta=meta,
            )
            return

        messages = [human_message]

        # 构造运行时配置，如果没有thread_id则生成一个
        user_id = str(current_user.id)
        thread_id = config.get("thread_id")
        
        # 从 meta 中提取 context（如果有）
        input_context = meta.get("context", {})
        if not isinstance(input_context, dict):
            input_context = {}
            
        if not thread_id:
            thread_id = str(uuid.uuid4())
            logger.warning(f"No thread_id provided, generated new thread_id: {thread_id}")

        # 注入基础字段
        input_context.update({"user_id": user_id, "thread_id": thread_id})

        # Initialize conversation manager
        conv_manager = ConversationManager(db)

        # 确保会话记录存在
        conversation = await conv_manager.get_conversation_by_thread_id(thread_id)
        if not conversation:
            logger.info(f"Conversation not found for thread_id {thread_id}, creating new one")
            await conv_manager.create_conversation(
                user_id=user_id,
                agent_id=agent_id,
                title=query[:50] + ("..." if len(query) > 50 else ""),
                thread_id=thread_id,
                metadata=input_context
            )

        # Save user message
        try:
            await conv_manager.add_message_by_thread_id(
                thread_id=thread_id,
                role="user",
                content=query,
                message_type=message_type,
                image_content=image_content,
                extra_metadata={"raw_message": human_message.model_dump()},
            )
        except Exception as e:
            logger.error(f"Error saving user message: {e}")

        try:
            assert thread_id, "thread_id is required"
            attachments = await conv_manager.get_attachments_by_thread_id(thread_id)
            input_context["attachments"] = attachments
            logger.debug(f"Loaded {len(attachments)} attachments for thread_id={thread_id}")
        except Exception as e:
            logger.error(f"Error loading attachments for thread_id={thread_id}: {e}")
            input_context["attachments"] = []

        try:
            full_msg = None
            langgraph_config = {"configurable": input_context}
            async for msg, metadata in agent.stream_messages(messages, input_context=input_context):
                if isinstance(msg, AIMessageChunk):
                    full_msg = msg if not full_msg else full_msg + msg
                    if conf.enable_content_guard and await content_guard.check_with_keywords(full_msg.content[-20:]):
                        logger.warning("Sensitive content detected in stream")
                        await save_partial_message(conv_manager, thread_id, full_msg, "content_guard_blocked")
                        meta["time_cost"] = asyncio.get_event_loop().time() - start_time
                        yield make_chunk(status="interrupted", message="检测到敏感内容，已中断输出", meta=meta)
                        return

                    yield make_chunk(content=msg.content, msg=msg.model_dump(), metadata=metadata, status="loading")

                else:
                    msg_dict = msg.model_dump()
                    msg_type = msg_dict.get("type")
                    yield make_chunk(msg=msg_dict, metadata=metadata, status="loading")

                    try:
                        # 1. 如果是 AI 消息（非 chunk），立即保存
                        if msg_type == "ai":
                            await _save_ai_message(conv_manager, thread_id, msg_dict, langgraph_config)
                            if full_msg:
                                setattr(full_msg, "_saved", True)
                        
                        # 2. 如果是工具消息，确保之前的 AI 消息已保存
                        elif msg_type == "tool":
                            # 如果有积累的 AI 消息 chunk 且尚未保存，先保存它
                            if full_msg and not getattr(full_msg, "_saved", False):
                                ai_msg_dict = full_msg.model_dump()
                                # 补充 tool_calls，因为 AIMessageChunk 可能只包含 tool_call_chunks
                                if not ai_msg_dict.get("tool_calls") and hasattr(full_msg, "tool_calls"):
                                    ai_msg_dict["tool_calls"] = full_msg.tool_calls
                                
                                await _save_ai_message(conv_manager, thread_id, ai_msg_dict, langgraph_config)
                                setattr(full_msg, "_saved", True)
                            
                            # 立即尝试同步状态，作为兜底
                            await save_messages_from_langgraph_state(agent, thread_id, conv_manager, langgraph_config)
                            
                            # 保存工具消息及其副作用
                            await _save_tool_message(conv_manager, thread_id, msg_dict, langgraph_config)
                            
                            graph = await agent.get_graph()
                            state = await graph.aget_state(langgraph_config)
                            agent_state = _extract_agent_state(getattr(state, "values", {})) if state else {}
                            if agent_state:
                                yield make_chunk(status="agent_state", agent_state=agent_state, meta=meta)
                    except Exception as e:
                        logger.error(f"Error processing tool message in stream: {e}")
                        pass

            if (
                conf.enable_content_guard
                and hasattr(full_msg, "content")
                and await content_guard.check(full_msg.content)
            ):
                logger.warning("Sensitive content detected in final message")
                await save_partial_message(conv_manager, thread_id, full_msg, "content_guard_blocked")
                meta["time_cost"] = asyncio.get_event_loop().time() - start_time
                yield make_chunk(status="interrupted", message="检测到敏感内容，已中断输出", meta=meta)
                return

            # After streaming finished, check for interrupts and save messages

            # Check for human approval interrupts
            async for chunk in check_and_handle_interrupts(agent, langgraph_config, make_chunk, meta, thread_id):
                yield chunk

            meta["time_cost"] = asyncio.get_event_loop().time() - start_time
            try:
                graph = await agent.get_graph()
                state = await graph.aget_state(langgraph_config)
                agent_state = _extract_agent_state(getattr(state, "values", {})) if state else {}
            except Exception:
                agent_state = {}

            if agent_state:
                yield make_chunk(status="agent_state", agent_state=agent_state, meta=meta)

            yield make_chunk(status="finished", meta=meta)

            # Save all messages from LangGraph state
            await save_messages_from_langgraph_state(
                agent_instance=agent,
                thread_id=thread_id,
                conv_mgr=conv_manager,
                config_dict=langgraph_config,
            )

        except (asyncio.CancelledError, ConnectionError) as e:
            # 客户端主动中断连接，检查中断并保存已生成的部分内容
            logger.warning(f"Client disconnected, cancelling stream: {e}")

            # 保存中断消息到数据库
            async with db_manager.get_async_session_context() as new_db:
                new_conv_manager = ConversationManager(new_db)
                await save_partial_message(
                    new_conv_manager,
                    thread_id,
                    full_msg=full_msg,
                    error_message="对话已中断" if not full_msg else None,
                    error_type="interrupted",
                )

            # 通知前端中断（可能发送不到，但用于一致性）
            yield make_chunk(status="interrupted", message="对话已中断", meta=meta)

        except Exception as e:
            logger.error(f"Error streaming messages: {e}, {traceback.format_exc()}")

            error_msg = f"Error streaming messages: {e}"
            error_type = "unexpected_error"

            # 保存错误消息到数据库
            async with db_manager.get_async_session_context() as new_db:
                new_conv_manager = ConversationManager(new_db)
                await save_partial_message(
                    new_conv_manager,
                    thread_id,
                    full_msg=full_msg,
                    error_message=error_msg,
                    error_type=error_type,
                )

            yield make_chunk(status="error", error_type=error_type, error_message=error_msg, meta=meta)

    return StreamingResponse(stream_messages(), media_type="application/json")


# =============================================================================
# > === 模型管理分组 ===
# =============================================================================


@chat.get("/models")
async def get_chat_models(model_provider: str, current_user: User = Depends(get_admin_user)):
    """获取指定模型提供商的模型列表（需要登录）"""
    model = select_model(model_provider=model_provider)
    return {"models": model.get_models()}


@chat.post("/models/update")
async def update_chat_models(model_provider: str, model_names: list[str], current_user=Depends(get_admin_user)):
    """更新指定模型提供商的模型列表 (仅管理员)"""
    conf.model_names[model_provider].models = model_names
    conf._save_models_to_file(model_provider)
    return {"models": conf.model_names[model_provider].models}


@chat.get("/tools")
async def get_tools(agent_id: str, current_user: User = Depends(get_required_user)):
    """获取所有可用工具（需要登录）"""
    # 获取Agent实例和配置类
    if not (agent := agent_manager.get_agent(agent_id)):
        raise HTTPException(status_code=404, detail=f"智能体 {agent_id} 不存在")

    if hasattr(agent, "get_tools") and callable(agent.get_tools):
        if asyncio.iscoroutinefunction(agent.get_tools):
            tools = await agent.get_tools()
        else:
            tools = agent.get_tools()
    else:
        tools = get_buildin_tools()

    tools_info = gen_tool_info(tools)
    return {"tools": {tool["id"]: tool for tool in tools_info}}


@chat.post("/agent/{agent_id}/resume")
async def resume_agent_chat(
    agent_id: str,
    thread_id: str = Body(...),
    approved: bool = Body(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """恢复被人工审批中断的对话（需要登录）"""
    start_time = asyncio.get_event_loop().time()
    logger.info(f"Resuming agent_id: {agent_id}, thread_id: {thread_id}, approved: {approved}")

    meta = {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "user_id": current_user.id,
        "approved": approved,
    }
    if "request_id" not in meta or not meta.get("request_id"):
        meta["request_id"] = str(uuid.uuid4())

    async def stream_resume():
        # 定义resume专用的make_chunk函数，与主聊天端点保持一致
        def make_resume_chunk(content=None, **kwargs):
            return (
                json.dumps(
                    {"request_id": meta.get("request_id"), "response": content, **kwargs}, ensure_ascii=False
                ).encode("utf-8")
                + b"\n"
            )

        try:
            agent = agent_manager.get_agent(agent_id)
        except Exception as e:
            logger.error(f"Error getting agent {agent_id}: {e}, {traceback.format_exc()}")
            yield (
                f'{{"request_id": "{meta.get("request_id")}", "message": '
                f'"Error getting agent {agent_id}: {e}", "status": "error"}}\n'
            )
            return

        # 发送init状态块，与主聊天端点保持一致
        init_msg = {"type": "system", "content": f"Resume with approved: {approved}"}
        yield make_resume_chunk(status="init", meta=meta, msg=init_msg)

        # 使用 Command(resume=approved) 恢复执行
        resume_command = Command(resume=approved)
        graph = await agent.get_graph()

        # 加载 context（包含 tools, model 等配置）
        input_context = {"user_id": str(current_user.id), "thread_id": thread_id}
        context = agent.context_schema.from_file(module_name=agent.module_name, input_context=input_context)
        logger.debug(f"Resume with context: {context}")

        # 创建流式数据源
        stream_source = graph.astream(
            resume_command, context=context, config={"configurable": input_context}, stream_mode="messages"
        )

        try:
            async for msg, metadata in stream_source:
                # 确保msg有正确的ID结构
                msg_dict = msg.model_dump()
                if "id" not in msg_dict:
                    msg_dict["id"] = str(uuid.uuid4())

                yield make_resume_chunk(
                    content=getattr(msg, "content", ""), msg=msg_dict, metadata=metadata, status="loading"
                )

            meta["time_cost"] = asyncio.get_event_loop().time() - start_time
            yield make_resume_chunk(status="finished", meta=meta)

            # 保存消息到数据库
            langgraph_config = {"configurable": input_context}
            conv_manager = ConversationManager(db)
            await save_messages_from_langgraph_state(
                agent_instance=agent,
                thread_id=thread_id,
                conv_mgr=conv_manager,
                config_dict=langgraph_config,
            )

        except (asyncio.CancelledError, ConnectionError) as e:
            # 客户端主动中断连接
            logger.warning(f"Client disconnected during resume: {e}")

            # 保存中断消息到数据库
            async with db_manager.get_async_session_context() as new_db:
                new_conv_manager = ConversationManager(new_db)
                await save_partial_message(
                    new_conv_manager, thread_id, error_message="对话恢复已中断", error_type="resume_interrupted"
                )

            yield make_resume_chunk(status="interrupted", message="对话恢复已中断", meta=meta)

        except Exception as e:
            # 处理其他异常
            logger.error(f"Error during resume: {e}, {traceback.format_exc()}")

            # 保存错误消息到数据库
            async with db_manager.get_async_session_context() as new_db:
                new_conv_manager = ConversationManager(new_db)
                await save_partial_message(
                    new_conv_manager, thread_id, error_message=f"Error during resume: {e}", error_type="resume_error"
                )

            yield make_resume_chunk(message=f"Error during resume: {e}", status="error")

    return StreamingResponse(stream_resume(), media_type="application/json")


@chat.post("/agent/{agent_id}/config")
async def save_agent_config(agent_id: str, config: dict = Body(...), current_user: User = Depends(get_required_user)):
    """保存智能体配置到YAML文件（需要登录）"""
    try:
        # 获取Agent实例和配置类
        if not (agent := agent_manager.get_agent(agent_id)):
            raise HTTPException(status_code=404, detail=f"智能体 {agent_id} 不存在")

        # 使用配置类的save_to_file方法保存配置
        result = agent.context_schema.save_to_file(config, agent.module_name)

        if result:
            return {"success": True, "message": f"智能体 {agent.name} 配置已保存"}
        else:
            raise HTTPException(status_code=500, detail="保存智能体配置失败")

    except Exception as e:
        logger.error(f"保存智能体配置出错: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"保存智能体配置出错: {str(e)}")


@chat.get("/agent/{agent_id}/history")
async def get_agent_history(
    agent_id: str, thread_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    """获取智能体历史消息（需要登录）- NEW STORAGE ONLY"""
    try:
        # 获取Agent实例验证
        if not agent_manager.get_agent(agent_id):
            raise HTTPException(status_code=404, detail=f"智能体 {agent_id} 不存在")

        # Use new storage system ONLY
        conv_manager = ConversationManager(db)
        messages = await conv_manager.get_messages_by_thread_id(thread_id)

        # Convert to frontend-compatible format
        history = []
        for msg in messages:
            # Map role to type that frontend expects
            role_type_map = {"user": "human", "assistant": "ai", "tool": "tool", "system": "system"}

            msg_dict = {
                "id": msg.id,  # Include message ID for feedback
                "type": role_type_map.get(msg.role, msg.role),  # human/ai/tool/system
                "content": msg.content,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
                "error_type": msg.extra_metadata.get("error_type") if msg.extra_metadata else None,
                "error_message": msg.extra_metadata.get("error_message") if msg.extra_metadata else None,
                "extra_metadata": msg.extra_metadata,  # 保留完整的metadata以备前端需要
                "message_type": msg.message_type,  # 添加消息类型字段
                "image_content": msg.image_content,  # 添加图片内容字段
            }

            # Add tool calls if present (for AI messages)
            if msg.tool_calls and len(msg.tool_calls) > 0:
                msg_dict["tool_calls"] = [
                    {
                        "id": str(tc.id),
                        "name": tc.tool_name,
                        "function": {"name": tc.tool_name},
                        "args": tc.tool_input or {},
                        "tool_call_result": {"content": (tc.tool_output or "")} if tc.status == "success" else None,
                        "status": tc.status,
                        "error_message": tc.error_message,
                    }
                    for tc in msg.tool_calls
                ]

            history.append(msg_dict)

        logger.info(f"Loaded {len(history)} messages from new storage for thread {thread_id}")
        return {"history": history}

    except Exception as e:
        logger.error(f"获取智能体历史消息出错: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取智能体历史消息出错: {str(e)}")


@chat.get("/agent/{agent_id}/state")
async def get_agent_state(
    agent_id: str,
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        if not agent_manager.get_agent(agent_id):
            raise HTTPException(status_code=404, detail=f"智能体 {agent_id} 不存在")

        conv_manager = ConversationManager(db)
        await _require_user_conversation(conv_manager, thread_id, str(current_user.id))

        agent = agent_manager.get_agent(agent_id)
        graph = await agent.get_graph()
        langgraph_config = {"configurable": {"user_id": str(current_user.id), "thread_id": thread_id}}
        state = await graph.aget_state(langgraph_config)
        agent_state = _extract_agent_state(getattr(state, "values", {})) if state else {}

        return {"agent_state": agent_state}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取AgentState出错: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取AgentState出错: {str(e)}")


@chat.get("/agent/{agent_id}/config")
async def get_agent_config(agent_id: str, current_user: User = Depends(get_required_user)):
    """从YAML文件加载智能体配置（需要登录）"""
    try:
        # 检查智能体是否存在
        if not (agent := agent_manager.get_agent(agent_id)):
            raise HTTPException(status_code=404, detail=f"智能体 {agent_id} 不存在")

        config = await agent.get_config()
        logger.debug(f"config: {config}, ContextClass: {agent.context_schema=}")
        return {"success": True, "config": config}

    except Exception as e:
        logger.error(f"加载智能体配置出错: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"加载智能体配置出错: {str(e)}")


# ==================== 线程管理 API ====================


class ThreadCreate(BaseModel):
    title: str | None = None
    agent_id: str
    metadata: dict | None = None


class ThreadResponse(BaseModel):
    id: str
    user_id: str
    agent_id: str
    title: str | None = None
    created_at: str
    updated_at: str


class AttachmentResponse(BaseModel):
    file_id: str
    file_name: str
    file_type: str | None = None
    file_size: int
    status: str
    uploaded_at: str
    truncated: bool | None = False


class AttachmentLimits(BaseModel):
    allowed_extensions: list[str]
    max_size_bytes: int


class AttachmentListResponse(BaseModel):
    attachments: list[AttachmentResponse]
    limits: AttachmentLimits


# =============================================================================
# > === 会话管理分组 ===
# =============================================================================


@chat.post("/thread", response_model=ThreadResponse)
async def create_thread(
    thread: ThreadCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_required_user)
):
    """创建新对话线程 (使用新存储系统)"""
    thread_id = str(uuid.uuid4())
    logger.debug(f"thread.agent_id: {thread.agent_id}")

    # Create conversation using new storage system
    conv_manager = ConversationManager(db)
    conversation = await conv_manager.create_conversation(
        user_id=str(current_user.id),
        agent_id=thread.agent_id,
        title=thread.title or "新的对话",
        thread_id=thread_id,
        metadata=thread.metadata,
    )

    logger.info(f"Created conversation with thread_id: {thread_id}")

    return {
        "id": conversation.thread_id,
        "user_id": conversation.user_id,
        "agent_id": conversation.agent_id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


@chat.get("/threads", response_model=list[ThreadResponse])
async def list_threads(
    agent_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_required_user)
):
    """获取用户的所有对话线程 (使用新存储系统)"""
    assert agent_id, "agent_id 不能为空"

    logger.debug(f"agent_id: {agent_id}")

    # Use new storage system
    conv_manager = ConversationManager(db)
    conversations = await conv_manager.list_conversations(
        user_id=str(current_user.id),
        agent_id=agent_id,
        status="active",
    )

    return [
        {
            "id": conv.thread_id,
            "user_id": conv.user_id,
            "agent_id": conv.agent_id,
            "title": conv.title,
            "created_at": conv.created_at.isoformat(),
            "updated_at": conv.updated_at.isoformat(),
        }
        for conv in conversations
    ]


@chat.delete("/thread/{thread_id}")
async def delete_thread(
    thread_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_required_user)
):
    """删除对话线程 (使用新存储系统)"""
    # Use new storage system
    conv_manager = ConversationManager(db)
    conversation = await conv_manager.get_conversation_by_thread_id(thread_id)

    if not conversation or conversation.user_id != str(current_user.id):
        raise HTTPException(status_code=404, detail="对话线程不存在")

    # Soft delete
    success = await conv_manager.delete_conversation(thread_id, soft_delete=True)

    if not success:
        raise HTTPException(status_code=500, detail="删除失败")

    return {"message": "删除成功"}


class ThreadUpdate(BaseModel):
    title: str | None = None


@chat.put("/thread/{thread_id}", response_model=ThreadResponse)
async def update_thread(
    thread_id: str,
    thread_update: ThreadUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """更新对话线程信息 (使用新存储系统)"""
    # Use new storage system
    conv_manager = ConversationManager(db)
    conversation = await conv_manager.get_conversation_by_thread_id(thread_id)

    if not conversation or conversation.user_id != str(current_user.id) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")

    # Update conversation
    updated_conv = await conv_manager.update_conversation(
        thread_id=thread_id,
        title=thread_update.title,
    )

    if not updated_conv:
        raise HTTPException(status_code=500, detail="更新失败")

    return {
        "id": updated_conv.thread_id,
        "user_id": updated_conv.user_id,
        "agent_id": updated_conv.agent_id,
        "title": updated_conv.title,
        "created_at": updated_conv.created_at.isoformat(),
        "updated_at": updated_conv.updated_at.isoformat(),
    }


@chat.post("/thread/{thread_id}/attachments", response_model=AttachmentResponse)
async def upload_thread_attachment(
    thread_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """上传并解析附件为 Markdown，附加到指定对话线程。"""
    conv_manager = ConversationManager(db)
    conversation = await _require_user_conversation(conv_manager, thread_id, str(current_user.id))

    try:
        conversion = await convert_upload_to_markdown(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(f"附件解析失败: {exc}")
        raise HTTPException(status_code=500, detail="附件解析失败，请稍后重试") from exc

    attachment_record = {
        "file_id": conversion.file_id,
        "file_name": conversion.file_name,
        "file_type": conversion.file_type,
        "file_size": conversion.file_size,
        "status": "parsed",
        "markdown": conversion.markdown,
        "uploaded_at": utc_isoformat(),
        "truncated": conversion.truncated,
    }
    await conv_manager.add_attachment(conversation.id, attachment_record)

    return _serialize_attachment(attachment_record)


@chat.get("/thread/{thread_id}/attachments", response_model=AttachmentListResponse)
async def list_thread_attachments(
    thread_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """列出当前对话线程的所有附件元信息。"""
    conv_manager = ConversationManager(db)
    conversation = await _require_user_conversation(conv_manager, thread_id, str(current_user.id))
    attachments = await conv_manager.get_attachments(conversation.id)
    return {
        "attachments": [_serialize_attachment(item) for item in attachments],
        "limits": {
            "allowed_extensions": sorted(ATTACHMENT_ALLOWED_EXTENSIONS),
            "max_size_bytes": MAX_ATTACHMENT_SIZE_BYTES,
        },
    }


@chat.delete("/thread/{thread_id}/attachments/{file_id}")
async def delete_thread_attachment(
    thread_id: str,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """移除指定附件。"""
    conv_manager = ConversationManager(db)
    conversation = await _require_user_conversation(conv_manager, thread_id, str(current_user.id))
    removed = await conv_manager.remove_attachment(conversation.id, file_id)
    if not removed:
        raise HTTPException(status_code=404, detail="附件不存在或已被删除")
    return {"message": "附件已删除"}


# =============================================================================
# > === 消息反馈分组 ===
# =============================================================================


class MessageFeedbackRequest(BaseModel):
    rating: str  # 'like' or 'dislike'
    reason: str | None = None  # Optional reason for dislike


class MessageFeedbackResponse(BaseModel):
    id: int
    message_id: int
    rating: str
    reason: str | None
    created_at: str


@chat.post("/message/{message_id}/feedback", response_model=MessageFeedbackResponse)
async def submit_message_feedback(
    message_id: int,
    feedback_data: MessageFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """Submit user feedback for a specific message"""
    try:
        # Validate rating
        if feedback_data.rating not in ["like", "dislike"]:
            raise HTTPException(status_code=422, detail="Rating must be 'like' or 'dislike'")

        # Verify message exists and get conversation to check permissions
        message_result = await db.execute(select(Message).filter_by(id=message_id))
        message = message_result.scalar_one_or_none()

        if not message:
            raise HTTPException(status_code=404, detail="Message not found")

        # Verify user has access to this message (through conversation)
        conversation_result = await db.execute(select(Conversation).filter_by(id=message.conversation_id))
        conversation = conversation_result.scalar_one_or_none()
        if not conversation or conversation.user_id != str(current_user.id):
            raise HTTPException(status_code=403, detail="Access denied")

        # Check if feedback already exists (user can only submit once)
        existing_feedback_result = await db.execute(
            select(MessageFeedback).filter_by(message_id=message_id, user_id=str(current_user.id))
        )
        existing_feedback = existing_feedback_result.scalar_one_or_none()

        if existing_feedback:
            raise HTTPException(status_code=409, detail="Feedback already submitted for this message")

        # Create new feedback
        new_feedback = MessageFeedback(
            message_id=message_id,
            user_id=str(current_user.id),
            rating=feedback_data.rating,
            reason=feedback_data.reason,
        )

        db.add(new_feedback)
        await db.commit()
        await db.refresh(new_feedback)

        logger.info(f"User {current_user.id} submitted {feedback_data.rating} feedback for message {message_id}")

        return MessageFeedbackResponse(
            id=new_feedback.id,
            message_id=new_feedback.message_id,
            rating=new_feedback.rating,
            reason=new_feedback.reason,
            created_at=new_feedback.created_at.isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting message feedback: {e}, {traceback.format_exc()}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to submit feedback: {str(e)}")


@chat.get("/message/{message_id}/feedback")
async def get_message_feedback(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """Get feedback status for a specific message (for current user)"""
    try:
        # Get user's feedback for this message
        feedback_result = await db.execute(
            select(MessageFeedback).filter_by(message_id=message_id, user_id=str(current_user.id))
        )
        feedback = feedback_result.scalar_one_or_none()

        if not feedback:
            return {"has_feedback": False, "feedback": None}

        return {
            "has_feedback": True,
            "feedback": {
                "id": feedback.id,
                "rating": feedback.rating,
                "reason": feedback.reason,
                "created_at": feedback.created_at.isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Error getting message feedback: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get feedback: {str(e)}")


# =============================================================================
# > === 多模态图片支持分组 ===
# =============================================================================


@chat.post("/image/upload", response_model=ImageUploadResponse)
async def upload_image(file: UploadFile = File(...), current_user: User = Depends(get_required_user)):
    """
    上传并处理图片，返回base64编码的图片数据
    """
    try:
        # 验证文件类型
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="只支持图片文件上传")

        # 读取文件内容
        image_data = await file.read()

        # 检查文件大小（10MB限制，超过后会压缩到5MB）
        if len(image_data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="图片文件过大，请上传小于10MB的图片")

        # 处理图片
        result = process_uploaded_image(image_data, file.filename)

        if not result["success"]:
            raise HTTPException(status_code=400, detail=f"图片处理失败: {result['error']}")

        logger.info(
            f"用户 {current_user.id} 成功上传图片: {file.filename}, "
            f"尺寸: {result['width']}x{result['height']}, "
            f"格式: {result['format']}, "
            f"大小: {result['size_bytes']} bytes"
        )

        return ImageUploadResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"图片上传处理失败: {str(e)}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"图片处理失败: {str(e)}")
