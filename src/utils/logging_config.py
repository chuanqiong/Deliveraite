import os
import sys
import json
from loguru import logger as loguru_logger
from src.utils.datetime_utils import shanghai_now
from src.utils.context_vars import trace_id_var
from src.version import VERSION, ENVIRONMENT

SAVE_DIR = os.getenv("SAVE_DIR") or "saves"
DATETIME = shanghai_now().strftime("%Y-%m-%d")
LOG_FILE = f"{SAVE_DIR}/logs/yuxi-{DATETIME}.log"
JSON_LOG_FILE = f"{SAVE_DIR}/logs/yuxi-{DATETIME}.json.log"

def serialize(record):
    """自定义序列化函数，生成结构化 JSON 日志"""
    subset = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "file": record["file"].name,
        "line": record["line"],
        "trace_id": record["extra"].get("trace_id", ""),
        "env": ENVIRONMENT,
        "version": VERSION
    }
    if record["extra"]:
        # 将除了 trace_id 以外的其他 extra 字段放入 extra 节点
        extra_data = {k: v for k, v in record["extra"].items() if k != "trace_id"}
        if extra_data:
            subset["extra"] = extra_data
    return json.dumps(subset, ensure_ascii=False)

def setup_logger(name, level="DEBUG", console=True):
    """使用 loguru 设置日志记录器"""
    os.makedirs(f"{SAVE_DIR}/logs", exist_ok=True)  # 创建日志目录

    # 移除默认的 handler
    loguru_logger.remove()

    # 1. 添加普通文件日志（无颜色，启用异步写入）
    loguru_logger.add(
        LOG_FILE,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} - {level} - [{extra[trace_id]}] - {file}:{line} - {message}",
        encoding="utf-8",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )

    # 2. 添加 JSON 结构化日志
    loguru_logger.add(
        JSON_LOG_FILE,
        level=level,
        format="{message}",
        serialize=serialize,  # 使用自定义序列化函数
        encoding="utf-8",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        enqueue=True,
    )

    # 3. 添加控制台日志（有颜色）
    if console:
        loguru_logger.add(
            lambda msg: print(msg, end=""),
            level=level,
            format=(
                "<green>{time:MM-DD HH:mm:ss}</green> "
                "<level>{level}</level> "
                "<magenta>[{extra[trace_id]}]</magenta> "
                "<cyan>{file}:{line}</cyan>: "
                "<level>{message}</level>"
            ),
            colorize=True,
        )

    # 使用 patch 动态注入 trace_id
    patched_logger = loguru_logger.patch(lambda record: record["extra"].update(trace_id=trace_id_var.get()))
    return patched_logger


# 设置根日志记录器
logger = setup_logger("Yuxi")

__all__ = ["logger"]

# If you want to disable logging from external libraries
# logging.getLogger('some_external_library').setLevel(logging.CRITICAL)
