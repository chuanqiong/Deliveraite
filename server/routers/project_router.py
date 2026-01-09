import datetime as dt
from typing import List, Optional, Any
import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from urllib.parse import quote
from src.services.export_service import export_markdown_to_docx
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.db.manager import db_manager
import re
from src import knowledge_base
from src.storage.db.models import Project, ProjectHistory, User, ProjectDeliverable, ProjectDeliverableContent
from server.utils.auth_middleware import get_current_user, get_required_user, get_db
from src.utils.datetime_utils import utc_now, utc_isoformat, ensure_utc
from src.config.app import config

# 创建路由器
projects_router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger(__name__)

# =============================================================================
# === 请求和响应模型 ===
# =============================================================================

class ProjectBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="项目名称")
    description: Optional[str] = Field(None, description="项目描述")
    start_date: Optional[str] = Field(None, description="开始日期 (ISO格式)")
    end_date: Optional[str] = Field(None, description="结束日期 (ISO格式)")
    status: str = Field("待启动", description="项目状态")
    progress: int = Field(0, ge=0, le=100, description="项目进度")
    metadata: Optional[dict] = Field(None, description="额外元数据")

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[int] = Field(None, ge=0, le=100)
    metadata: Optional[dict] = None

class ProjectResponse(ProjectBase):
    id: int
    metadata: Optional[dict] = {}
    is_deleted: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True

class ProjectListResponse(BaseModel):
    total: int
    items: List[ProjectResponse]
    page: int
    page_size: int

class ProjectDeliverableBase(BaseModel):
    name: str = Field(..., description="交付物名称")
    quantity: int = Field(0, description="数量")
    word_count: int = Field(0, description="字数")
    status: str = Field("未撰写", description="状态")

class ProjectDeliverableCreate(ProjectDeliverableBase):
    pass

class ProjectDeliverableUpdate(BaseModel):
    name: Optional[str] = None
    quantity: Optional[int] = None
    word_count: Optional[int] = None
    status: Optional[str] = None
    content: Optional[str] = None
    metadata: Optional[dict] = None

class ProjectDeliverableResponse(ProjectDeliverableBase):
    id: int
    project_id: int
    metadata: Optional[dict] = {}
    can_download: bool = False
    is_deleted: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True

class ProjectDeliverableListResponse(BaseModel):
    total: int
    items: List[ProjectDeliverableResponse]
    page: int
    page_size: int

class ProjectDeliverableExtractRequest(BaseModel):
    file_id: str
    db_id: str = "kb_0f7ffb5eec05a6132546d7f26a7fd32b"

class StandardResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: Optional[Any] = None

# =============================================================================
# === 辅助函数 ===
# =============================================================================

async def log_project_history(
    db: AsyncSession, 
    project_id: int, 
    field_name: str, 
    old_value: str, 
    new_value: str, 
    user_id: str
):
    history = ProjectHistory(
        project_id=project_id,
        field_name=field_name,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
        changed_by=user_id
    )
    db.add(history)

def parse_iso_datetime(iso_str: Optional[str]) -> Optional[dt.datetime]:
    if not iso_str:
        return None
    try:
        # 简单的 ISO 格式解析，实际项目中可能需要更复杂的处理
        return dt.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
    except ValueError:
        return None

# =============================================================================
# === 路由实现 ===
# =============================================================================

