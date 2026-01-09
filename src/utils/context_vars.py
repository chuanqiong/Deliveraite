from contextvars import ContextVar

# 定义 ContextVar 用于存储 trace_id
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

def get_trace_id() -> str:
    """获取当前请求的 trace_id"""
    return trace_id_var.get()
