# nanoAgent 项目结构说明
本项目基于NanoAgent二次开发。
- 拓展1 新增mcp tools——集成minimax mcp client 以文生图为例
- 拓展2 增加本地tool——get_https 可以读取网页并理解
- 拓展3 增加本地skill 约束“阅读代码并画图”这个技能的工作边界


## 验证方法
```bash
# 运行测试
python -m pytest tests/test_agent.py

# 测试基础版
python agent.py "列出当前目录文件"

# 测试增强版  
python agent-plus.py "创建test.txt文件"

# 测试ClaudeCode版
python agent_claudecode.py "搜索所有.py文件"

# 测试get_https工具
python agent_claudecode.py "告诉我这个网页关于什么：https://github.com/LINYUNLinYun/23-vision-ZhuRonghui" --plan

# 测试文生图工具调用
python agent_claudecode.py "please draw a picture of a masterpiece with best quality, there is a silhouette of a girl in the center of the picture from the distance, and the girl has silver long hair, school uniform and cherry blossoms background, in soft light and anime style" --plan

# 测试阅读代码并画流程图技能
python agent_claudecode.py "请阅读该目录下的的agent.py文件，并画一个生动易懂的流程图说明agent的工作原理" --plan
```

## 运行结果
### get_https工具测试结果：成功获取网页内容并总结出网页主题
![get_https工具测试结果](pictures/test_get_https.png)

### mcp tools 调用测试——文生图工具为例：成功生成符合提示词描述的图片
![文生图工具测试结果](pictures/test_text_to_image.png)


![生成图片示例](pictures/the_gen_girl.png)

### skill测试——阅读代码并画流程图技能为例：成功生成了agent.py的流程图
![skill测试——阅读代码并画流程图技能为例](pictures/test_code2draw.png)
![生成流程图示例](pictures/the_gen_pt.jpg)
  

## 📍 关键文件路径
- [agent.py](agent.py) - 基础版本智能体
- [agent-plus.py](agent-plus.py) - 增强版本智能体  
- [agent-claudecode.py](agent-claudecode.py) - ClaudeCode版本智能体
- [mcp_tool_loader.py](mcp_tool_loader.py) - MCP client 用于支持调用外部工具
- [tests/test_agent.py](tests/test_agent.py) - 测试套件
- [README_CN.md](README_CN.md) - 中文文档
- [requirements.txt](requirements.txt) - 依赖配置

