# session-manager-claude

Claude Code 会话管理 Skill —— 三层防护自动命名、交互式命名、列表、重命名、清理。

## 功能

- **/bye 交互式命名**：退出前由 Claude 直接生成会话名称，用户确认后保存（零额外 API 调用）
- **自动命名兜底**：忘记 /bye 直接 /exit 时，SessionEnd Hook 静默调用 LLM API 生成名称
- **启动时确认**：下次会话启动时提醒未确认/未命名的会话，由 Claude 主动询问
- **会话列表**：按工作区查看所有会话，显示名称、消息数、命名状态
- **重命名会话**：通过 ID 前缀重命名任意会话
- **清理旧会话**：识别并安全清理过期会话、碎片会话

## 架构

三层防护，覆盖所有退出场景：

```
层级 1：/bye（主要路径 — 交互式）
  用户输入 /bye → Claude 根据对话上下文生成名称
  → 用户确认/修改 → 保存为 user_confirmed

层级 2：SessionEnd Hook（兜底 — 静默）
  /exit → 检查 meta
  → 已 user_confirmed → 写入确认名称为终态（清理所有中间条目）
  → 未确认 → 直接调用 LLM API → 自动生成 → 标记为 auto

层级 3：SessionStart Hook（最终兜底）
  新会话启动 → 扫描未确认 + 未命名会话
  → Claude 主动询问用户确认/修改
```

| 用户操作 | /bye | SessionEnd | SessionStart | 结果 |
|---------|------|-----------|-------------|------|
| /bye 确认 → /exit | 保存名称 | 写入确认名为终态 | 无待确认 | 完美 |
| /bye 取消 → /exit | 未修改 | 自动生成 | 下次确认 | OK |
| 直接 /exit | — | 自动生成 | 下次确认 | 兜底有效 |
| Ctrl+C 强退 | — | 可能不触发 | 检测未命名 | 最终兜底 |

## 安装

### 1. 复制 Skill 文件

```bash
cp -r . ~/.claude/skills/session-manager-claude/
```

### 2. 配置 LLM API（用于 SessionEnd 自动命名兜底）

```bash
# 使用预设（推荐）
python ~/.claude/skills/session-manager-claude/scripts/session-namer.py --setup --preset zhipu-flash --api-key YOUR_KEY

# 查看所有预设
python ~/.claude/skills/session-manager-claude/scripts/session-namer.py --presets

# 自定义 API
python ~/.claude/skills/session-manager-claude/scripts/session-namer.py --setup --api-base https://api.example.com/v1 --api-key YOUR_KEY --model model-name
```

可用预设：
- `zhipu-flash` — 智谱 GLM-4-Flash（免费）
- `zhipu-glm5` — 智谱 GLM-5
- `deepseek` — DeepSeek
- `qwen` — 通义千问 Qwen-Turbo
- `openai` — OpenAI GPT-4o-mini
- `anthropic` — Anthropic Claude Haiku

> 不配置 API 也可以使用 /bye 交互命名（由 Claude 在对话中直接生成，不需要外部 API）。API 仅用于 SessionEnd 静默兜底。

### 3. 配置 Hooks

在 `~/.claude/settings.json` 中添加：

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python ~/.claude/skills/session-manager-claude/scripts/session-namer.py",
            "timeout": 30000
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python ~/.claude/skills/session-manager-claude/scripts/session-start-reminder.py",
            "timeout": 10000
          }
        ]
      }
    ]
  }
}
```

> **Windows 用户**：将路径替换为完整路径，如 `"python \"C:/Users/YOUR_USER/.claude/skills/session-manager-claude/scripts/session-namer.py\""`。

### 4. 验证

```bash
# 测试 API 连接
python ~/.claude/skills/session-manager-claude/scripts/session-namer.py --test

