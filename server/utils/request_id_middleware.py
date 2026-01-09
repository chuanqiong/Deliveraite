import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from collections.abc import Callable
from src.utils.context_vars import trace_id_var

class RequestIDMiddleware(BaseHTTPMiddleware):
    """请求 ID 中间件 - 捕获或生成追踪 ID"""
    
    async def dispatch(self, request: Request, call_next: Callable):
        # 尝试从请求头获取 trace_id
        trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
        
        # 设置 ContextVar
        token = trace_id_var.set(trace_id)
        
        try:
            # 继续处理请求
            response = await call_next(request)
            # 在响应头中返回 trace_id，方便前端关联
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            # 重置 ContextVar
            trace_id_var.reset(token)
