"""
SessionStart Hook: 提醒用户确认自动命名的会话
读取 session-meta.json，展示最近的待确认会话。
"""
import json
import sys
import os

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META_PATH = os.path.join(_SKILL_DIR, "session-meta.json")


def main():
    try:
        if not os.path.exists(META_PATH):
            return

        with open(META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)

        sessions = meta.get("sessions", {})
        pending = [
            (sid, info) for sid, info in sessions.items()
            if info.get("namingStatus") == "auto"
        ]

        if not pending:
            return

        # 按时间倒序，最多展示 3 个
        pending.sort(key=lambda x: x[1].get("namedAt", ""), reverse=True)
        pending = pending[:3]

        print("[session-manager] 以下会话已自动命名，需要向用户确认:")
        for sid, info in pending:
            name = info.get("autoName", "未知")
            print(f"  {sid[:8]}... -> {name}")
        print("请向用户确认以上会话名称是否正确。用户可以:")
        print('  1. 说"确认"来确认最近一个')
        print('  2. 说"重命名会话 [ID前缀] 为 [新名称]"来修改')

    except Exception:
        pass  # 静默失败，不阻塞启动


if __name__ == "__main__":
    # 消费 stdin（hook 会传入 JSON，必须读完）
    try:
        sys.stdin.read()
    except Exception:
        pass
    main()
