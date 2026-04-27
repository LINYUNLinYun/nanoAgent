import json
import os
import queue
import re
import subprocess
import threading
from pathlib import Path


class StdioMCPClient:
    """通过stdio与MCP服务器通信的客户端"""

    def __init__(self, command, args=None, env=None, cwd=None, timeout=15):
        """
        初始化MCP客户端

        Args:
            command: 要执行的命令
            args: 命令参数列表
            env: 环境变量字典
            cwd: 工作目录
            timeout: 响应超时时间（秒）
        """
        self.command = command
        self.args = args or []
        self.env = env or os.environ.copy()
        self.cwd = cwd
        self.timeout = timeout
        self.process = None
        self._messages = queue.Queue()  # 收到的消息队列
        self._reader_thread = None
        self._stderr_lines = queue.Queue()  # stderr输出队列
        self._stderr_thread = None
        self._next_id = 1  # 下一个请求ID

    def __enter__(self):
        # 启动子进程，建立通信管道
        self.process = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            env=self.env,
        )
        # 启动后台线程读取stdout和stderr
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def close(self):
        """关闭子进程"""
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def initialize(self):
        """发送initialize请求，返回服务器响应"""
        response = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "nanoAgent",
                    "version": "0.1.0",
                },
            },
        )
        self.notify("notifications/initialized", {})
        return response

    def list_tools(self):
        """列出MCP服务器提供的所有工具"""
        response = self.request("tools/list", {})
        result = response.get("result", {})
        tools = result.get("tools", [])
        return tools if isinstance(tools, list) else []

    def request(self, method, params=None, timeout=None):
        """
        发送JSON-RPC请求并等待响应

        Args:
            method: JSON-RPC方法名
            params: 请求参数
            timeout: 超时时间（秒）

        Returns:
            服务器响应消息
        """
        request_id = self._next_id
        self._next_id += 1
        wait_timeout = self.timeout if timeout is None else timeout
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )

        while True:
            try:
                message = self._messages.get(timeout=wait_timeout)
            except queue.Empty as exc:
                stderr_output = self.get_stderr()
                details = f"Timeout waiting for MCP response to '{method}'"
                if self.process is not None and self.process.poll() is not None:
                    details += f" (process exited with code {self.process.returncode})"
                if stderr_output:
                    details += f". stderr: {stderr_output}"
                raise RuntimeError(details) from exc
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                details = f"MCP request failed: {error}"
                stderr_output = self.get_stderr()
                if stderr_output:
                    details += f". stderr: {stderr_output}"
                raise RuntimeError(details)
            return message

    def notify(self, method, params=None):
        """发送JSON-RPC通知（无响应）"""
        self._send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
            }
        )

    def _send(self, message):
        """发送JSON-RPC消息到stdin"""
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("MCP process is not running")
        body = (json.dumps(message) + "\n").encode("utf-8")
        self.process.stdin.write(body)
        self.process.stdin.flush()

    def _reader_loop(self):
        """后台线程：读取stdout并放入消息队列"""
        try:
            while self.process is not None and self.process.stdout is not None:
                line = self.process.stdout.readline()
                if not line:
                    return
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue
                self._messages.put(json.loads(decoded))
        except Exception as exc:
            self._messages.put({"error": {"message": str(exc)}})

    def _stderr_loop(self):
        """后台线程：读取stderr并放入队列"""
        if self.process is None or self.process.stderr is None:
            return
        try:
            for line in self.process.stderr:
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    self._stderr_lines.put(decoded)
        except Exception:
            return

    def get_stderr(self):
        """获取所有暂存的stderr输出"""
        lines = []
        while True:
            try:
                lines.append(self._stderr_lines.get_nowait())
            except queue.Empty:
                break
        return " | ".join(lines)


