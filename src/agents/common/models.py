import os
import traceback
import httpx

from langchain.chat_models import BaseChatModel, init_chat_model
from pydantic import SecretStr

from src import config
from src.utils import get_docker_safe_url
from src.utils.logging_config import logger


# 创建全局的 httpx 客户端，禁用 http2 以避免 "incomplete chunked read" 错误
# 并设置较长的超时时间。同时提供同步和异步客户端以适配不同的调用场景。
_shared_async_client = httpx.AsyncClient(
    http2=False,
    timeout=httpx.Timeout(60.0, connect=10.0, read=300.0),
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
)

_shared_sync_client = httpx.Client(
    http2=False,
    timeout=httpx.Timeout(60.0, connect=10.0, read=300.0),
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
)


def load_chat_model(fully_specified_name: str, use_fallback: bool = True, **kwargs) -> BaseChatModel:
    """
    Load a chat model from a fully specified name.
    """
    provider, model = fully_specified_name.split("/", maxsplit=1)

    assert provider != "custom", "[弃用] 自定义模型已移除，请在 src/config/static/models.py 中配置"

    model_info = config.model_names.get(provider)
    if not model_info:
        raise ValueError(f"Unknown model provider: {provider}")

    env_var = model_info.env

    api_key = os.getenv(env_var) or env_var

    base_url = get_docker_safe_url(model_info.base_url)

    # 注入全局 client 以解决网络连接不稳定的问题
    if "http_client" not in kwargs:
        kwargs["http_client"] = _shared_sync_client
    if "http_async_client" not in kwargs:
        kwargs["http_async_client"] = _shared_async_client

    # 提取并处理 enable_thinking 和 enable_search
    # 这两个参数不能直接传递给 init_chat_model 或 ChatOpenAI 的构造函数
    enable_thinking = kwargs.pop("enable_thinking", False)
    enable_search = kwargs.pop("enable_search", False)
    
    extra_body = kwargs.pop("extra_body", {})
    if enable_thinking:
        extra_body["enable_thinking"] = True
    if enable_search:
        extra_body["enable_search"] = True

    chat_model = None
    if provider in ["openai", "deepseek"]:
        model_spec = f"{provider}:{model}"
        # 增加默认重试次数
        if "max_retries" not in kwargs:
            kwargs["max_retries"] = 3
        
        # 如果有 extra_body，则注入到 kwargs 中
        if extra_body:
            kwargs["extra_body"] = extra_body
            
        logger.debug(f"[offical] Loading model {model_spec} with kwargs {kwargs}")
        chat_model = init_chat_model(model_spec, **kwargs)

    elif provider in ["dashscope"]:
        from langchain_deepseek import ChatDeepSeek

        # 提取模型参数，设置默认值
        model_kwargs = {}

        # temperature 参数
        if "temperature" in kwargs:
            model_kwargs["temperature"] = kwargs["temperature"]

        # top_p 参数（注意：API 中可能叫 top_p 或 topP）
        if "top_p" in kwargs:
            model_kwargs["top_p"] = kwargs["top_p"]
        elif "top_N" in kwargs:
            # 支持别名 top_N（根据用户需求）
            model_kwargs["top_p"] = kwargs["top_N"]

        # max_tokens 参数
        if "max_tokens" in kwargs:
            model_kwargs["max_tokens"] = kwargs["max_tokens"]
        elif "max_output_tokens" in kwargs:
            model_kwargs["max_tokens"] = kwargs["max_output_tokens"]

        # 打印模型参数配置（便于调试）
        logger.info(
            f"Loading dashscope model '{model}' with parameters:\n"
            f"  - temperature: {model_kwargs.get('temperature', 'default')}\n"
            f"  - top_p: {model_kwargs.get('top_p', 'default')}\n"
            f"  - enable_thinking: {enable_thinking}\n"
            f"  - enable_search: {enable_search}\n"
            f"  - extra_body: {extra_body if extra_body else 'None'}"
        )

        chat_model = ChatDeepSeek(
            model=model,
            api_key=SecretStr(api_key),
            base_url=base_url,
            api_base=base_url,
            stream_usage=True,
            max_retries=kwargs.get("max_retries", 3),
            http_client=kwargs.get("http_client"),
            http_async_client=kwargs.get("http_async_client"),
            # 传递模型参数
            **model_kwargs,
            # 传递额外参数（用于 enable_thinking 和 enable_search）
            **({"extra_body": extra_body} if extra_body else {}),
        )

    else:
        try:  # 其他模型，默认使用OpenAIBase, like openai, zhipuai
            from langchain_openai import ChatOpenAI

            # 提取模型参数
            model_kwargs = {
                "max_retries": kwargs.get("max_retries", 3),
                "http_client": kwargs.get("http_client"),
                "http_async_client": kwargs.get("http_async_client"),
            }
            if "temperature" in kwargs:
                model_kwargs["temperature"] = kwargs["temperature"]
            if "top_p" in kwargs:
                model_kwargs["top_p"] = kwargs["top_p"]
            if "max_tokens" in kwargs:
                model_kwargs["max_tokens"] = kwargs["max_tokens"]
            elif "max_output_tokens" in kwargs:
                model_kwargs["max_tokens"] = kwargs["max_output_tokens"]
            
            if extra_body:
                model_kwargs["extra_body"] = extra_body

            chat_model = ChatOpenAI(
                model=model,
                api_key=SecretStr(api_key),
                base_url=base_url,
                stream_usage=True,
                **model_kwargs
            )
        except Exception as e:
            raise ValueError(f"Model provider {provider} load failed, {e} \n {traceback.format_exc()}")

    # 注入 Fallback 机制
    if use_fallback and chat_model:
        # 默认回退到 fast_model (通常是一个稳定且快速的模型)
        fallback_name = config.fast_model
        if fallback_name and fully_specified_name != fallback_name:
            try:
                fallback_model = load_chat_model(fallback_name, use_fallback=False, **kwargs)
                logger.info(f"Adding fallback for {fully_specified_name} -> {fallback_name}")
                return chat_model.with_fallbacks([fallback_model])
            except Exception as e:
                logger.warning(f"Failed to load fallback model {fallback_name}: {e}")

    return chat_model
