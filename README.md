# session-manager-claude

Claude Code 会话管理 Skill —— 自动命名、列表、重命名、清理。

## 功能

- **自动命名**：会话结束时通过 LLM 自动生成会话名称（SessionEnd Hook）
- **主动确认**：下次会话启动时提醒用户确认或修改自动命名的名称（SessionStart Hook）
- **会话列表**：按工作区查看所有会话，显示名称、消息数、命名状态
- **重命名会话**：通过 Skill 命令重命名任意会话
- **清理旧会话**：识别并安全清理过期会话、碎片会话、孤立目录

## 安装

### 1. 安装

```bash
# 将整个 skill 目录复制到 ~/.claude/skills/
cp -r . ~/.claude/skills/session-manager-claude/
```

### 2. 配置自动命名

首次使用需配置 LLM API（用于自动命名）：

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

配置保存在 skill 目录下的 `session-namer-config.json`，不会上传或共享。

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
            "timeout": 5000
          }
        ]
      }
    ]
  }
}
```

> **Windows 用户**：将 `~` 替换为完整路径，如 `"python \"C:/Users/YOUR_USER/.claude/skills/session-manager-claude/scripts/session-namer.py\""`。

### 4. 验证

```bash
# 测试 API 连接
python ~/.claude/skills/session-manager-claude/scripts/session-namer.py --test

# 查看当前配置
python ~/.claude/skills/session-manager-claude/scripts/session-namer.py --show
```

## 使用

安装完成后，在 Claude Code 中直接用自然语言操作：

- **「会话列表」** — 列出当前工作区的会话
- **「所有会话」** — 列出所有工作区的会话
- **「确认」** — 确认最近一次自动命名
- **「重命名会话 abc 为 新名称」** — 重命名指定会话
- **「清理会话」** — 列出可清理的旧会话

## 工作原理

### 自动命名（SessionEnd Hook）

1. 会话结束 → Hook 接收 `session_id` 和 `transcript_path`
2. 从 `.jsonl` 文件提取对话内容（兼容新旧格式）
3. 调用 LLM API 生成 ≤30 字的中文会话名称
4. 追加 `custom-title` + `agent-name` 到 `.jsonl` 文件（等效 `/rename`）
5. API 失败时回退到取最后一条用户消息的前 30 字

### 主动确认（SessionStart Hook）

1. 新会话启动 → 读取 skill 目录下 `session-meta.json` 中待确认的会话
2. 输出提醒信息，Claude 主动询问用户是否确认
3. 用户确认/修改后更新状态

### 命名状态

| 状态 | 含义 |
|------|------|
| `auto` | LLM 自动命名，待用户确认 |
| `confirmed` | 用户已确认 |
| `renamed` | 用户手动修改过名称 |

## 仓库结构

```
session-manager-claude/
├── SKILL.md                       # Skill 定义
├── README.md
├── config.example.json            # API 配置模板
├── .gitignore
├── scripts/
│   ├── session-namer.py           # SessionEnd hook：自动命名
│   ├── session-start-reminder.py  # SessionStart hook：确认提醒
│   ├── session-list.py            # 会话列表
│   ├── session-rename.py          # 重命名/确认
│   └── session-clean.py           # 清理旧会话
├── session-namer-config.json      # API 配置（自动生成，不提交）
├── session-meta.json              # 会话元数据（自动生成，不提交）
└── logs/                          # 日志（自动生成，不提交）
```

## 要求

- Python 3.8+
- Claude Code CLI
- 任一 OpenAI 兼容 API 或 Anthropic API 的 API Key

## License

MIT
