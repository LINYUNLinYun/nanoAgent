import os
import json
import subprocess
from openai import OpenAI
from dotenv import load_dotenv


# 改为读取本地环境变量，免去设置系统环境变量
load_dotenv()

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
    base_url=os.environ.get("OPENAI_BASE_URL")
)
# openai 的工具列表封装，每个函数，包含函数名、描述和参数信息，供模型调用
tools = [
    {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "Execute a bash command",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write to a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
]


def execute_bash(command):
    '''开一个子进程调用shell并返回标准输出和错误输出'''
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return result.stdout + result.stderr


def read_file(path):
    with open(path, "r") as f:
        return f.read()


def write_file(path, content):
    with open(path, "w") as f:
        f.write(content)
    return f"Wrote to {path}"


functions = {"execute_bash": execute_bash, "read_file": read_file, "write_file": write_file}


def run_agent(user_message, max_iterations=5):
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Be concise."},
        {"role": "user", "content": user_message},
    ]
    # 对于复杂的任务，模型会需要多轮对话才能完成，所以这个最大迭代次数不能太小，否则任务完成到一半就会终止
    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=messages,
            tools=tools,
        )
        message = response.choices[0].message
        messages.append(message)
        # print(message.content, "\n---------\n\n\n\n\n\n")
        if not message.tool_calls:
            return message.content              # 如果没有工具调用，直接返回内容，即为最终结果
        for tool_call in message.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            print(f"[Tool] {name}({args})")
            if name not in functions:
                result = f"Error: Unknown tool '{name}'"
            else:
                result = functions[name](**args)        #调用工具函数，并把结果加进messages中，供下一轮对话使用
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
    return "Max iterations reached"


if __name__ == "__main__":
    import sys
    # print(sys.argv)
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Hello"     # join用空格连接命令行参数
    # print(f"sdadasda{task}")
    print(run_agent(task))
