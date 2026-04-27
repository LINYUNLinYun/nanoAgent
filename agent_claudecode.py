import os
import json
import re
import shlex
import subprocess
import sys
import glob as glob_module
import urllib.request
from html import unescape
from datetime import datetime
from pathlib import Path
from typing import Any
from openai import OpenAI
from dotenv import load_dotenv
from mcp_tool_loader import MCPToolLoader


# 改为读取本地环境变量，免去设置系统环境变量
load_dotenv()
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    base_url=os.environ.get("OPENAI_BASE_URL")
)

MEMORY_FILE = "agent_memory.md"
RULES_DIR = ".agent/rules"
SKILLS_DIR = ".agent/skills"
MCP_CONFIG = ".agent/mcp.json"

current_plan = []
plan_mode = False

base_tools = [
    {"type": "function", "function": {"name": "read", "description": "Read file with line numbers", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write", "description": "Write content to file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit", "description": "Replace string in file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {"name": "glob", "description": "Find files by pattern", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "grep", "description": "Search files for pattern", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "bash", "description": "Run shell command", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "get_https", "description": "Fetch content from an HTTPS URL (cleaned text by default)", "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "raw_html": {"type": "boolean"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "plan", "description": "Break down complex task into steps and execute sequentially", "parameters": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}}}
]

def read(path, offset=None, limit=None):
    """读取指定文件的某一段内容[offset, offset + limit)，并给每行加上行号显示。"""
    try:
        # windows下要加utf8 
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        start = offset if offset else 0
        end = (start + limit) if limit else len(lines)
        numbered = [f"{i+1:4d} {line}" for i, line in enumerate(lines[start:end], start)]
        return ''.join(numbered)
    except Exception as e:
        return f"Error: {str(e)}"

def write(path, content):
    """将内容写入指定文件。"""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error: {str(e)}"

def edit(path, old_string, new_string):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        # 只有这个字符串在文件中仅出现一次才进行替换
        if content.count(old_string) != 1:
            return f"Error: old_string must appear exactly once"
        new_content = content.replace(old_string, new_string)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return f"Successfully edited {path}"
    except Exception as e:
        return f"Error: {str(e)}"

def glob(pattern):
    """在当前目录及子目录中查找匹配pattern的文件，返回按修改时间排序的文件列表(按修改时间由新到旧)。
    pattern支持常见的glob语法，如*.txt、**/*.py等。
    """
    try:
        files = glob_module.glob(pattern, recursive=True)
        files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        return '\n'.join(files) if files else "No files found"
    except Exception as e:
        return f"Error: {str(e)}"

def grep(pattern, path="."):
    """在指定目录及子目录中查找匹配pattern的行，返回匹配结果。
    pattern支持正则表达式。
    """
    # nt：windows内核，统一grep风格输入和输出，但不改动posix分支行为
    if os.name == 'nt':
        # 解析命令行参数的函数，支持类似grep -nr pattern path的输入格式
        def parse_windows_grep_args(raw_pattern, raw_path):
            if raw_path != ".":
                return {
                    "pattern": raw_pattern,
                    "targets": [raw_path],
                    "line_number": True,
                    "recursive": True
                }

            command = raw_pattern.strip()
            if command.startswith("grep "):
                command = command[5:].strip()

            try:
                tokens = shlex.split(command, posix=False)
            except ValueError as e:
                return {"error": f"Error: Invalid grep arguments - {str(e)}"}

            show_line_number = False
            is_recursive = False
            positional = []

            index = 0
            while index < len(tokens):
                token = tokens[index]
                if token == "--":
                    positional.extend(tokens[index + 1:])
                    break
                if token.startswith("-") and token != "-":
                    for flag in token[1:]:
                        if flag == "n":
                            show_line_number = True
                        elif flag == "r":
                            is_recursive = True
                        else:
                            return {"error": f"Error: Unsupported grep option '-{flag}'"}
                else:
                    positional.append(token)
                index += 1

            if not positional:
                return {"error": "Error: Missing search pattern"}

            implicit_default_target = len(positional) == 1
            return {
                "pattern": positional[0],
                "targets": positional[1:] or ["."],
                "line_number": show_line_number,
                "recursive": is_recursive or implicit_default_target
            }
        # 对单文件和目录分别处理
        def iter_target_files(target, is_recursive):
            if os.path.isfile(target):
                return [target]
            if os.path.isdir(target):
                if not is_recursive:
                    raise IsADirectoryError(target)
                matched_files = []
                for root, _, files in os.walk(target):
                    for name in files:
                        matched_files.append(os.path.join(root, name))
                return matched_files
            raise FileNotFoundError(target)

        parsed = parse_windows_grep_args(pattern, path)
        if "error" in parsed:
            return parsed["error"]

        try:
            regex = re.compile(parsed["pattern"])
        except re.error as e:
            return f"Error: Invalid regex pattern - {str(e)}"

        try:
            all_files = []
            for target in parsed["targets"]:
                all_files.extend(iter_target_files(target, parsed["recursive"]))
        except FileNotFoundError as e:
            return f"Error: {str(e)}: No such file or directory"
        except IsADirectoryError as e:
            return f"Error: {str(e)}: Is a directory"

        show_filename = parsed["recursive"] or len(parsed["targets"]) > 1 or len(all_files) > 1
        results = []

        for filepath in all_files:
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        if not regex.search(line):
                            continue
                        prefixes = []
                        if show_filename:
                            prefixes.append(filepath)
                        if parsed["line_number"]:
                            prefixes.append(str(line_num))
                        content = line.rstrip('\r\n')
                        if prefixes:
                            results.append(f"{':'.join(prefixes)}:{content}")
                        else:
                            results.append(content)
            except Exception:
                pass

        return "\n".join(results) if results else "No matches found"
    elif os.name == 'posix':    
        try:
            result = subprocess.run(f"grep -rn '{pattern}' {path}", shell=True, capture_output=True, text=True, timeout=30)
            return result.stdout if result.stdout else "No matches found"
        except Exception as e:
            return f"Error: {str(e)}"
    else:
        return "Error: Unsupported OS for grep"

def bash(command):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        return result.stdout + result.stderr
    except Exception as e:
        return f"Error: {str(e)}"

def _extract_readable_text_from_html(html: str) -> str:
    # Remove non-content blocks first, then strip tags and normalize whitespace.
    cleaned = re.sub(r"(?is)<!--.*?-->", " ", html)
    cleaned = re.sub(r"(?is)<(script|style|svg|noscript|template)[^>]*>.*?</\1>", " ", cleaned)
    cleaned = re.sub(r"(?is)<\s*br\s*/?\s*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</\s*(p|div|li|tr|section|article|h[1-6])\s*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in cleaned.split("\n")]
    lines = [line for line in lines if line]
    normalized = "\n".join(lines)
    return re.sub(r"[ \t]+", " ", normalized)

def get_https(url, raw_html=False):
    if not isinstance(url, str) or not url.startswith("https://"):
        return "Error: URL must start with https://"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "agent-claudecode/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            content_type = response.headers.get("Content-Type", "").lower()
        decoded = data.decode(charset, errors="replace")
        if raw_html:
            return decoded
        if "text/html" in content_type:
            return _extract_readable_text_from_html(decoded)
        return decoded
    except Exception as e:
        return f"Error: {str(e)}"

def plan(task):
    global current_plan, plan_mode
    if plan_mode:
        return "Error: Cannot plan within a plan"
    print(f"[Plan] Breaking down: {task}")
    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": "Break the task into 1-5 agent execution steps. Each step must be a plain string, not an object. Prefer one step when a single tool call can complete the task. If the task asks to read or analyze a file before making an image or diagram, include a read/analyze step before the image step. Do not invent physical/manual preparation steps such as preparing paper, brushes, boards, colors, or shading. Return JSON with a 'steps' array of strings."},
            {"role": "user", "content": task}
        ],
        response_format={"type": "json_object"}
    )
    try:
        plan_data = json.loads(response.choices[0].message.content)
        steps = normalize_plan_steps(plan_data.get("steps", [task]))
        current_plan = steps
        print(f"[Plan] Created {len(steps)} steps")
        for i, step in enumerate(steps, 1):
            print(f"  {i}. {step}")
        return f"Plan created with {len(steps)} steps. Executing now..."
    except:
        return "Error: Failed to create plan"

available_functions = {"read": read, "write": write, "edit": edit, "glob": glob, "grep": grep, "bash": bash, "get_https": get_https, "plan": plan}

def normalize_plan_steps(raw_steps):
    # 兜底把任意格式的 steps 归一化为字符串列表，避免后续执行阶段崩溃。
    if not isinstance(raw_steps, list):
        raw_steps = [raw_steps]

    steps = []
    for step in raw_steps:
        if isinstance(step, str):
            normalized = step.strip()
        elif isinstance(step, dict):
            tool_name = step.get("tool")
            prompt = step.get("prompt") or step.get("task") or step.get("description")
            if tool_name and prompt:
                normalized = f"Use the {tool_name} tool for this task: {prompt}"
            else:
                normalized = json.dumps(step, ensure_ascii=False)
        else:
            normalized = json.dumps(step, ensure_ascii=False)

        if normalized:
            steps.append(normalized)

    return steps

def parse_tool_arguments(raw_arguments: str) -> dict[str, Any]:
    # 工具参数必须是 JSON 对象；解析失败时返回结构化错误供模型自修复。
    if not raw_arguments:
        return {}
    try:
        parsed = json.loads(raw_arguments)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError as error:
        return {"_argument_error": f"Invalid JSON arguments: {error}"}

def load_memory():
    # 只注入最近 50 行记忆，控制上下文长度。
    if not os.path.exists(MEMORY_FILE):
        return ""
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')
            return '\n'.join(lines[-50:]) if len(lines) > 50 else content
    except:
        return ""

def save_memory(task, result):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n## {timestamp}\n**Task:** {task}\n**Result:** {result}\n"
    try:
        with open(MEMORY_FILE, 'a', encoding='utf-8') as f:
            f.write(entry)
    except:
        pass

def load_rules():
    rules = []
    if not os.path.exists(RULES_DIR):
        return ""
    try:
        for rule_file in Path(RULES_DIR).glob("*.md"):
            with open(rule_file, 'r') as f:
                rules.append(f"# {rule_file.stem}\n{f.read()}")
        return "\n\n".join(rules) if rules else ""
    except:
        return ""

def load_skills():
    skills = []
    if not os.path.exists(SKILLS_DIR):
        return []
    for skill_file in Path(SKILLS_DIR).glob("*.json"):
        try:
            # Use utf-8-sig to tolerate BOM-prefixed JSON files.
            with open(skill_file, 'r', encoding='utf-8-sig') as f:
                skills.append(json.load(f))
        except Exception as exc:
            print(f"[Skills] Failed to load {skill_file.name}: {exc}")
    return skills

def collect_skill_system_prompt_additions(skills):
    # 仅拼接技能中显式声明的 system prompt 增量指令。
    additions = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        templates = skill.get("templates")
        if not isinstance(templates, dict):
            continue
        addition = templates.get("system_prompt_addition")
        if isinstance(addition, str) and addition.strip():
            skill_name = skill.get("name", "skill")
            additions.append(f"## {skill_name}\n{addition.strip()}")
    return "\n\n".join(additions)

def create_mcp_loader():
    if os.path.exists(MCP_CONFIG):
        return MCPToolLoader(project_root=".", custom_config_path=MCP_CONFIG)
    return MCPToolLoader(project_root=".", minimax_config_path=".agent/mcp.minimax.json")

def get_tool_name(tool):
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    if not isinstance(function, dict):
        return ""
    return str(function.get("name", ""))

def merge_tools(local_tools, external_tools):
    """Merge local and MCP tool schemas, keeping local tools on name conflicts."""
    merged = []
    seen_names = set()

    for tool in local_tools:
        name = get_tool_name(tool)
        if not name or name in seen_names:
            continue
        merged.append(tool)
        seen_names.add(name)

    for tool in external_tools:
        name = get_tool_name(tool)
        if not name:
            continue
        if name in seen_names:
            print(f"[MCP] Skipping MCP tool '{name}' because a local tool has the same name")
            continue
        merged.append(tool)
        seen_names.add(name)

    return merged

def load_mcp_tools(loader=None):
    loader = loader or create_mcp_loader()
    # 优先配置自定义mcp服务器，其次加载本地缓存的mcp tools，最后才是解析配置文件或minimax配置生成工具
    if os.path.exists(MCP_CONFIG):
        tools = loader.load_tools()
        print(f"[MCP] Loaded {len(tools)} tools from {MCP_CONFIG}")
        return tools

    tools = loader.load_cached_tools()
    if tools:
        print(f"[MCP] Loaded {len(tools)} cached tools from {loader.tool_cache_path}")
        return tools

    tools = loader.load_tools()
    if tools:
        loader.save_cached_tools(tools)
    print(f"[MCP] Loaded {len(tools)} tools from {loader.minimax_config_path}")
    return tools

def call_mcp_tool(loader, mcp_tool_names, function_name, function_args):
    if function_name not in mcp_tool_names:
        return None
    try:
        return loader.call_tool(function_name, function_args, timeout=180)
    except Exception as exc:
        return f"Error: MCP tool '{function_name}' failed: {exc}"

def should_run_direct_tool_task(task, mcp_tool_names):
    # 纯图片生成请求可直接调 text_to_image，避免无意义的 plan 拆分（防止最坏的情况发生）
    if "text_to_image" not in mcp_tool_names:
        return False
    task_text = task.lower()
    analysis_intents = [
        "阅读",
        "读取",
        "分析",
        "代码结构",
        "架构图",
        "目录",
        ".py",
        "file",
        "read",
        "analyze",
        "architecture",
        "diagram",
    ]
    if any(intent in task_text for intent in analysis_intents):
        return False
    image_intents = [
        "text_to_image",
        "prompt",
        "生成图片",
        "生成一张图",
        "画一幅图",
        "画一张图",
        "图片",
        "image",
        "draw",
    ]
    return any(intent in task_text for intent in image_intents)

def build_code_diagram_plan(task):
    # 先读代码再画图场景强制生成两步计划，降低幻觉风险。
    task_text = task.lower()
    needs_file_context = any(intent in task_text for intent in ["阅读", "读取", "分析", "read", "analyze"])
    wants_diagram = any(intent in task_text for intent in ["架构图", "结构图", "diagram", "architecture"])
    if not (needs_file_context and wants_diagram):
        return []

    file_match = re.search(r"[A-Za-z0-9_./\\-]+\.py", task)
    target_file = file_match.group(0) if file_match else "agent.py"
    return [
        f"Read {target_file} and summarize its actual code structure, including imports, tool definitions, functions, and main execution flow.",
        f"Use text_to_image to generate an architecture diagram based on the actual structure summarized from {target_file}.",
    ]

def run_agent_step(messages, tools, max_iterations=30, mcp_loader=None, mcp_tool_names=None):
    global current_plan, plan_mode
    mcp_tool_names = mcp_tool_names or set()
    # agent loop
    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=messages,
            tools=tools
        )
        message = response.choices[0].message
        messages.append(message)
        # 依旧无工具调用直接结束
        if not message.tool_calls:
            return message.content, messages
        for tool_call in message.tool_calls:
            function_payload = getattr(tool_call, "function", None)
            if function_payload is None:
                continue
            function_name = str(getattr(function_payload, "name", ""))
            raw_arguments = str(getattr(function_payload, "arguments", ""))
            # 将json字符串解析为字典
            function_args = parse_tool_arguments(raw_arguments)
            print(f"[Tool] {function_name}({function_args})")
            function_impl = available_functions.get(function_name)
            # 依旧解析错误返回错误的信息--Invalid JSON arguments: ...
            if "_argument_error" in function_args:
                function_response = f"Error: {function_args['_argument_error']}"
            elif function_name == "plan" and function_impl is not None:
                # plan 工具是递归入口：产出步骤后逐步执行，且执行中禁用二次 plan。
                # 单独处理plan函数——即使用户不主动打开plan mode，ai也可以调用plan工具来分解任务
                plan_mode = True
                function_response = function_impl(**function_args)
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": function_response})
                # 如果plan函数被正确的执行了 那么curr plan包含了分解的步骤 直接执行就好
                if current_plan:
                    results = []
                    for i, step in enumerate(current_plan, 1):
                        print(f"\n[Step {i}/{len(current_plan)}] {step}")
                        messages.append({"role": "user", "content": step})
                        result, messages = run_agent_step(
                            messages,
                            [t for t in tools if t["function"]["name"] != "plan"],
                            mcp_loader=mcp_loader,
                            mcp_tool_names=mcp_tool_names,
                        )
                        results.append(result)
                        print(f"\n{result}")
                    # 执行完毕重置plan状态
                    plan_mode = False
                    current_plan = []
                    return "\n".join(results), messages
            elif function_impl is not None:
                # 非plan函数直接执行即可
                function_response = function_impl(**function_args)
            elif mcp_loader is not None and function_name in mcp_tool_names:
                function_response = call_mcp_tool(
                    mcp_loader,
                    mcp_tool_names,
                    function_name,
                    function_args,
                )
            else:
                function_response = f"Error: Unknown tool '{function_name}'"
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": function_response})
    return "Max iterations reached", messages

# agent loop
def run_agent_claudecode(task, use_plan=False):
    global plan_mode, current_plan
    print("[Init] Loading ClaudeCode features...")
    # 初始化agent skills mcp tools...
    memory = load_memory()
    rules = load_rules()
    skills = load_skills()
    mcp_loader = create_mcp_loader()
    mcp_tools = load_mcp_tools(mcp_loader)
    # 合并外部工具
    all_tools = merge_tools(base_tools, mcp_tools)
    mcp_tool_names = {
        get_tool_name(tool)
        for tool in mcp_tools
        if get_tool_name(tool) not in available_functions
    }
    context_parts = [
        "You are a helpful assistant that can interact with the system. Be concise.",
        "When the user asks to generate or draw an image and a text_to_image tool is available, call text_to_image directly with the user's requested prompt. Do not break the image request into manual drawing or preparation steps.",
    ]
    if rules:
        context_parts.append(f"\n# Rules\n{rules}")
        print(f"[Rules] Loaded {len(rules.split('# '))-1} rule files")
    if skills:
        context_parts.append(f"\n# Skills\n" + "\n".join([f"- {s['name']}: {s.get('description', '')}" for s in skills]))
        skill_additions = collect_skill_system_prompt_additions(skills)
        if skill_additions:
            context_parts.append(f"\n# Skill Instructions\n{skill_additions}")
        print(f"[Skills] Loaded {len(skills)} skills")
    if mcp_tools:
        print(f"[MCP] Loaded {len(mcp_tools)} MCP tools")
    if memory:
        context_parts.append(f"\n# Previous Context\n{memory}")
    messages = [{"role": "system", "content": "\n".join(context_parts)}]
    if use_plan and should_run_direct_tool_task(task, mcp_tool_names):
        # 计划模式下也允许对纯图片任务走“直调工具”快速路径。
        print("[Plan] Direct tool task detected; skipping plan and calling tools directly.")
        messages.append({"role": "user", "content": task})
        final_result, messages = run_agent_step(
            messages,
            all_tools,
            mcp_loader=mcp_loader,
            mcp_tool_names=mcp_tool_names,
            max_iterations=50,
        )
        print(f"\n{final_result}")
    elif use_plan:
        # 若命中“代码结构图”模式，优先使用固定两步计划；否则让模型自由拆解。
        forced_plan = build_code_diagram_plan(task)
        if forced_plan:
            current_plan = forced_plan
            plan_result = f"Plan created with {len(current_plan)} steps. Executing now..."
            print("[Plan] Using code-diagram plan based on the requested file workflow.")
            for i, step in enumerate(current_plan, 1):
                print(f"  {i}. {step}")
        else:
            plan_result = plan(task)
        print(f"[Plan] {plan_result}")
        if not current_plan:
            final_result = plan_result
            print(f"\n{final_result}")
            save_memory(task, final_result)
            return final_result
        plan_mode = True
        results = []
        for i, step in enumerate(current_plan, 1):
            print(f"\n[Step {i}/{len(current_plan)}] {step}")
            messages.append({"role": "user", "content": step})
            result, messages = run_agent_step(
                messages,
                [t for t in all_tools if t["function"]["name"] != "plan"],
                mcp_loader=mcp_loader,
                mcp_tool_names=mcp_tool_names,
                max_iterations=50,
            )
            results.append(result)
            print(f"\n{result}")
        plan_mode = False
        current_plan = []
        final_result = "\n".join(results)
    else:
        messages.append({"role": "user", "content": task})
        final_result, messages = run_agent_step(
            messages,
            all_tools,
            mcp_loader=mcp_loader,
            mcp_tool_names=mcp_tool_names,
            max_iterations=50,
        )
        print(f"\n{final_result}")
    save_memory(task, final_result)
    return final_result

if __name__ == "__main__":
    # 程序入口--命令行解析
    use_plan = "--plan" in sys.argv
    if use_plan:
        sys.argv.remove("--plan")
    if len(sys.argv) < 2:
        print("Usage: python agent-claudecode.py [--plan] 'your task'")
        print("  --plan: Enable task planning")
        print("\nFeatures: Memory, Rules, Skills, MCP, Plan tool")
        sys.exit(1)
    task = " ".join(sys.argv[1:])
    run_agent_claudecode(task, use_plan=use_plan)




