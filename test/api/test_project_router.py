"""
Integration tests for project management API routes.
"""

from __future__ import annotations

import uuid
import pytest
from src.utils.datetime_utils import utc_isoformat, utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

async def test_create_project(test_client, admin_headers):
    """测试创建项目"""
    project_name = f"Test Project {uuid.uuid4().hex[:6]}"
    payload = {
        "name": project_name,
        "description": "This is a test project created by pytest.",
        "start_date": utc_isoformat(utc_now()),
        "status": "待启动",
        "progress": 0
    }
    
    response = await test_client.post("/api/projects", json=payload, headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "success"
    assert data["data"]["name"] == project_name
    assert data["data"]["description"] == payload["description"]
    return data["data"]["id"]

async def test_list_projects(test_client, admin_headers):
    """测试获取项目列表"""
    response = await test_client.get("/api/projects", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "success"
    assert "items" in data["data"]
    assert "total" in data["data"]
    assert isinstance(data["data"]["items"], list)

async def test_get_project_detail(test_client, admin_headers):
    """测试获取项目详情"""
    # 先创建一个项目
    project_name = f"Detail Test {uuid.uuid4().hex[:6]}"
    create_payload = {"name": project_name, "description": "Detail test"}
    create_res = await test_client.post("/api/projects", json=create_payload, headers=admin_headers)
    project_id = create_res.json()["data"]["id"]
    
    # 获取详情
    response = await test_client.get(f"/api/projects/{project_id}", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["id"] == project_id
    assert data["data"]["name"] == project_name

async def test_update_project(test_client, admin_headers):
    """测试更新项目"""
    # 先创建一个项目
    create_res = await test_client.post("/api/projects", json={"name": "Before Update"}, headers=admin_headers)
    project_id = create_res.json()["data"]["id"]
    
    # 更新项目
    update_payload = {
        "name": "After Update",
        "description": "Updated description",
        "status": "进行中",
        "progress": 50
    }
    response = await test_client.put(f"/api/projects/{project_id}", json=update_payload, headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["name"] == "After Update"
    assert data["data"]["status"] == "进行中"
    assert data["data"]["progress"] == 50

async def test_search_projects(test_client, admin_headers):
    """测试搜索项目"""
    unique_str = uuid.uuid4().hex[:8]
    project_name = f"SearchMe_{unique_str}"
    await test_client.post("/api/projects", json={"name": project_name}, headers=admin_headers)
    
    # 搜索
    response = await test_client.get(f"/api/projects/search?name={unique_str}", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]["items"]) >= 1
    assert any(unique_str in p["name"] for p in data["data"]["items"])

async def test_delete_project(test_client, admin_headers):
    """测试删除项目（软删除）"""
    # 先创建一个项目
    create_res = await test_client.post("/api/projects", json={"name": "To Be Deleted"}, headers=admin_headers)
    project_id = create_res.json()["data"]["id"]
    
    # 删除项目
    response = await test_client.delete(f"/api/projects/{project_id}", headers=admin_headers)
    assert response.status_code == 200
    assert "成功删除" in response.json()["message"]
    
    # 验证列表不再包含该项目
    list_res = await test_client.get("/api/projects", headers=admin_headers)
    projects = list_res.json()["data"]["items"]
    assert not any(p["id"] == project_id for p in projects)
    
    # 验证直接获取返回404
    detail_res = await test_client.get(f"/api/projects/{project_id}", headers=admin_headers)
    assert detail_res.status_code == 404