class MCPToolLoader:
    """MCP工具加载器，负责从配置文件或MCP服务器加载工具"""

    def __init__(
        self,
        project_root=".",
        custom_config_path=".agent/mcp.json",
        tool_cache_path=".agent/mcp_tools_cache.json",
        minimax_config_path=None,
    ):
        """
        初始化工具加载器

        Args:
            project_root: 项目根目录
            custom_config_path: 自定义配置文件路径
            tool_cache_path: 工具缓存文件路径
            minimax_config_path: MiniMax服务器配置文件路径
        """
        self.project_root = Path(project_root)
        self.custom_config_path = self.project_root / custom_config_path
        self.tool_cache_path = self.project_root / tool_cache_path
        # 默认从 mcp.minimax.local.json 加载MiniMax服务器配置
        self.minimax_config_path = self.project_root / minimax_config_path if minimax_config_path else self.project_root / ".agent/mcp.minimax.local.json"

    def load_tools(self):
        """
        加载工具：优先从自定义配置加载，否则从MCP服务器获取

        Returns:
            工具列表
        """
        if self.custom_config_path.exists():
            # 自定义mcp服务器
            static_tools = self._load_static_tools(self.custom_config_path)
            if static_tools:
                return static_tools
            custom_config = self._load_custom_server_config()
            if custom_config:
                return self._load_tools_from_server_config(custom_config)

        config = self._load_minimax_server_config()
        if config:
            return self._load_tools_from_server_config(config)
        return []

    def load_cached_tools(self):
        """从缓存文件加载已保存的工具"""
        try:
            with open(self.tool_cache_path, "r", encoding="utf-8") as file:
                payload = json.load(file)
        except Exception:
            return []
        tools = payload.get("tools", [])
        return tools if isinstance(tools, list) else []

    def save_cached_tools(self, tools):
        """保存工具列表到缓存文件"""
        self.tool_cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.tool_cache_path, "w", encoding="utf-8") as file:
            json.dump({"tools": tools}, file, ensure_ascii=False, indent=2)

    def call_tool(self, tool_name, arguments, timeout=120, as_text=True):
        """
        调用指定的MCP工具

        Args:
            tool_name: 工具名称
            arguments: 工具参数
            timeout: 超时时间（秒）
            as_text: 是否返回格式化文本

        Returns:
            格式化后的结果字符串或原始结果
        """
        server_name, server_config = self._get_server_for_tool(tool_name)
        if not server_config:
            raise RuntimeError(f"No active MCP server configuration found for tool '{tool_name}'")
        result = self._call_tool_via_stdio(server_name, server_config, tool_name, arguments, timeout=timeout)
        summary = self._format_tool_result(tool_name, arguments, result)
        print(summary)
        if as_text:
            return summary
        return result

    def _load_static_tools(self, config_path):
        """从静态配置文件加载工具定义"""
        try:
            with open(config_path, "r", encoding="utf-8") as file:
                config = json.load(file)
        except Exception:
            return []

        mcp_tools = []
        for server_config in config.get("mcpServers", {}).values():
            if server_config.get("disabled", False):
                continue
            for tool in server_config.get("tools", []):
                normalized = self._normalize_tool(tool)
                if normalized:
                    mcp_tools.append(normalized)
        return mcp_tools

    def _load_tools_from_server_config(self, config):
        """从服务器配置启动MCP服务并获取工具列表"""
        servers = config.get("mcpServers", {})
        if not isinstance(servers, dict):
            return []

        for server_name, server_config in servers.items():
            if server_config.get("disabled", False):
                continue
            tools = self._list_tools_via_stdio(server_name, server_config)
            if tools:
                return tools
        return []

    def _load_custom_server_config(self):
        """从 .agent/mcp.json 加载用户自定义 MCP 配置"""
        config_path = self.custom_config_path
        if not config_path.exists():
            return None
        try:
            with open(config_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return None

    def _iter_active_servers(self, config):
        servers = config.get("mcpServers", {}) if isinstance(config, dict) else {}
        if not isinstance(servers, dict):
            return
        for server_name, server_config in servers.items():
            if not isinstance(server_config, dict):
                continue
            if server_config.get("disabled", False):
                continue
            yield server_name, server_config

    def _server_declares_tool(self, server_config, tool_name):
        tools = server_config.get("tools", [])
        if not tools:
            return False
        for tool in tools:
            normalized = self._normalize_tool(tool)
            if not normalized:
                continue
            function = normalized.get("function", {})
            if function.get("name") == tool_name:
                return True
        return False

    def _get_server_for_tool(self, tool_name):
        """获取可以调用指定工具的 MCP 服务器配置"""
        custom_config = self._load_custom_server_config()
        if custom_config:
            first_active = (None, None)
            for server_name, server_config in self._iter_active_servers(custom_config):
                if first_active == (None, None):
                    first_active = (server_name, server_config)
                if self._server_declares_tool(server_config, tool_name):
                    return server_name, server_config
            # If custom config only provides one command without static tools,
            # let the MCP server validate the actual tool name.
            if first_active != (None, None):
                return first_active

        config = self._load_minimax_server_config()
        if not config:
            return None, None

        for server_name, server_config in self._iter_active_servers(config):
            return server_name, server_config
        return None, None

    def _load_minimax_server_config(self):
        """从 mcp.minimax.local.json 加载服务器配置"""
        config_path = self.minimax_config_path
        if not config_path.exists():
            return None
        try:
            with open(config_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return None

    def _list_tools_via_stdio(self, server_name, server_config):
        """启动MCP服务器并通过stdio获取工具列表"""
        command, args, env = self._build_process_config(server_name, server_config)
        if not command:
            return []

        try:
            with StdioMCPClient(
                command=command,
                args=args,
                env=env,
                cwd=str(self.project_root),
            ) as client:
                client.initialize()
                tools = client.list_tools()
        except Exception as exc:
            print(f"[MCP] Failed to load tools from server '{server_name}': {exc}")
            return []

        mcp_tools = []
        for tool in tools:
            normalized = self._normalize_tool(tool)
            if normalized:
                mcp_tools.append(normalized)
        return mcp_tools

    def _call_tool_via_stdio(self, server_name, server_config, tool_name, arguments, timeout=120):
        """通过stdio调用MCP工具"""
        command, args, env = self._build_process_config(server_name, server_config)
        if not command:
            raise RuntimeError(f"Server '{server_name}' is missing a command")

        with StdioMCPClient(
            command=command,
            args=args,
            env=env,
            cwd=str(self.project_root),
        ) as client:
            client.initialize()
            response = client.request(
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": arguments,
                },
                timeout=timeout,
            )
        return response.get("result", response)

    def _build_process_config(self, server_name, server_config):
        """
        构建进程启动配置

        Returns:
            (command, args, env) 元组，如果API_KEY无效则返回(None, None, None)
        """
        command = server_config.get("command")
        if not command:
            return None, None, None

        args = server_config.get("args", [])
        server_env = server_config.get("env", {})
        env = os.environ.copy()
        env.update(server_env)

        # Only MiniMax servers require the MiniMax API key check.
        process_text = " ".join([str(command), *[str(arg) for arg in args]]).lower()
        is_minimax_server = (
            "minimax" in str(server_name).lower()
            or "minimax" in process_text
            or any(str(key).startswith("MINIMAX_") for key in server_env)
        )
        if is_minimax_server:
            api_key = env.get("MINIMAX_API_KEY", "")
            if not api_key or api_key == "REPLACE_WITH_MINIMAX_API_KEY":
                print(f"[MCP] Skipping server '{server_name}': MINIMAX_API_KEY is missing or still using the placeholder value.")
                return None, None, None

        # uvx命令需要设置缓存目录
        if command == "uvx":
            cache_dir = self.project_root / ".uv-cache"
            tool_dir = self.project_root / ".uv-tools"
            cache_dir.mkdir(parents=True, exist_ok=True)
            tool_dir.mkdir(parents=True, exist_ok=True)
            env.setdefault("UV_CACHE_DIR", str(cache_dir))
            env.setdefault("UV_TOOL_DIR", str(tool_dir))

        return command, args, env

    def _normalize_tool(self, tool):
        """
        统一工具格式为 {type: "function", function: {...}}

        Returns:
            标准化后的工具对象，或None（无效工具）
        """
        if not isinstance(tool, dict):
            return None

        # 已经是标准格式
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            return {"type": "function", "function": tool["function"]}

        if isinstance(tool.get("function"), dict):
            return {"type": "function", "function": tool["function"]}

        # 从旧格式转换
        name = tool.get("name")
        if not name:
            return None

        parameters = tool.get("inputSchema") or tool.get("input_schema") or tool.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": tool.get("description", ""),
                "parameters": parameters,
            },
        }

    def _format_tool_result(self, tool_name, arguments, result):
        """将工具调用结果格式化为易读的字符串"""
        lines = [f"MCP tool '{tool_name}' finished."]
        lines.append(f"Arguments: {json.dumps(arguments, ensure_ascii=False)}")

        if isinstance(result, dict):
            is_error = result.get("isError")
            if is_error is True:
                lines.append("Status: error")
            elif is_error is False:
                lines.append("Status: success")

            content = result.get("content")
            content_text = self._extract_content_text(content)
            if content_text:
                lines.append(f"Content: {content_text}")

            file_paths = self._extract_file_paths(result)
            if file_paths:
                lines.append("Files: " + ", ".join(file_paths))

            remaining_keys = {
                key: value
                for key, value in result.items()
                if key not in {"content", "isError"}
            }
            if remaining_keys:
                lines.append(f"Metadata: {json.dumps(remaining_keys, ensure_ascii=False)}")
        else:
            lines.append(f"Content: {result}")

        return "\n".join(lines)

    def _extract_content_text(self, content):
        """从content字段提取文本内容"""
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""

        text_parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text"):
                text_parts.append(str(item["text"]))
            elif item.get("type") == "image" and item.get("path"):
                text_parts.append(f"image saved at {item['path']}")
        return " | ".join(text_parts)

    def _extract_file_paths(self, result):
        """从结果中提取有效的文件路径"""
        found_paths = []

        def add_candidate(value):
            if not isinstance(value, str):
                return
            normalized = value.strip()
            if not normalized:
                return
            if os.path.exists(normalized):
                found_paths.append(normalized)

        def walk(value):
            if isinstance(value, dict):
                for nested in value.values():
                    walk(nested)
                return
            if isinstance(value, list):
                for nested in value:
                    walk(nested)
                return
            add_candidate(value)

        walk(result)

        # 从文本内容中匹配路径
        content_text = self._extract_content_text(result.get("content")) if isinstance(result, dict) else ""
        for match in re.findall(r"[A-Za-z]:\\[^|]+|(?:\./|../)?output[\\/][^|]+", content_text):
            add_candidate(match.rstrip(" ."))

        # 去重
        unique_paths = []
        seen = set()
        for path in found_paths:
            if path in seen:
                continue
            seen.add(path)
            unique_paths.append(path)
        return unique_paths


