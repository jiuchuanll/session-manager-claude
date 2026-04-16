---
name: session-manager-claude
description: Claude Code 会话管理工具。列出会话、重命名会话、确认自动命名、清理旧会话、配置自动命名。当用户提到「会话列表」「列出会话」「session list」「重命名会话」「rename session」「确认会话」「清理会话」「删除会话」「session clean」「会话管理」「配置会话命名」「setup session namer」时触发。
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

**展示格式**：将 JSON 结果格式化为表格，包含：会话名称、消息数、最后修改时间、命名状态。

命名状态图标：
- 有 customTitle 且 namingStatus 为 confirmed → ✅已确认
- 有 customTitle 且 namingStatus 为 auto → 🤖自动命名
- 有 customTitle 且 namingStatus 为 renamed → ✏️已重命名
- 无 customTitle → ⚪未命名

每个会话显示 sessionId 的前 8 位用于后续操作引用。

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

### 4. 清理会话

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
- 会话的自动命名由 SessionEnd hook 完成，不在此 Skill 中触发
- 重命名通过追加 custom-title 到 .jsonl 文件实现，等效 Claude Code 原生 /rename 命令
- 用户可通过 `claude --resume <sessionId>` 恢复任意会话
- API Key 存储在用户本地 `~/.claude/session-namer-config.json`，不会上传或共享
