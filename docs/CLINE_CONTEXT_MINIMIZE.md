# Cline 会话启动上下文最小化方案

> 来源：`docs/sessions/` 实测数据提炼（2026-08-16 ~ 08-17 三轮优化会话）
> 适用：所有使用 **VS Code + Cline 扩展** 的开发项目

---

## 一、上下文消耗的构成（先认清大头）

实测数据（EchoHymn 项目，Cline + DeepSeek API）：

| 节点 | 上下文累计 | 增量 | 说明 |
| --- | --- | --- | --- |
| 会话开始（7 个 MCP 常驻） | ~114K~130K | — | **系统提示一次性注入，还没干任何事** |
| 读 `SESSION_SUMMARY.md`（128 行） | 114,734 | +0.7K | 文件本身很小 |
| 读 `UI_CONFIRMATION.md`（161 行） | 117,512 | +2.8K | 全文读取 |
| `git log --oneline -15` | 120,839 | +3.3K | 提交历史 |

**结论：简单提问就消耗 122K 的根因不是工具使用，而是 MCP 服务器工具定义的常驻注入（约 100K+，占 94%）。** 文件读取合计仅约 8K。

MCP 工具定义开销排行：

- `powerpoint`（100+ 工具）、`playwright`（40+）、`xmind`（40+）、`web-bridge` → 每个都是几万 token 的 JSON Schema
- `fetch`、`memory`、`sequential-thinking` → 轻量，合计约 1~2K

---

## 二、优化方案（按收益从大到小排序）

### 方案 1：精简 MCP 常驻服务器（收益最大，-100K）

**只保留轻量常驻，其余 `disabled: true`。**

推荐常驻白名单：

- `fetch`（网页抓取，按需使用频率高）
- `memory`（知识图谱记忆，轻量）

推荐禁用：

- `playwright` / `xmind` / `powerpoint` / `sequential-thinking` / `web-bridge` 及其他重型服务器

**关键操作（VS Code 扩展）**：

1. 打开 MCP 配置文件：`%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`（**VS Code 扩展只读这一份**，CLI 的 `~/.cline/` 副本不生效，避免维护两份）
2. 将不用的服务器设为 `"disabled": true`
3. **重启 Cline / 重开新会话生效**（旧会话工具列表是启动时快照，不会动态移除）
4. 需要用到时，在 Cline 的 MCP 管理面板一键启用，用完再禁用——`disabled` 仅代表不注入工具定义，配置完整保留

**实测效果**：7 个常驻 → 2 个常驻，会话基础上下文 130K → 33K（重启后首条即 33K）。

### 方案 2：启动动作最小化（-6K）

把「新会话启动流程」固化进规则文件，让 Cline 只读必要内容：

```markdown
## 新会话启动流程（续接开发）

新会话开始时按以下顺序加载上下文（目的：最小化消耗）：

1. 读 `<项目>/docs/SESSION_SUMMARY.md`（开发总结）——按需全文或关键章节
2. 读 `<项目>/docs/UI_CONFIRMATION.md` 的「最终定稿」章节（可按需读全文）
3. 执行 `git log --oneline -15` 查看提交历史

**禁止**：
- 不要重复读取全局 `.clinerules` 与项目 `.clinerules`（系统均已自动注入）
- 不要遍历用户配置目录（如 `~/.cline`）
- 不要读取 `.mcp.json`（已有 MCP 工具列表即可）
- 不要未经授权扫描整个仓库目录树
```

**实测效果**：完整启动流程（三步 + 规则注入）仅 **~22K tokens（2.2%）**，剩余上下文充足可开发。

### 方案 3：规则文件职责分离（防止跨项目污染）

| 文件 | 职责 | 内容示例 |
| --- | --- | --- |
| 全局 `C:\Users\<用户>\.clinerules` | **通用原则**（所有项目生效） | MCP 精简指引、禁止遍历的目录、防中断策略、公共 API key、git 安全守则 |
| 项目级 `<项目>/.clinerules` | **项目专属**（仅本项目） | 项目启动路径、架构、构建命令、发布机制、遗留任务 |

**铁律**：全局规则里**绝不写项目专属路径**（如 `读 docs/SESSION_SUMMARY.md`），否则所有项目的新会话都会去读不存在的路径、白耗上下文。

### 方案 4：大文件按需读取（-2~3K）

- 长文档只读**关键章节**（如 `UI_CONFIRMATION.md` 161 行只用读第 3 轮定稿章节，省 2.8K）
- 用 `start_line` / `end_line` 限定读取范围，不整文件读入
- 定期把长期积累的开发决策压缩进 `SESSION_SUMMARY.md`，避免新旧文档重复叠加

### 方案 5：会话日志机制（间接收益）

在项目 `.clinerules` 固化「会话日志强制流程」：

- 每个会话在 `docs/sessions/YYYY-MM-DD_HH-MM-SS.md` 记录：提问 / 解决思路 / 最终结果
- 结果必须含三要素：**改了什么文件 / 验证了什么 / 遗留什么**
- 日志随代码 `git add docs/sessions/` 提交