# 查看当前配置
python ~/.claude/skills/session-manager-claude/scripts/session-namer.py --show
```

## 使用

安装后将 `bye.md` 复制到 `~/.claude/commands/` 目录，即可将 `/bye` 注册为全局命令：

```bash
cp commands/bye.md ~/.claude/commands/bye.md
```

之后在 Claude Code 中：

- **`/bye`** — 退出前交互式命名当前会话（推荐，已注册为 slash command）
- **「会话列表」** — 列出当前工作区的会话
- **「所有会话」** — 列出所有工作区的会话
- **「确认」** — 确认最近一次自动命名
- **「重命名会话 abc 为 新名称」** — 重命名指定会话
- **「清理会话」** — 列出可清理的旧会话

## 工作原理

### /bye 交互式命名（层级 1）

1. 用户输入 `/bye` → Claude 检查当前会话命名状态
2. Claude 根据完整对话上下文生成建议名称（零 API 调用）
3. 用户确认/修改/取消 → 保存名称到 `.jsonl` 并标记为 `user_confirmed`

### 自动命名（层级 2 — SessionEnd Hook）

1. 会话结束（`/exit`）→ 读取 `session-meta.json`
2. 若已 `user_confirmed`：将确认名写为 `.jsonl` 中唯一的终态标题（清理所有中间条目）
3. 若未确认：从 `.jsonl` 提取最近 30 条有效对话（排除代码块、工具调用、系统消息）
4. 直接调用 LLM API 生成名称（非 `claude -p`，3-7s 完成）
5. 上下文过长时自动缩减：30 → 20 → 10 条消息重试
6. 内置 prompt 污染检测，防止 API 返回模板文本作为名称
7. 写入 `.jsonl`（清理所有旧 custom-title/agent-name，末尾追加终态条目）
8. Windows 下自动重试（最多 5 次，间隔 0.5s）以处理文件锁竞争

### 启动确认（层级 3 — SessionStart Hook）

1. 新会话启动 → 扫描 `session-meta.json` 中 `status=auto` 的会话
2. 扫描当前工作区中无 `custom-title` 且消息数 >= 3 的会话
3. Claude 主动列出并询问用户确认/修改

### 命名状态

| 状态 | 含义 | 来源 |
|------|------|------|
| `user_confirmed` | 用户已确认 | /bye 或手动确认 |
| `auto` | 自动命名，待确认 | SessionEnd Hook |
| `renamed` | 用户手动重命名 | 重命名命令 |
| `none` | 未命名 | — |

## 仓库结构

```
session-manager-claude/
├── SKILL.md                       # Skill 定义（含 /bye 流程）
├── README.md
├── config.example.json            # API 配置模板
├── .gitignore
├── scripts/
│   ├── session-namer.py           # SessionEnd hook：静默自动命名（直接 API）
│   ├── session-start-reminder.py  # SessionStart hook：扫描未确认+未命名会话
│   ├── session-list.py            # 会话列表
│   ├── session-rename.py          # 重命名/确认/检查当前会话状态
│   └── session-clean.py           # 清理旧会话
├── session-namer-config.json      # API 配置（自动生成，不提交）
├── session-meta.json              # 会话元数据（自动生成，不提交）
└── logs/                          # 日志（自动生成，不提交）
```

## 平台兼容性

| 平台 | 支持情况 | 注意事项 |
|------|---------|---------|
| **macOS** | ✅ 完全支持 | Hook 配置中使用 `python3` 代替 `python` |
| **Windows** | ✅ 完全支持 | Hook 配置中使用完整路径（见安装说明）；文件锁重试已内置 |
| **Linux** | ✅ 完全支持 | 同 macOS，使用 `python3` |

**macOS / Linux Hook 配置示例：**

```json
{
  "hooks": {
    "SessionEnd": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 ~/.claude/skills/session-manager-claude/scripts/session-namer.py",
        "timeout": 30000
      }]
    }],
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 ~/.claude/skills/session-manager-claude/scripts/session-start-reminder.py",
        "timeout": 10000
      }]
    }]
  }
}
```

## 仓库结构

```
session-manager-claude/
├── SKILL.md                       # Skill 定义（含 /bye 流程）
├── README.md
├── config.example.json            # API 配置模板
├── commands/
│   └── bye.md                     # /bye slash command（复制到 ~/.claude/commands/）
├── .gitignore
├── scripts/
│   ├── session-namer.py           # SessionEnd hook：静默自动命名 + 终态写入
│   ├── session-start-reminder.py  # SessionStart hook：扫描未确认+未命名会话
│   ├── session-list.py            # 会话列表
│   ├── session-rename.py          # 重命名/确认/检查当前会话状态
│   └── session-clean.py           # 清理旧会话
├── session-namer-config.json      # API 配置（自动生成，不提交）
├── session-meta.json              # 会话元数据（自动生成，不提交）
└── logs/                          # 日志（自动生成，不提交）
```

## 要求

- Python 3.8+（macOS/Linux 请使用 `python3`）
- Claude Code CLI
- （可选）LLM API Key — 仅 SessionEnd 兜底需要，/bye 不需要

## License

MIT