@projects_router.post("", response_model=StandardResponse)
async def create_project(
    project_in: ProjectCreate, 
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """新增项目"""
    project = Project(
        name=project_in.name,
        user_id=current_user.user_id,
        description=project_in.description,
        start_date=parse_iso_datetime(project_in.start_date),
        end_date=parse_iso_datetime(project_in.end_date),
        status=project_in.status,
        progress=project_in.progress,
        extra_metadata=project_in.metadata
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    
    return StandardResponse(data=project.to_dict())

@projects_router.get("", response_model=StandardResponse)
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """项目列表接口（分页）"""
    offset = (page - 1) * page_size
    
    # 查询总数
    total_query = select(func.count()).select_from(Project).where(
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    )
    total_result = await db.execute(total_query)
    total = total_result.scalar()
    
    # 查询分页数据
    # 构建子查询获取实时的交付物数量
    total_deliverables_subquery = (
        select(func.count(ProjectDeliverable.id))
        .where(ProjectDeliverable.project_id == Project.id, ProjectDeliverable.is_deleted == 0)
        .scalar_subquery()
    )
    completed_deliverables_subquery = (
        select(func.count(ProjectDeliverable.id))
        .where(ProjectDeliverable.project_id == Project.id, ProjectDeliverable.is_deleted == 0, ProjectDeliverable.status != "未撰写")
        .scalar_subquery()
    )

    query = select(
        Project, 
        total_deliverables_subquery.label("deliverables_total"),
        completed_deliverables_subquery.label("deliverables_completed")
    ).where(
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    ).order_by(desc(Project.created_at)).offset(offset).limit(page_size)
    
    result = await db.execute(query)
    rows = result.all()
    
    project_items = []
    for row in rows:
        project, total_del, completed_del = row
        p_dict = project.to_dict()
        # 实时更新交付物数量到 metadata 中，确保前端展示准确
        if "metadata" not in p_dict or p_dict["metadata"] is None:
            p_dict["metadata"] = {}
        p_dict["metadata"]["deliverablesTotal"] = total_del
        p_dict["metadata"]["deliverablesCompleted"] = completed_del
        project_items.append(p_dict)
    
    data = ProjectListResponse(
        total=total,
        items=project_items,
        page=page,
        page_size=page_size
    )
    return StandardResponse(data=data)

@projects_router.get("/search", response_model=StandardResponse)
async def search_projects(
    name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """项目搜索接口"""
    offset = (page - 1) * page_size
    
    filters = [Project.is_deleted == 0, Project.user_id == current_user.user_id]
    if name:
        filters.append(Project.name.contains(name))
    if status:
        filters.append(Project.status == status)
    
    # 查询总数
    total_query = select(func.count()).select_from(Project).where(*filters)
    total_result = await db.execute(total_query)
    total = total_result.scalar()
    
    # 查询数据
    # 构建子查询获取实时的交付物数量
    total_deliverables_subquery = (
        select(func.count(ProjectDeliverable.id))
        .where(ProjectDeliverable.project_id == Project.id, ProjectDeliverable.is_deleted == 0)
        .scalar_subquery()
    )
    completed_deliverables_subquery = (
        select(func.count(ProjectDeliverable.id))
        .where(ProjectDeliverable.project_id == Project.id, ProjectDeliverable.is_deleted == 0, ProjectDeliverable.status != "未撰写")
        .scalar_subquery()
    )

    query = select(
        Project,
        total_deliverables_subquery.label("deliverables_total"),
        completed_deliverables_subquery.label("deliverables_completed")
    ).where(*filters).order_by(desc(Project.created_at)).offset(offset).limit(page_size)
    
    result = await db.execute(query)
    rows = result.all()
    
    project_items = []
    for row in rows:
        project, total_del, completed_del = row
        p_dict = project.to_dict()
        # 实时更新交付物数量到 metadata 中，确保前端展示准确
        if "metadata" not in p_dict or p_dict["metadata"] is None:
            p_dict["metadata"] = {}
        p_dict["metadata"]["deliverablesTotal"] = total_del
        p_dict["metadata"]["deliverablesCompleted"] = completed_del
        project_items.append(p_dict)
    
    data = ProjectListResponse(
        total=total,
        items=project_items,
        page=page,
        page_size=page_size
    )
    return StandardResponse(data=data)
    
@projects_router.get("/{project_id}", response_model=StandardResponse)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取单个项目详情"""
    # 构建子查询获取实时的交付物数量
    total_deliverables_subquery = (
        select(func.count(ProjectDeliverable.id))
        .where(ProjectDeliverable.project_id == project_id, ProjectDeliverable.is_deleted == 0)
        .scalar_subquery()
    )
    completed_deliverables_subquery = (
        select(func.count(ProjectDeliverable.id))
        .where(ProjectDeliverable.project_id == project_id, ProjectDeliverable.is_deleted == 0, ProjectDeliverable.status != "未撰写")
        .scalar_subquery()
    )

    query = select(
        Project,
        total_deliverables_subquery.label("deliverables_total"),
        completed_deliverables_subquery.label("deliverables_completed")
    ).where(
        Project.id == project_id, 
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    )
    result = await db.execute(query)
    row = result.first()
    
    if not row:
        raise HTTPException(status_code=404, detail="项目未找到或无权访问")
        
    project, total_del, completed_del = row
    p_dict = project.to_dict()
    # 实时更新交付物数量到 metadata 中，确保前端展示准确
    if "metadata" not in p_dict or p_dict["metadata"] is None:
        p_dict["metadata"] = {}
    p_dict["metadata"]["deliverablesTotal"] = total_del
    p_dict["metadata"]["deliverablesCompleted"] = completed_del
        
    return StandardResponse(data=p_dict)

@projects_router.put("/{project_id}", response_model=StandardResponse)
async def update_project(
    project_id: int,
    project_in: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新项目"""
    query = select(Project).where(
        Project.id == project_id, 
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    )
    result = await db.execute(query)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目未找到或无权访问")
    
    update_data = project_in.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        # 映射 field 到模型属性
        attr_name = "extra_metadata" if field == "metadata" else field
        
        if not hasattr(project, attr_name):
            continue
            
        old_val = getattr(project, attr_name)
        new_val = value
        
        # 处理日期转换
        if field in ["start_date", "end_date"] and value:
            new_val = parse_iso_datetime(value)
        
        # 合并元数据而不是完全替换，方便部分更新
        if field == "metadata":
            old_meta = old_val or {}
            new_val = {**old_meta, **(value or {})}
            
        setattr(project, attr_name, new_val)
            
        # 记录历史
        if str(old_val) != str(new_val):
            await log_project_history(db, project_id, field, old_val, new_val, current_user.user_id)
            
    project.updated_at = utc_now()
    await db.commit()
    await db.refresh(project)
    
    return StandardResponse(data=project.to_dict())

@projects_router.post("/{project_id}/files/{file_id}", response_model=StandardResponse)
async def link_project_file(
    project_id: int,
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """关联项目与知识库文件"""
    query = select(Project).where(
        Project.id == project_id, 
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    )
    result = await db.execute(query)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目未找到或无权访问")
    
    meta = project.extra_metadata or {}
    kb_files = meta.get("kb_files", [])
    
    if file_id not in kb_files:
        kb_files.append(file_id)
        meta["kb_files"] = kb_files
        project.extra_metadata = meta
        # 强制标记字段已修改，确保 SQLAlchemy 检测到 JSON 变化
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(project, "extra_metadata")
        
        await log_project_history(db, project_id, "kb_files", "added", file_id, current_user.user_id)
        await db.commit()
        await db.refresh(project)
    
    return StandardResponse(data=project.to_dict())

@projects_router.delete("/{project_id}/files/{file_id}", response_model=StandardResponse)
async def unlink_project_file(
    project_id: int,
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """取消项目与知识库文件的关联"""
    query = select(Project).where(
        Project.id == project_id, 
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    )
    result = await db.execute(query)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目未找到或无权访问")
    
    meta = project.extra_metadata or {}
    kb_files = meta.get("kb_files", [])
    
    if file_id in kb_files:
        kb_files.remove(file_id)
        meta["kb_files"] = kb_files
        project.extra_metadata = meta
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(project, "extra_metadata")
        
        await log_project_history(db, project_id, "kb_files", "removed", file_id, current_user.user_id)
        await db.commit()
        await db.refresh(project)
    
    return StandardResponse(data=project.to_dict())

@projects_router.delete("/{project_id}", response_model=StandardResponse)
async def delete_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除项目接口（软删除）"""
    query = select(Project).where(
        Project.id == project_id, 
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    )
    result = await db.execute(query)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="项目未找到或无权访问")
    
    project.is_deleted = 1
    project.deleted_at = utc_now()
    
    await log_project_history(db, project_id, "is_deleted", "0", "1", current_user.user_id)
    
    await db.commit()
    
    return StandardResponse(message="项目已成功删除")

# =============================================================================
# === 交付物管理接口 ===
# =============================================================================

@projects_router.post("/{project_id}/deliverables/cleanup", response_model=StandardResponse)
async def cleanup_deliverables_metadata(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """清理交付物元数据中残留的 content 字段"""
    # 验证项目归属
    project_query = select(Project).where(
        Project.id == project_id, 
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    )
    project_result = await db.execute(project_query)
    if not project_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="项目未找到或无权访问")
        
    query = select(ProjectDeliverable).where(
        ProjectDeliverable.project_id == project_id,
        ProjectDeliverable.is_deleted == 0
    )
    result = await db.execute(query)
    deliverables = result.scalars().all()
    
    count = 0
    from sqlalchemy.orm.attributes import flag_modified
    for d in deliverables:
        if d.extra_metadata and "content" in d.extra_metadata:
            new_meta = dict(d.extra_metadata)
            del new_meta["content"]
            d.extra_metadata = new_meta
            flag_modified(d, "extra_metadata")
            count += 1
            
    if count > 0:
        await db.commit()
        
    return StandardResponse(message=f"已成功清理 {count} 个交付物的元数据内容")


@projects_router.get("/{project_id}/deliverables", response_model=StandardResponse)
async def list_project_deliverables(
    project_id: int,
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1),
    id: Optional[int] = Query(None),
    name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取项目交付物列表"""
    # 验证项目归属
    project_query = select(Project).where(
        Project.id == project_id, 
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    )
    project_result = await db.execute(project_query)
    if not project_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="项目未找到或无权访问")
        
    filters = [ProjectDeliverable.project_id == project_id, ProjectDeliverable.is_deleted == 0]
    if id:
        filters.append(ProjectDeliverable.id == id)
    if name:
        filters.append(ProjectDeliverable.name.contains(name))
    if status:
        filters.append(ProjectDeliverable.status == status)
    
    # 查询总数
    total_query = select(func.count()).select_from(ProjectDeliverable).where(*filters)
    total_result = await db.execute(total_query)
    total = total_result.scalar()
    
    # 构建查询，同时预加载内容详情
    from sqlalchemy.orm import selectinload
    query = select(ProjectDeliverable).where(*filters).options(selectinload(ProjectDeliverable.content_detail)).order_by(desc(ProjectDeliverable.created_at))
    
    # 如果提供了分页参数，则进行分页
    if page is not None and page_size is not None:
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
    
    result = await db.execute(query)
    deliverables = result.scalars().all()
    
    data = ProjectDeliverableListResponse(
        total=total,
        items=[d.to_dict() for d in deliverables],
        page=page or 1,
        page_size=page_size or total
    )
    return StandardResponse(data=data.model_dump())

@projects_router.post("/{project_id}/deliverables", response_model=StandardResponse)
async def create_project_deliverable(
    project_id: int,
    del_in: ProjectDeliverableCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """新增项目交付物"""
    # 验证项目归属
    project_query = select(Project).where(
        Project.id == project_id, 
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    )
    project_result = await db.execute(project_query)
    if not project_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="项目未找到或无权访问")
        
    # 检查是否已存在同名交付物
    existing_query = select(ProjectDeliverable).where(
        ProjectDeliverable.project_id == project_id,
        ProjectDeliverable.name == del_in.name,
        ProjectDeliverable.is_deleted == 0
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"交付物 '{del_in.name}' 已存在")
        
    deliverable = ProjectDeliverable(
        project_id=project_id,
        name=del_in.name,
        quantity=del_in.quantity,
        word_count=del_in.word_count,
        status=del_in.status,
        extra_metadata={} # 显式初始化为空字典，确保没有 content
    )
    db.add(deliverable)
    await db.commit()
    await db.refresh(deliverable)
    
    return StandardResponse(data=deliverable.to_dict())

@projects_router.get("/{project_id}/deliverables/{del_id}/content", response_model=StandardResponse)
async def get_deliverable_content(
    project_id: int,
    del_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取交付物正文内容（独立接口）"""
    # 验证项目归属
    project_query = select(Project).where(
        Project.id == project_id, 
        Project.is_deleted == 0,
        Project.user_id == current_user.user_id
    )
    project_result = await db.execute(project_query)
    if not project_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="项目未找到或无权访问")
        
    query = select(ProjectDeliverableContent).where(
        ProjectDeliverableContent.deliverable_id == del_id
    )
    result = await db.execute(query)
    content_obj = result.scalar_one_or_none()
    
    return StandardResponse(data={
        "deliverable_id": del_id,
        "content": content_obj.content if content_obj else ""
    })

@projects_router.put("/{project_id}/deliverables/{del_id}", response_model=StandardResponse)
async def update_project_deliverable(
    project_id: int,
    del_id: int,
    del_in: ProjectDeliverableUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新项目交付物"""
    logger.info(f"Updating deliverable {del_id} for project {project_id}")
    # 查询交付物，同时预加载内容
    from sqlalchemy.orm import selectinload
    query = select(ProjectDeliverable).where(
        ProjectDeliverable.id == del_id, 
        ProjectDeliverable.project_id == project_id,
        ProjectDeliverable.is_deleted == 0
    ).options(selectinload(ProjectDeliverable.content_detail))
    result = await db.execute(query)
    deliverable = result.scalar_one_or_none()
    
    if not deliverable:
        raise HTTPException(status_code=404, detail="交付物未找到")
    
    update_data = del_in.model_dump(exclude_unset=True)
    logger.info(f"[Deliverable Update] Updating deliverable {del_id}. Request body fields: {list(update_data.keys())}")
    
    if "content" in update_data:
        content_val = update_data["content"]
        logger.info(f"[Deliverable Update] Received content field, length: {len(content_val) if content_val else 0}")
        
        if deliverable.content_detail and deliverable.content_detail.content:
            current_content = deliverable.content_detail.content
            current_len = len(current_content)
            new_len = len(content_val) if content_val else 0
            
            # 严重 BUG 修复：防止前端旧数据覆盖后端 AI 刚生成的新内容
            # 强化保护逻辑：如果数据库内容明显长于前端内容，且更新时间非常近（3分钟内），拒绝覆盖
            time_diff = (utc_now() - ensure_utc(deliverable.content_detail.updated_at)).total_seconds()
            
            # 场景 A: 长度大幅缩水保护 (缩水 > 100 字符)
            is_length_loss = current_len > new_len + 100
            # 场景 B: 任何形式的缩水且时间极短 (30秒内)
            is_recent_clash = current_len > new_len and time_diff < 30
            
            if (is_length_loss and time_diff < 180) or is_recent_clash:
                logger.warning(f"[Deliverable Update] Race condition detected! DB content length {current_len}, Frontend length {new_len}, Time diff {time_diff:.1f}s.")
                logger.warning(f"[Deliverable Update] Refusing to overwrite newer/longer DB content with potentially stale frontend data.")
                # 从更新数据中移除 content，防止被后面的循环覆盖
                del update_data["content"]
            elif content_val:
                logger.info(f"[Deliverable Update] Content update accepted. Length: {new_len}")
        elif content_val:
            logger.info(f"[Deliverable Update] Initial content save. Length: {len(content_val)}")
    
    if "metadata" in update_data:
        meta_val = update_data["metadata"]
        if isinstance(meta_val, dict) and "outline" in meta_val:
            outline = meta_val["outline"]
            sections_with_content = [s.get("title") for s in outline if s.get("contentLength", 0) > 0]
            logger.info(f"[Deliverable Update] Received outline with {len(outline)} sections. Sections with content: {sections_with_content}")

    for field, value in update_data.items():
        if field == "content":
            # 处理内容分表逻辑
            if not deliverable.content_detail:
                deliverable.content_detail = ProjectDeliverableContent(
                    deliverable_id=deliverable.id,
                    content=value
                )
                db.add(deliverable.content_detail)
            else:
                deliverable.content_detail.content = value
                deliverable.content_detail.updated_at = utc_now()
            
            # 确保 extra_metadata 中不包含 content
            if deliverable.extra_metadata and "content" in deliverable.extra_metadata:
                new_meta = dict(deliverable.extra_metadata)
                del new_meta["content"]
                deliverable.extra_metadata = new_meta
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(deliverable, "extra_metadata")
            continue

        # 映射 field 到模型属性
        attr_name = "extra_metadata" if field == "metadata" else field
        
        if not hasattr(deliverable, attr_name):
            continue
            
        # 如果更新的是 metadata，确保不包含 content 字段
        if field == "metadata" and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k != "content"}
            
        setattr(deliverable, attr_name, value)
            
    # 自动更新数据库中的状态列，确保 SQL 统计（如项目进度）准确
    if deliverable.is_exportable:
        # 如果有内容但状态是"未撰写"，自动升级为"已撰写"
        if deliverable.status == "未撰写":
            deliverable.status = "已撰写"
    else:
        # 如果没内容但状态是"已撰写"，回退为"未撰写"
        if deliverable.status == "已撰写":
            deliverable.status = "未撰写"
            
    deliverable.updated_at = utc_now()
    try:
        await db.commit()
        logger.info(f"Successfully committed update for deliverable {del_id}")
    except Exception as e:
        logger.error(f"Failed to commit update for deliverable {del_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database commit failed: {str(e)}")
        
    await db.refresh(deliverable)
    
    return StandardResponse(data=deliverable.to_dict())

@projects_router.delete("/{project_id}/deliverables/{del_id}", response_model=StandardResponse)
async def delete_project_deliverable(
    project_id: int,
    del_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除项目交付物（软删除）"""
    query = select(ProjectDeliverable).where(
        ProjectDeliverable.id == del_id, 
        ProjectDeliverable.project_id == project_id,
        ProjectDeliverable.is_deleted == 0
    )
    result = await db.execute(query)
    deliverable = result.scalar_one_or_none()
    
    if not deliverable:
        raise HTTPException(status_code=404, detail="交付物未找到")
    
    deliverable.is_deleted = 1
    deliverable.deleted_at = utc_now()
    await db.commit()
    
    return StandardResponse(message="交付物已成功删除")

@projects_router.post("/{project_id}/deliverables/extract", response_model=StandardResponse)
async def extract_project_deliverables(
    project_id: int,
    req: ProjectDeliverableExtractRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """从文件中提取交付物清单"""
    # 1. 查询知识库获取内容
    query_text = "请列出该文档中提到的所有交付物清单及其对应的数量。"
    try:
        from src.models import select_model
        
        # 使用之前增强的 aquery，支持 file_ids 过滤
        search_results = await knowledge_base.aquery(
            query_text, 
            req.db_id, 
            file_ids=[req.file_id],
            top_k=20
        )
        
        if not search_results:
            return StandardResponse(code=200, message="当前文档中无交付物清单信息", data={"items": [], "existing": []})
            
        # 合并所有检索到的内容
        full_content = "\n".join([res["content"] for res in search_results])
        
        # 2. 使用 LLM 解析交付物
        # 从配置中读取默认模型
        model_spec = config.default_model
        try:
            llm = select_model(model_spec=model_spec)
        except Exception as e:
            logger.error(f"加载 LLM {model_spec} 失败，回退到默认模型: {e}")
            llm = select_model()
            
        prompt = f"""你是一个专业的项目经理，负责从文档中准确提取交付物清单。

交付物通常出现在“主要交付物”、“交付物清单”、“成果产出”等章节中。

提取要求：
1. **精准提取**：仅提取文档中明确列出的“项目主要交付物”或“成果物”。忽略背景介绍、过程记录等非正式交付物。
2. **清洗名称**：提取的交付物名称必须去除两端的符号，包括但不限于书名号（《 》）、双引号（" "）、单引号（' '）、括号（( )、（ ））等。
3. **解析数量**：提取对应的数量。如果文档中是以“1份”、“两份”等形式描述，请转换为纯数字。如果没有明确提到数量，请默认为 1。
4. **输出格式**：仅输出结果，格式为：交付物名称:数量
5. **禁令**：不要包含任何序号（如 1., (1)）、不要包含任何解释性文字、不要使用 Markdown 表格。

文档内容：
{full_content}

提取清单："""

        # 调用大模型生成结果
        import asyncio
        response = await asyncio.to_thread(llm.call, prompt)
        llm_result = response.content if response else ""
        logger.info(f"[DEBUG] LLM 返回结果: {llm_result}")
        
        if not llm_result:
            return StandardResponse(code=200, message="模型未返回有效提取信息", data={"items": [], "existing": []})

        extracted_items = []
        existing_items = []
        
        # 解析模型返回的格式（交付物名称:数量）
        lines = llm_result.strip().split('\n')
        for line_idx, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
                
            logger.debug(f"[DEBUG] 正在处理第 {line_idx + 1} 行: {line}")
            
            # 处理中英文冒号
            line_clean = line.replace('：', ':')
            if ':' not in line_clean:
                # 如果没有冒号，尝试整行作为名称，数量默认为 1
                name = line_clean
                quantity = 1
            else:
                parts = line_clean.split(':', 1)
                name = parts[0].strip()
                qty_str = parts[1].strip()
                
                # 尝试解析数量
                try:
                    # 提取数字（处理可能带有的单位，如 "2个" -> 2）
                    qty_match = re.search(r'\d+', qty_str)
                    quantity = int(qty_match.group()) if qty_match else 1
                except (ValueError, AttributeError):
                    quantity = 1
            
            # 进一步清洗名称：移除序号和两端符号
            name = re.sub(r'^\d+[\.\s、]+', '', name) # 移除开头的序号
            name = re.sub(r'^[（\(]\d+[）\)]', '', name) # 移除 (1) 或 （1） 形式的序号
            name = name.strip('《》"\'()（） ') # 移除两端特定符号
            
            if not name or len(name) < 2 or name == "交付物名称" or "过程材料" in name:
                continue
                
            # 尝试解析数量
            try:
                # 提取数字（处理可能带有的单位，如 "2个" -> 2）
                qty_match = re.search(r'\d+', qty_str)
                quantity = int(qty_match.group()) if qty_match else 1
            except (ValueError, AttributeError):
                quantity = 1
            
            # 3. 去重校验：查询 project_deliverables 表
            existing_query = select(ProjectDeliverable).where(
                ProjectDeliverable.project_id == project_id,
                ProjectDeliverable.name == name,
                ProjectDeliverable.is_deleted == 0
            )
            existing_result = await db.execute(existing_query)
            existing_obj = existing_result.scalar_one_or_none()
            
            if existing_obj:
                logger.info(f"[DEBUG] 交付物已存在，跳过: {name}")
                if name not in existing_items:
                    existing_items.append(name)
                continue
            
            # 4. 存储新交付物
            # 预估字数：根据交付物类型给予一个合理的默认值
            estimated_word_count = 5000
            if any(kw in name for kw in ["建议书", "报告", "方案", "规划"]):
                estimated_word_count = 10000
            elif any(kw in name for kw in ["简报", "纪要", "说明"]):
                estimated_word_count = 2000
            
            logger.info(f"[DEBUG] 准备插入数据库: {name}, 数量: {quantity}")
            deliverable = ProjectDeliverable(
                project_id=project_id,
                name=name,
                quantity=quantity,
                word_count=estimated_word_count,
                status="未撰写"
            )
            db.add(deliverable)
            extracted_items.append({
                "name": name, 
                "quantity": quantity, 
                "word_count": estimated_word_count,
                "status": "未撰写"
            })
            
        if extracted_items:
            try:
                await db.commit()
                logger.info(f"项目 {project_id} 从文件 {req.file_id} 中通过 LLM 成功提取并存入 {len(extracted_items)} 个交付物")
            except Exception as commit_err:
                await db.rollback()
                logger.error(f"数据库提交失败: {commit_err}")
                raise commit_err
        else:
            logger.info(f"项目 {project_id} 从文件 {req.file_id} 中 LLM 未匹配到任何新交付物（可能已存在或格式错误）")
            
        return StandardResponse(data={"items": extracted_items, "existing": existing_items})
        
    except Exception as e:
        logger.error(f"提取交付物失败: {e}, {traceback.format_exc()}")
        return StandardResponse(code=500, message=f"提取交付物失败: {str(e)}")

@projects_router.get("/{project_id}/deliverables/{del_id}/export", response_class=StreamingResponse)
async def export_deliverable(
    project_id: int,
    del_id: int,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db)
):
    """导出交付物为 Word 文档"""
    # 1. 获取交付物基本信息
    from sqlalchemy.orm import selectinload
    query = select(ProjectDeliverable).where(
        ProjectDeliverable.id == del_id,
        ProjectDeliverable.project_id == project_id,
        ProjectDeliverable.is_deleted == 0
    ).options(selectinload(ProjectDeliverable.content_detail))
    result = await db.execute(query)
    deliverable = result.scalar_one_or_none()
    
    if not deliverable:
        raise HTTPException(status_code=404, detail="交付物未找到")
        
    if not deliverable.is_exportable:
        raise HTTPException(status_code=400, detail="交付物尚未撰写完成，无法导出")
        
    # 2. 获取交付物内容
    content = ""
    source = "NONE"
    
    # 调试信息
    has_content_detail = deliverable.content_detail is not None
    content_detail_val = deliverable.content_detail.content if has_content_detail else None
    has_outline = deliverable.extra_metadata and "outline" in deliverable.extra_metadata
    outline_len = len(deliverable.extra_metadata["outline"]) if has_outline and isinstance(deliverable.extra_metadata["outline"], list) else 0

    logger.info(f"导出诊断 [ID:{del_id}]: has_detail={has_content_detail}, detail_len={len(content_detail_val) if content_detail_val else 0}, has_outline={has_outline}, outline_count={outline_len}")

    if content_detail_val and content_detail_val.strip():
        content = content_detail_val
        source = "content_detail"
    
    # 如果 content_detail 为空，或者内容明显不完整（例如只有几行，而大纲有很多项），尝试从 outline 恢复
    # 或者如果 detail 的内容长度远小于大纲项合并后的长度
    outline_combined = ""
    if has_outline and isinstance(deliverable.extra_metadata["outline"], list):
        outline_parts = []
        for item in deliverable.extra_metadata["outline"]:
            t = item.get('title', '')
            c = item.get('content', '')
            if t or c:
                outline_parts.append(f"## {t}\n\n{c}")
        outline_combined = "\n\n".join(outline_parts)

    if not content.strip() or (outline_combined and len(outline_combined) > len(content) * 1.5):
        if outline_combined:
            content = outline_combined
            source = f"extra_metadata.outline (restored, prev_source={source})"
    
    logger.info(f"最终导出决策: 来源={source}, 最终长度={len(content)}")
    
    if not content or not content.strip():
        raise HTTPException(status_code=404, detail="交付物内容为空，请先保存内容再导出")
        
    # 3. 转换为 Word
    try:
        import asyncio
        # 在线程池中执行耗时的转换操作
        doc_stream = await asyncio.to_thread(
            export_markdown_to_docx, 
            content, 
            deliverable.name
        )
        
        # 4. 返回流式响应
        filename = f"{deliverable.name}.docx"
        # 对文件名进行 URL 编码以支持中文
        encoded_filename = quote(filename)
        
        headers = {
            'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}",
            'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        }
        
        return StreamingResponse(doc_stream, headers=headers)
        
    except Exception as e:
        logger.error(f"导出交付物失败: {str(e)}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")
