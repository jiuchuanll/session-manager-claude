---
description: 退出前交互式命名当前会话
---

请执行 session-manager-claude 技能中的 /bye 流程：

**步骤 1：检查现有名称**

```bash
python "C:/Users/m/.claude/skills/session-manager-claude/scripts/session-rename.py" --current-dir "$CWD" --check
```

解析返回的 JSON，根据 namingStatus：
- `user_confirmed`：告知用户当前名称为「XXX」，询问是否要修改
- `auto`：告知用户当前自动生成的名称为「XXX」，建议确认或修改
- `none`：直接进入步骤 2

**步骤 2：生成建议名称**

根据当前对话的完整上下文，生成一个简洁的中文会话名称（不超过 20 个字）。要求：
- 概括本次对话的主要话题和成果
- 只基于有效内容（用户的问题/需求 + 你的结论/方案）
- 排除代码细节、工具调用过程、思考过程、系统消息

**步骤 3：用户确认**

展示：「建议会话名称：「XXX」，确认使用？」

等待用户回应：
- 说「确认」「好」「OK」→ 使用建议名称，进入步骤 4
- 输入新名称 → 使用用户提供的名称，进入步骤 4
- 说「取消」「算了」「跳过」→ 不修改，提示可直接 /exit 退出，结束

**步骤 4：保存名称**

```bash
python "C:/Users/m/.claude/skills/session-manager-claude/scripts/session-rename.py" --current-dir "$CWD" --name "最终确定的名称"
```

**步骤 5：完成提示**

告知用户：「会话名称已保存为「XXX」。可以继续对话，或输入 /exit 退出。」
