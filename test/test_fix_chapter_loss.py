
import asyncio
import json
import logging
from unittest.mock import MagicMock, AsyncMock

# Mock SQLAlchemy models
class ProjectDeliverable:
    def __init__(self, id, extra_metadata=None):
        self.id = id
        self.extra_metadata = extra_metadata or {}
        self.status = "未撰写"
        self.content_detail = None
        self.project_id = 1

class ProjectDeliverableContent:
    def __init__(self, deliverable_id, content):
        self.deliverable_id = deliverable_id
        self.content = content
        self.updated_at = None

# The logic to test (extracted from chat_router.py)
def test_merging_logic(deliverable, section_updates):
    has_outline_update = False
    new_content = ""
    
    if section_updates and deliverable.extra_metadata and "outline" in deliverable.extra_metadata:
        outline = deliverable.extra_metadata["outline"]
        
        def _update_item(items, sid, cont):
            for item in items:
                if str(item.get("id")) == str(sid):
                    item["content"] = cont
                    return True
                if item.get("children") and _update_item(item["children"], sid, cont):
                    return True
            return False
        
        for sid, cont in section_updates:
            if _update_item(outline, sid, cont):
                has_outline_update = True
        
        if has_outline_update:
            # 从大纲重新生成完整内容
            def _get_all_content(items):
                parts = []
                for item in items:
                    if item.get("content"):
                        parts.append(item["content"])
                    if item.get("children"):
                        parts.append(_get_all_content(item["children"]))
                return "\n\n".join([p for p in parts if p])
            
            full_content_from_outline = _get_all_content(outline)
            if full_content_from_outline:
                new_content = full_content_from_outline
    
    return new_content, has_outline_update

def run_test():
    print("Running Chapter Loss Fix Verification...")
    
    # 1. Setup initial state with an outline
    initial_outline = [
        {"id": "1", "title": "Chapter 1", "content": ""},
        {"id": "2", "title": "Chapter 2", "content": "", "children": [
            {"id": "2.1", "title": "Sub 2.1", "content": ""}
        ]},
        {"id": "3", "title": "Chapter 3", "content": ""}
    ]
    deliverable = ProjectDeliverable(id=63, extra_metadata={"outline": initial_outline})
    
    # 2. Simulate first tool call: batch_generate_sections for 1 and 2.1
    updates_1 = [
        ("1", "Content for Chapter 1"),
        ("2.1", "Content for Sub 2.1")
    ]
    content_1, updated_1 = test_merging_logic(deliverable, updates_1)
    
    print(f"First update: updated={updated_1}, length={len(content_1)}")
    assert updated_1 == True
    assert "Content for Chapter 1" in content_1
    assert "Content for Sub 2.1" in content_1
    assert "Chapter 3" not in content_1 # No content yet
    
    # 3. Simulate second tool call: generate_section_content for 3
    # In the OLD logic, this would OVERWRITE chapters 1 and 2.1
    updates_2 = [
        ("3", "Content for Chapter 3")
    ]
    content_2, updated_2 = test_merging_logic(deliverable, updates_2)
    
    print(f"Second update: updated={updated_2}, length={len(content_2)}")
    assert updated_2 == True
    # The NEW logic should contain ALL chapters
    assert "Content for Chapter 1" in content_2
    assert "Content for Sub 2.1" in content_2
    assert "Content for Chapter 3" in content_2
    
    # 4. Verify nested structure update
    found_2_1 = False
    for item in deliverable.extra_metadata["outline"]:
        if item["id"] == "2":
            for child in item["children"]:
                if child["id"] == "2.1":
                    assert child["content"] == "Content for Sub 2.1"
                    found_2_1 = True
    assert found_2_1 == True

    print("Test PASSED: Incremental generation preserved all chapters.")

if __name__ == "__main__":
    run_test()
