import json
import re
from collections.abc import Callable
from typing import Any

import json_repair
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from src.utils import logger

from langchain_core.messages import AIMessage

class RobustPatchToolCallsMiddleware(AgentMiddleware):
    """鲁棒的工具调用修复中间件"""

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        try:
            response = await handler(request)
        except Exception as e:
            # 如果是 Pydantic 验证错误，尝试从错误信息中恢复（虽然这很难，但可以尝试记录更多信息）
            logger.error(f"Model call failed with error: {e}")
            raise e
        
        # 检查响应中是否有 result
        if not hasattr(response, "result") or not response.result:
            return response
            
        new_results = []
        for msg in response.result:
            if not isinstance(msg, AIMessage):
                new_results.append(msg)
                continue
                
            # 准备新的工具调用列表
            fixed_tool_calls = []
            fixed_invalid_calls = []
            
            # 1. 处理已有的 valid tool_calls
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    # 某些情况下 tc 可能是 dict 也可能是 ToolCall 对象
                    tc_dict = tc if isinstance(tc, dict) else dict(tc)
                    args = tc_dict.get("args")
                    
                    if isinstance(args, str):
                        patched_args = self._patch_json_args(args)
                        try:
                            args_dict = json.loads(patched_args)
                            # 增加对 args_dict 类型的校验，确保是 dict
                            if isinstance(args_dict, dict):
                                tc_dict["args"] = self._coerce_tool_args(tc_dict.get("name"), args_dict)
                                fixed_tool_calls.append(tc_dict)
                            else:
                                # 如果不是 dict，尝试看是否是 list 且第一个元素是 dict
                                if isinstance(args_dict, list) and len(args_dict) > 0 and isinstance(args_dict[0], dict):
                                    tc_dict["args"] = self._coerce_tool_args(tc_dict.get("name"), args_dict[0])
                                    fixed_tool_calls.append(tc_dict)
                                else:
                                    raise ValueError(f"Args is not a dict: {type(args_dict)}")
                        except Exception as e:
                            # 解析失败，退化为 invalid_tool_call
                            logger.warning(f"Failed to parse or validate args for tool {tc_dict.get('name')}: {e}")
                            itc = {
                                "name": tc_dict.get("name"),
                                "args": args,
                                "id": tc_dict.get("id"),
                                "error": f"Failed to parse args: {e}"
                            }
                            fixed_invalid_calls.append(itc)
                    else:
                        tc_dict["args"] = self._coerce_tool_args(tc_dict.get("name"), args)
                        fixed_tool_calls.append(tc_dict)
            
            # 2. 处理 invalid_tool_calls
            if hasattr(msg, "invalid_tool_calls") and msg.invalid_tool_calls:
                for itc in msg.invalid_tool_calls:
                    itc_dict = itc if isinstance(itc, dict) else dict(itc)
                    args = itc_dict.get("args")
                    
                    if isinstance(args, str):
                        patched_args = self._patch_json_args(args)
                        try:
                            parsed = json.loads(patched_args)
                            
                            # 递归解析，处理多重转义的字符串
                            max_depth = 5
                            while max_depth > 0:
                                if isinstance(parsed, str):
                                    try:
                                        # 如果字符串看起来像 JSON 对象或数组，尝试继续解析
                                        stripped = parsed.strip()
                                        if (stripped.startswith('{') and stripped.endswith('}')) or \
                                           (stripped.startswith('[') and stripped.endswith(']')):
                                            parsed = json.loads(stripped)
                                            max_depth -= 1
                                        else:
                                            break
                                    except:
                                        break
                                elif isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], str):
                                    # 处理类似 ["{...}"] 的情况
                                    parsed = parsed[0]
                                    max_depth -= 1
                                else:
                                    break
                                    
                            # 如果解析出来的是 list 且第一个元素是 dict，尝试提取
                            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                                parsed = parsed[0]

                            # 如果没有名称，尝试从参数中猜测（比如参数里有 sections 可能就是 batch_generate_sections）
                            name = itc_dict.get("name")
                            if not name or name == "":
                                if isinstance(parsed, dict):
                                    if "sections" in parsed:
                                        name = "batch_generate_sections"
                                    elif "section_id" in parsed:
                                        if "existing_content" in parsed:
                                            name = "update_section_content"
                                        else:
                                            name = "generate_section_content"
                                            
                            if not name:
                                logger.warning(f"Could not infer tool name for invalid tool call: {itc_dict}")
                                fixed_invalid_calls.append(itc_dict)
                                continue

                            # 最终校验：必须是 dict 才能放入 fixed_tool_calls
                            # 尝试进行类型强制转换
                            coerced_args = self._coerce_tool_args(name, parsed)
                            
                            if isinstance(coerced_args, dict):
                                valid_tc = {
                                    "name": name,
                                    "args": coerced_args,
                                    "id": itc_dict.get("id"),
                                    "type": "tool_call"
                                }
                                fixed_tool_calls.append(valid_tc)
                                logger.info(f"Successfully patched invalid tool call: {name}")
                            else:
                                logger.warning(f"Still not a dict after patching invalid tool call {name}: {type(coerced_args)}")
                                fixed_invalid_calls.append(itc_dict)
                        except Exception as e:
                            # 如果解析失败，但我们有工具名称，尝试直接对原始字符串进行强制转换
                            name = itc_dict.get("name")
                            if name:
                                # 使用经过初步清理的 patched_args 而不是原始 args
                                coerced_args = self._coerce_tool_args(name, patched_args)
                                if isinstance(coerced_args, dict):
                                    valid_tc = {
                                        "name": name,
                                        "args": coerced_args,
                                        "id": itc_dict.get("id"),
                                        "type": "tool_call"
                                    }
                                    fixed_tool_calls.append(valid_tc)
                                    logger.info(f"Successfully patched invalid tool call via fallback: {name}")
                                    continue
                                    
                            logger.warning(f"Still failed to parse invalid tool call {itc_dict.get('name')}: {e}")
                            fixed_invalid_calls.append(itc_dict)
                    else:
                        fixed_invalid_calls.append(itc_dict)
            
            # 3. 创建新的 AIMessage 替换旧的，避免 in-place 修改导致的验证问题
            new_msg = AIMessage(
                content=msg.content,
                additional_kwargs=msg.additional_kwargs,
                response_metadata=msg.response_metadata,
                tool_calls=fixed_tool_calls,
                invalid_tool_calls=fixed_invalid_calls,
                id=msg.id
            )
            new_results.append(new_msg)
            
        # 使用 dataclasses.replace 或直接实例化创建新的 response
        from dataclasses import replace
        return replace(response, result=new_results)

    def _coerce_tool_args(self, tool_name: str, args: Any) -> Any:
        """针对特定工具进行参数类型强制转换"""
        # 1. 针对 sequentialthinking 工具的特殊处理
        if tool_name == "sequentialthinking":
            # 如果 args 是字符串，尝试看看是否是损坏的 JSON 或者直接就是内容
            if isinstance(args, str):
                stripped = args.strip()
                # 尝试修复并解析字符串形式的 JSON
                if stripped.startswith('{'):
                    try:
                        repaired = json_repair.repair_json(stripped)
                        parsed = json.loads(repaired)
                        if isinstance(parsed, dict):
                            args = parsed
                    except Exception:
                        pass
                
                # 如果仍然是字符串，尝试正则表达式提取关键字段
                if isinstance(args, str):
                    extracted = {"thought": args}
                    # 提取整数地段
                    for field in ["thoughtNumber", "totalThoughts", "revisesThought", "branchFromThought"]:
                        # 匹配 "field": 123 或 "field": "123"
                        match = re.search(f'"{field}"\\s*:\\s*"?(\\d+)"?', args)
                        if match:
                            extracted[field] = int(match.group(1))
                    
                    # 提取布尔字段
                    for field in ["nextThoughtNeeded", "isRevision", "needsMoreThoughts"]:
                        match = re.search(f'"{field}"\\s*:\\s*(true|false)', args, re.I)
                        if match:
                            extracted[field] = match.group(1).lower() == "true"
                    
                    # 如果提取到了结构化字段，则使用提取的结果
                    if len(extracted) > 1:
                        # 尝试提取真正的 thought 内容（如果存在）
                        # 匹配 "thought": "..." 或 "thought": "被截断的...
                        thought_match = re.search(r'"thought"\s*:\s*"(.*?)(?:"\s*[,}]|(?<!\\)"\s*$|$)', args, re.S)
                        if thought_match:
                            thought_val = thought_match.group(1)
                            # 如果结尾有引号，去掉它
                            if thought_val.endswith('"') and not thought_val.endswith('\\"'):
                                thought_val = thought_val[:-1]
                            extracted["thought"] = thought_val.replace('\\"', '"').replace('\\\\', '\\')
                        args = extracted
                    else:
                        # 否则保持为 {"thought": args}
                        args = {"thought": args}
            
            # 如果 args 是 dict，确保字段类型正确
            if isinstance(args, dict):
                # 整数类型字段
                int_fields = ["thoughtNumber", "totalThoughts", "revisesThought", "branchFromThought"]
                for field in int_fields:
                    if field in args and args[field] is not None:
                        try:
                            if isinstance(args[field], str):
                                # 尝试从字符串中提取数字 (比如 "1/10" 提取 1)
                                match = re.search(r'\d+', args[field])
                                if match:
                                    args[field] = int(match.group())
                            else:
                                args[field] = int(args[field])
                        except (ValueError, TypeError):
                            logger.warning(f"Failed to coerce {field} to int for sequentialthinking: {args[field]}")

                # 布尔类型字段
                bool_fields = ["nextThoughtNeeded", "isRevision", "needsMoreThoughts"]
                for field in bool_fields:
                    if field in args and args[field] is not None:
                        if isinstance(args[field], str):
                            val = args[field].lower().strip()
                            if val in ("true", "yes", "1", "t", "y"):
                                args[field] = True
                            elif val in ("false", "no", "0", "f", "n"):
                                args[field] = False
                        else:
                            args[field] = bool(args[field])
        
        # 2. 针对 batch_generate_sections 等工具
        elif tool_name == "batch_generate_sections":
            if isinstance(args, list):
                args = {"sections": args}

        return args

    def _patch_json_args(self, args_str: str) -> str:
        """清理并修复 JSON 参数字符串，使用 json_repair 提高鲁棒性"""
        if not args_str:
            return args_str
            
        # 1. 移除 Markdown 代码块
        args_str = re.sub(r'```json\s*([\s\S]*?)\s*```', r'\1', args_str)
        args_str = re.sub(r'```\s*([\s\S]*?)\s*```', r'\1', args_str)
        
        args_str = args_str.strip()
        
        # 2. 处理双重转义/包裹在引号中的 JSON，或者被截断的包裹在引号中的 JSON
        if args_str.startswith('"') or args_str.startswith("'"):
            # 如果是完整的包裹在引号中
            if (args_str.startswith('"') and args_str.endswith('"')) or \
               (args_str.startswith("'") and args_str.endswith("'")):
                try:
                    # 尝试解开一层引号
                    unquoted = json.loads(args_str)
                    if isinstance(unquoted, str):
                        return self._patch_json_args(unquoted)
                    elif isinstance(unquoted, (dict, list)):
                        return json.dumps(unquoted)
                except Exception:
                    # 解开失败，尝试正则移除首尾引号并处理转义
                    stripped = args_str[1:-1].replace('\\"', '"').replace('\\\\', '\\')
                    if stripped.strip().startswith(('{', '[')):
                        args_str = stripped
            # 如果是被截断的包裹在引号中 (例如 "{\"thought\": ... )
            elif args_str.startswith('"{\\"') or args_str.startswith("'{\\'") or \
                 args_str.startswith('"[{\\"') or args_str.startswith("'[{\\'"):
                try:
                    # 尝试移除开头的引号并处理转义
                    # 注意：这里我们不加结尾引号，让 json_repair 去闭合它
                    stripped = args_str[1:].replace('\\"', '"').replace('\\\\', '\\')
                    if stripped.strip().startswith(('{', '[')):
                        args_str = stripped
                except Exception:
                    pass
        
        # 3. 使用 json_repair 尝试修复截断或损坏的 JSON
        try:
            # repair_json 可以处理截断的字符串并返回合法的 JSON 字符串
            repaired = json_repair.repair_json(args_str, ensure_ascii=False)
            if repaired:
                return repaired
        except Exception as e:
            logger.warning(f"json_repair failed: {e}")

        # 4. 退化方案：原有的手动修复逻辑
        args_str = re.sub(r',\s*([\]}])', r'\1', args_str)
        args_str = self._close_json_string(args_str)
        
        return args_str

    def _close_json_string(self, s: str) -> str:
        """尝试闭合被截断的 JSON 字符串"""
        s = s.strip()
        if not s:
            return s
            
        stack = []
        in_string = False
        escaped = False
        
        for i, char in enumerate(s):
            if char == '"' and not escaped:
                in_string = not in_string
            elif char == '\\' and in_string:
                escaped = not escaped
                continue
            
            if not in_string:
                if char == '{':
                    stack.append('}')
                elif char == '[':
                    stack.append(']')
                elif char in '} ]':
                    if stack and char == stack[-1]:
                        stack.pop()
            
            escaped = False
            
        # 如果还在字符串中，先闭合引号
        if in_string:
            s += '"'
            
        # 按照相反顺序闭合括号
        while stack:
            s += stack.pop()
            
        return s
