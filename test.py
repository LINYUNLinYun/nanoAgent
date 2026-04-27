import os
import sys

from mcp_tool_loader import MCPToolLoader
from agent_claudecode import get_https


if __name__ == "__main__":
    # Avoid Windows GBK stdout errors when printing web content to a pipe.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    loader = MCPToolLoader(project_root=".")
    cached_tools = loader.load_cached_tools()
    print(f"cached tools: {[tool.get('function', {}).get('name') for tool in cached_tools]}")
    output_directory = "text_to_image_test"
    os.makedirs(
        os.path.join("output", "minimax_mcp_data", output_directory),
        exist_ok=True,
    )

    # result = loader.call_tool(
    #     "text_to_image",
    #     {
    #         "prompt": "A tiny orange robot reading code at a desk, clean illustration style",
    #         "output_directory": output_directory,
    #     },
    #     timeout=180,
    # )
    result = get_https("https://github.com/LINYUNLinYun/23-vision-ZhuRonghui")
    print(result)