if __name__ == "__main__":
    # 测试代码：打印配置和加载的工具
    loader = MCPToolLoader(project_root=".",minimax_config_path=".agent/mcp.minimax.json")
    print("[MCP Test] -------------- Initialized MCPToolLoader -----------------\n")
    print("[MCP Test] project_root =", loader.project_root.resolve())
    print("[MCP Test] custom_config =", loader.custom_config_path, "exists =", loader.custom_config_path.exists())

    config_path = loader.minimax_config_path
    print("[MCP Test] minimax_config =", config_path, "exists =", config_path.exists())
    print("-------------------------------------------------------\n")
    
    # 载入mcp server的所有tools，并打印工具名称和描述
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as file:
                config = json.load(file)
            servers = config.get("mcpServers", {})
            print("[MCP Test] servers in", config_path.name, "=", list(servers.keys()))
            for server_name, server_config in servers.items():
                env = server_config.get("env", {})
                api_key = env.get("MINIMAX_API_KEY", "")
                masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else api_key
                print(
                    f"[MCP Test] {server_name}: command={server_config.get('command')} "
                    f"args={server_config.get('args', [])} "
                    f"host={env.get('MINIMAX_API_HOST', '')} "
                    f"api_key={masked_key or '<missing>'}"
                )
        except Exception as exc:
            print(f"[MCP Test] Failed to read {config_path}: {exc}")
    if 0:
        tools = loader.load_tools()
        print(f"[MCP Test] loaded {len(tools)} tools")
        for tool in tools:
            function = tool.get("function", {})
            print(f"- {function.get('name', '<unknown>')}: {function.get('description', '')}")


    # 调用text_to_image工具生成图片，并打印结果
    # 载入tools json
    cached_tools = loader.load_cached_tools()
    print(f"cached tools: {[tool.get('function', {}).get('name') for tool in cached_tools]}")
    output_directory = "text_to_image_test"
    os.makedirs(
        os.path.join("output", "minimax_mcp_data", output_directory),
        exist_ok=True,
    )

    result = loader.call_tool(
        "text_to_image",
        {
            "prompt": "A beautiful girls with long hair, smiling with eyes closed, holding a roses facing the camera, wearing a light-colored long dress, in an animated style (2D illustration), high quality",
            "output_directory": output_directory,
            "aspect_ratio": "4:3",
        },
        timeout=180,
    )
    print(result)
