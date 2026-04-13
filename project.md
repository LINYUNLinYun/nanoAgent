# nanoAgent 项目结构说明

## 📁 项目概述
**nanoAgent**是一个使用OpenAI函数调用的最小化AI智能体实现，核心代码约100行。项目提供三个渐进式版本，支持通过自然语言与系统交互。

## 📄 根目录文件

### 1. [agent.py](agent.py) - 基础版本
- **作用**：最简单的AI智能体实现（~100行代码）
- **核心功能**：
  - 定义三个基础工具：`execute_bash`、`read_file`、`write_file`
  - 智能体循环：调用模型 → 执行工具 → 重复（最多5次迭代）
  - 基本错误处理（未知工具、JSON解析错误）
- **使用示例**：`python agent.py "列出当前目录下所有python文件"`

### 2. [agent-plus.py](agent-plus.py) - 增强版本
- **作用**：在基础版上添加高级功能
- **新增功能**：
  - **内存系统**：保存任务历史到`agent_memory.md`
  - **任务规划**：`--plan`参数自动分解复杂任务为3-5步
  - **增强错误处理**：30秒超时控制、异常捕获
- **使用示例**：`python agent-plus.py --plan "统计所有.py文件代码行数"`

### 3. [agent-claudecode.py](agent-claudecode.py) - ClaudeCode版本
- **作用**：最完整的版本，模拟Claude Code工具集
- **功能集**：
  - **完整工具**：`read`、`write`、`edit`、`glob`、`grep`、`bash`、`plan`
  - **规则系统**：从`.agent/rules/`加载行为规则（.md文件）
  - **技能系统**：从`.agent/skills/`加载技能配置（.json文件）
  - **MCP支持**：从`.agent/mcp.json`加载外部工具
  - **嵌套规划**：`plan`工具支持分步执行
- **使用示例**：`python agent-claudecode.py --plan "重构项目结构"`

### 4. 文档文件
- **[README.md](README.md)** / **[README_CN.md](README_CN.md)**：英文/中文项目文档
- **[LICENSE](LICENSE)**：MIT许可证文件
- **[requirements.txt](requirements.txt)**：仅依赖`openai`库
- **[.gitignore](.gitignore)**：忽略Python缓存文件（`__pycache__/`、`*.py[cod]`）

## 📂 目录结构

### [tests/](tests/) - 测试目录
- **[test_agent.py](tests/test_agent.py)**：三个版本的回归测试
  - 工具参数解析测试
  - 未知工具错误处理测试
  - JSON解析错误测试
  - 独立的测试类（基础版、增强版、ClaudeCode版）

### [.git/](.git/) - Git版本控制
- 标准的Git仓库数据目录（配置、对象、引用等）

### [.agent/](.agent/) - ClaudeCode配置目录（动态创建）
- **rules/**：存放规则文件（.md格式），定义智能体行为约束
- **skills/**：存放技能配置文件（.json格式），定义专业能力
- **mcp.json**：MCP（Model Context Protocol）工具配置

## 🔧 版本对比

| 版本 | 工具数量 | 特色功能 | 适用场景 |
|------|----------|----------|----------|
| 基础版 | 3 | bash、read_file、write_file | 学习、简单任务 |
| 增强版 | 3 | 内存、任务规划、错误处理增强 | 复杂任务、需要历史记忆 |
| ClaudeCode版 | 7+ | 完整工具集、规则、技能、MCP | 专业任务、系统集成 |

## 🏗️ 项目架构

### 核心工作流程
```
用户输入 → OpenAI API → 工具调用 → 系统交互 → 结果返回 → 重复直到完成
```

### 工具调用机制
1. 用户提供自然语言任务
2. 模型选择工具和参数
3. 执行工具并获取结果
4. 结果返回模型进行下一步决策
5. 重复直到完成或达到最大迭代次数（默认5次）

### 错误处理
- JSON解析错误 → 友好错误信息
- 未知工具 → 错误提示
- 超时控制 → 30秒限制
- 异常捕获 → 防止崩溃

## 🎯 项目用途

### 主要用途
1. **学习AI智能体开发**：极简实现，适合学习智能体架构
2. **系统自动化**：通过自然语言控制文件系统、执行命令
3. **任务自动化**：文件操作、代码分析、系统管理
4. **原型开发**：快速验证AI智能体概念

### 目标用户
- AI开发者学习智能体架构
- 需要系统自动化的用户
- 希望用自然语言控制系统的用户
- 需要快速原型验证的开发者

## 🔌 扩展性

### 可扩展方向
1. **添加新工具**：在工具列表中定义新函数
2. **集成外部API**：添加API调用工具
3. **自定义规则**：通过规则文件定义行为约束
4. **技能扩展**：通过技能文件添加专业能力
5. **MCP集成**：连接更多外部工具和服务

## 🧪 验证方法
```bash
# 运行测试
python -m pytest tests/test_agent.py

# 测试基础版
python agent.py "列出当前目录文件"

# 测试增强版  
python agent-plus.py "创建test.txt文件"

# 测试ClaudeCode版
python agent-claudecode.py "搜索所有.py文件"
```

## 📍 关键文件路径
- [agent.py](agent.py) - 基础版本智能体
- [agent-plus.py](agent-plus.py) - 增强版本智能体  
- [agent-claudecode.py](agent-claudecode.py) - ClaudeCode版本智能体
- [tests/test_agent.py](tests/test_agent.py) - 测试套件
- [README_CN.md](README_CN.md) - 中文文档
- [requirements.txt](requirements.txt) - 依赖配置

---

**总结**：nanoAgent是一个优秀的教学和原型开发工具，展示了如何用最少代码构建功能完整的AI智能体系统。三个渐进式版本满足不同层次需求，从学习基础概念到构建专业系统。