**价值**：新会话通过 `SESSION_SUMMARY.md` + 会话日志即可完整续接，无需回溯全部历史对话；中断的会话也能记录已完成步骤与未完成事项。

---

## 三、EchoHymn 落地现状（已验证）

| 项目 | 状态 |
| --- | --- |
| MCP 常驻 | 仅 `fetch` + `memory` 启用，其余 disabled（globalStorage 唯一配置） |
| 启动三步 | 读 SESSION_SUMMARY → 读 UI_CONFIRMATION 最终定稿章节 → git log -15 |
| 规则职责 | 全局只放通用原则；项目专属路径全在项目 `.clinerules` |
| 实测启动消耗 | **~22K tokens**（约为优化前 1/6） |
| 会话日志 | 已建立 `docs/sessions/` 目录 + 模板 + 强制流程 |

---

## 四、另一台设备的启动提示词（可直接套用）

> 适用：新设备 / 新项目，VS Code + Cline 扩展，希望启动阶段消耗最少的上下文。

### 4.1 第一步：先配置规则文件（一次性，之后每会话自动生效）

**全局规则** `C:\Users\<你的用户名>\.clinerules`：

```markdown
# 全局规则（所有项目通用）

## MCP 精简（上下文最大头）
- 只保留轻量常驻服务器（fetch / memory），其余一律 disabled: true
- MCP 配置只维护 VS Code 扩展那份：
  %APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json
- 需要重型服务器（playwright/xmind/powerpoint 等）时在 Cline MCP 面板按需启用，用完禁用

## 新会话启动流程（通用）
1. 读取项目级 .clinerules，按其中指引执行
2. 执行 git log --oneline -15 查看提交历史
3. 汇总已加载的关键上下文，等待用户实际指令

## 禁止事项（防止白耗上下文）
- 禁止重复读取全局/项目 .clinerules（系统已自动注入）
- 禁止未授权遍历用户配置目录（如 ~/.cline）与整个仓库目录树
- 禁止整文件读取长文档；优先按 start_line/end_line 读关键章节
- 禁止在启动阶段调用 MCP 工具或读取 .mcp.json
```

**项目规则** `<你的项目>/.clinerules`：

```markdown
# <项目名> 项目规则

## 新会话启动流程（续接开发）
1. 读 `docs/SESSION_SUMMARY.md`（或同等续接文档）——按需全文或关键章节
2. 读 `docs/UI_CONFIRMATION.md` 的「最终设计定稿」章节（可按需读全文）
3. 执行 `git log --oneline -15` 查看提交历史

## 重要须知（项目）
- <目标平台 / 构建命令 / 发布机制等关键信息，控制在 10 行内>

## 会话日志强制流程
- 每会话在 docs/sessions/ 下创建 YYYY-MM-DD_HH-MM-SS.md
- 记录：提问 / 解决思路 / 最终结果（改了什么 / 验证了什么 / 遗留什么）
- 日志随代码一起 git 提交
```

### 4.2 第二步：新会话的**首条**提示词（复制即用）

```
这是一个全新 Cline 会话。请按最小上下文方式完成启动，严格执行以下指令，不要多做任何事：

1. 只读取这三个内容：
   a. docs/SESSION_SUMMARY.md（若存在，读全文；目标 ≤ 300 行时）
   b. docs/UI_CONFIRMATION.md 中「最终设计定稿」章节（用 start_line/end_line 只读该章节，不读全文）
   c. 执行 git log --oneline -15
2. 被禁止的操作（不要做）：
   - 不读 .clinerules / .mcp.json / 全局规则（系统已自动注入）
   - 不遍历用户配置目录、不扫描整个仓库目录树
   - 不调用任何 MCP 工具
   - 不读取其他任何文件
3. 完成后，用不超过 5 行汇报：项目当前状态、最近提交、遗留任务，然后停止，等待我的实际指令。
```

### 4.3 可选：自检清单（启动完成后核对）

| 检查项 | 目标 |
| --- | --- |
| 会话开始的 Context Window Usage | ≤ 25K（若 > 30K 说明有重型 MCP 未禁用） |
| MCP 工具列表 | 仅 fetch / memory（或项目实际需要的最小集） |
| 启动动作 | 仅 2~3 次文件读取 + 1 次 git log，无多余操作 |
| 规则注入 | 全局 + 项目 `.clinerules` 均已出现在系统提示（无需再读） |

---

## 五、常见问题

**Q：为什么 MCP 优化后旧会话没变少？**
A：Cline 的工具列表是会话启动时快照。修改 MCP 配置后必须**重开新会话**才生效。

**Q：`disabled: true` 是不是删除了服务器？**
A：不是。配置完整保留，只是不注入工具定义（不占上下文）。在 Cline MCP 面板点击启用即恢复，无需改文件。

**Q：全局和项目 `.clinerules` 都写启动流程，会怎样？**
A：重复加载、浪费 token。正确做法：全局只写「读项目级 .clinerules」，具体路径与步骤全部放项目级。

**Q：项目文档特别长怎么办？**
A：先按章节读取（start_line/end_line），只在真正需要时读全文；同时定期把决策压缩进 SESSION_SUMMARY，让它成为唯一的长文档入口。
