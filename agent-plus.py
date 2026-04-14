import os
import json
import subprocess
import sys
from datetime import datetime
from typing import Any
from openai import OpenAI
from dotenv import load_dotenv


# 改为读取本地环境变量，免去设置系统环境变量
load_dotenv()

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    base_url=os.environ.get("OPENAI_BASE_URL")
)

#memory_file 用于存储agent的长期记忆，记录之前执行过的任务和结果，供后续任务参考
MEMORY_FILE = "agent_memory.md"

# tools 和基础版agent无不同
tools = [
    {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "Execute a bash command on the system",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to execute"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        }
    }
]

def execute_bash(command):
    '''相较于普通版agent，这里增加了timeout:30s，一定程度上是为了防止某些情况下卡死比如网络问题等
        但是它怎么区分长时间的任务和卡死呢？感觉会有个潜在的坑。
        此外，采用try-except捕获异常，防止某些命令执行失败导致整个agent崩溃，并把错误信息返回给模型，供下一轮对话使用。
    '''
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        return result.stdout + result.stderr
    except Exception as e:
        return f"Error: {str(e)}"

def read_file(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error: {str(e)}"

def write_file(path, content):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error: {str(e)}"

# 和基础版一样
available_functions = {
    "execute_bash": execute_bash,
    "read_file": read_file,
    "write_file": write_file
}

def parse_tool_arguments(raw_arguments: str) -> dict[str, Any]:
    '''解析工具调用的原始参数字符串，返回一个字典。如果解析失败，返回包含错误信息的字典'''
    if not raw_arguments:
        return {}
    try:
        parsed = json.loads(raw_arguments)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError as error:
        return {"_argument_error": f"Invalid JSON arguments: {error}"}

# 载入最近的50行记忆
def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return ""
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')
            return '\n'.join(lines[-50:]) if len(lines) > 50 else content
    except:
        return ""

# 将本次执行的结果存入记忆文件
def save_memory(task, result):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n## {timestamp}\n**Task:** {task}\n**Result:** {result}\n"
    try:
        # 注意是'a'追加模式写入，'w'是覆盖写入
        with open(MEMORY_FILE, 'a', encoding='utf-8') as f:
            f.write(entry)
    except:
        pass

def create_plan(task):
    '''
        让模型将任务分成多个步骤，并按照规定的json格式返回步骤再解析
    '''
    print("[Planning] Breaking down task...")
    # response 强制返回json
    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": "Break down the task into 3-5 simple, actionable steps. Return as JSON array of strings."},
            {"role": "user", "content": f"Task: {task}"}
        ],
        response_format={"type": "json_object"}
    )
    try:
        # print(f"\n\n[Debug] Raw plan response below:-----------------\n{response.choices[0].message}\n --------------------------------\n\n")
        # print("------------------------------------------\n")
        plan_data = json.loads(response.choices[0].message.content)
        # print(f"\n\n[Debug] Parsed plan data: {plan_data}\n\n")
        if isinstance(plan_data, dict):
            steps = plan_data.get("steps", [task])
        elif isinstance(plan_data, list):
            steps = plan_data
        else:
            steps = [task]
        print(f"[Plan] {len(steps)} steps created")
        for i, step in enumerate(steps, 1):
            print(f"  {i}. {step}")
        return steps
    except:
        return [task]

def run_agent_step(task, messages, max_iterations=5):
    messages.append({"role": "user", "content": task})
    actions = []
    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=messages,
            tools=tools
        )
        message = response.choices[0].message
        messages.append(message)
        if not message.tool_calls:
            return message.content, actions, messages
        for tool_call in message.tool_calls:
            function_payload = getattr(tool_call, "function", None)
            if function_payload is None:
                continue
            function_name = str(getattr(function_payload, "name", ""))
            raw_arguments = str(getattr(function_payload, "arguments", ""))
            function_args = parse_tool_arguments(raw_arguments)
            print(f"[Tool] {function_name}({function_args})")
            function_impl = available_functions.get(function_name)
            if function_impl is None:
                function_response = f"Error: Unknown tool '{function_name}'"
            elif "_argument_error" in function_args:
                function_response = f"Error: {function_args['_argument_error']}"
            else:
                function_response = function_impl(**function_args)
                actions.append({"tool": function_name, "args": function_args})
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": function_response})
    return "Max iterations reached", actions, messages

def run_agent_plus(task, use_plan=False):
    memory = load_memory()
    system_prompt = "You are a helpful assistant that can interact with the system. Be concise."
    if memory:
        system_prompt += f"\n\nPrevious context:\n{memory}"
    messages = [{"role": "system", "content": system_prompt}]
    # using plan mode
    if use_plan:
        # 实测如果是不复杂的任务，最好不要plan，不然会导致不必要的错误
        steps = create_plan(task)
    else:
        steps = [task]
    all_results = []
    for i, step in enumerate(steps, 1):
        if len(steps) > 1:
            print(f"\n[Step {i}/{len(steps)}] {step}")
        result, actions, messages = run_agent_step(step, messages,max_iterations=20)
        all_results.append(result)
        print(f"\n{result}")
    final_result = "\n".join(all_results)
    save_memory(task, final_result)
    return final_result

if __name__ == "__main__":
    use_plan = "--plan" in sys.argv
    if use_plan:
        sys.argv.remove("--plan")
    if len(sys.argv) < 2:
        print("Usage: python agent-plus.py [--plan] 'your task here'")
        print("  --plan: Enable task planning and decomposition")
        sys.exit(1)
    task = " ".join(sys.argv[1:])
    run_agent_plus(task, use_plan=use_plan)
