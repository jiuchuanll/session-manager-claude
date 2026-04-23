---
name: session-manager-claude
description: Claude Code 会话管理工具。列出会话、重命名会话、确认自动命名、清理旧会话、退出前命名。当用户提到「会话列表」「列出会话」「session list」「重命名会话」「rename session」「确认会话」「清理会话」「删除会话」「session clean」「会话管理」「bye」「退出命名」「/bye」时触发。
---

# Session Manager for Claude Code

你是一个会话管理助手。通过以下 Python 脚本帮助用户管理 Claude Code 的会话。

## 主动确认行为

当你看到 SessionStart hook 输出 `[session-manager]` 开头的待确认提醒时，**必须主动向用户询问**：

1. 列出待确认的会话名称
2. 逐个询问用户是否确认，例如："上个会话自动命名为「XXX」，是否合适？"
3. 用户说「确认」「可以」「OK」→ 执行 `session-rename.py --confirm-latest`
4. 用户说「改成 YYY」→ 执行 `session-rename.py --id <前缀> --name "YYY"`
5. 用户说「跳过」或「不管」→ 不执行，下次启动仍会提醒

## 首次使用配置

当用户首次使用或需要配置自动命名功能时，引导用户运行配置命令：

```bash
# 使用预设（推荐）
python "~/.claude/skills/session-manager-claude/scripts/session-namer.py" --setup --preset zhipu-flash --api-key YOUR_KEY

# 或自定义
python "~/.claude/skills/session-manager-claude/scripts/session-namer.py" --setup --api-base https://api.example.com/v1 --api-key YOUR_KEY --model model-name
```

可用预设：`zhipu-flash`、`zhipu-glm5`、`deepseek`、`qwen`、`openai`、`anthropic`

用 `--presets` 查看所有预设详情：
```bash
python "~/.claude/skills/session-manager-claude/scripts/session-namer.py" --presets
```

配置保存在 `~/.claude/session-namer-config.json`，不会上传或共享。

如果用户没有配置 API Key，自动命名会回退到取最后一条用户消息的前 30 字。

## 可用操作

### 1. 列出会话

当用户说「会话列表」「列出会话」「show sessions」「session list」时：

```bash
python "~/.claude/skills/session-manager-claude/scripts/session-list.py" --workspace current --json
```

如果用户要求「所有会话」「全部工作区」「all sessions」：

```bash
python "~/.claude/skills/session-manager-claude/scripts/session-list.py" --workspace all --json
```

**展示格式**：将 JSON 结果格式化为 Markdown 表格，必须包含以下列（缺一不可）：

`| ID前缀 | 会话名称 | 消息数 | 确认状态 | 最后修改 |`

各列取值规则：
- **ID前缀**：`sessionId` 前 8 位，反引号包裹，供后续操作引用
- **会话名称**：优先 `customTitle`，其次 `autoName`，再次 `firstMessage` 前 20 字，都没有则显示 `(未命名)`
- **消息数**：`messageCount`
- **确认状态**：根据 `namingStatus` + `customTitle` 综合判断：
  - `user_confirmed` → ✅已确认
  - `auto` → 🤖自动命名
  - `renamed` → ✏️已重命名
  - `none` 且有 customTitle → ✅已确认
  - `none` 且无 customTitle → ⚪未命名
- **最后修改**：`lastModified` 取日期部分（YYYY-MM-DD），省略时间

### 2. 确认最近的自动命名

当用户说「确认」「确认会话名称」「confirm session name」时：

```bash
python "~/.claude/skills/session-manager-claude/scripts/session-rename.py" --confirm-latest
```

### 3. 重命名会话

当用户说「重命名会话 XXX 为 YYY」「rename session XXX to YYY」时：

提取 ID 前缀和新名称，执行：

```bash
python "~/.claude/skills/session-manager-claude/scripts/session-rename.py" --id "XXX" --name "YYY"
```

重命名成功后提示用户：新名称将在下次 `claude -r` 时生效。

### 4. /bye — 退出前交互式命名

当用户输入 `/bye` 或说「bye」「退出命名」时，执行以下流程：

**步骤 1：检查现有名称**

```bash
python "~/.claude/skills/session-manager-claude/scripts/session-rename.py" --current-dir "当前工作目录的绝对路径" --check
```

解析返回的 JSON：
- 如果 `namingStatus` 为 `user_confirmed`：告知用户当前名称，询问是否修改
- 如果 `namingStatus` 为 `auto`：告知用户有自动生成的名称，建议确认或修改
- 如果 `namingStatus` 为 `none`：进入步骤 2

**步骤 2：生成建议名称**

根据当前对话的完整上下文，生成一个简洁的中文会话名称（不超过 20 个字）。

生成要求：
- 概括本次对话的主要话题和成果
- 只基于有效内容（用户的问题/需求 + 你的结论/方案）
- 排除：代码细节、工具调用过程、思考过程、系统消息

**步骤 3：用户确认**

展示建议名称，等待用户回应：
- 用户说「确认」「好」「OK」或直接回车 → 使用建议名称
- 用户输入新名称 → 使用用户提供的名称
- 用户说「取消」「算了」「跳过」→ 不修改，提示可直接 `/exit` 退出

**步骤 4：保存名称**

```bash
python "~/.claude/skills/session-manager-claude/scripts/session-rename.py" --current-dir "当前工作目录的绝对路径" --name "最终确定的名称"
```

**步骤 5：完成提示**

保存成功后告知用户：「会话名称已保存为「XXX」。可以继续对话，或输入 /exit 退出。」

**注意**：
- /bye 保存的名称标记为 `user_confirmed`，后续 `/exit` 时 SessionEnd hook 不会覆盖
- 用户可多次使用 /bye 更新名称，以最后一次为准
- 如果用户忘记 /bye 直接 /exit，SessionEnd hook 会自动生成名称作为兜底

### 5. 清理会话

当用户说「清理会话」「删除旧会话」「session clean」时：

**第一步**：列出候选

```bash
python "~/.claude/skills/session-manager-claude/scripts/session-clean.py" --list
```

将结果格式化展示，包含：会话名称、消息数、天数、清理原因。

**第二步**：等待用户确认要删除的会话 ID

**第三步**：用户确认后执行删除

```bash
python "~/.claude/skills/session-manager-claude/scripts/session-clean.py" --delete <id1> <id2> ...
```

**重要**：清理操作不可逆，必须在用户明确确认后才执行 --delete。

## 注意事项

- 所有脚本输出 JSON 格式，你负责格式化为用户友好的展示
- 会话的自动命名由 SessionEnd hook 完成（兜底），主要命名通过 /bye 交互完成
- 重命名通过原位修改 .jsonl 文件中的 custom-title 和 agent-name 实现
- 用户可通过 `claude --resume <sessionId>` 恢复任意会话
- API Key 存储在用户本地 `~/.claude/skills/session-manager-claude/session-namer-config.json`，不会上传或共享
