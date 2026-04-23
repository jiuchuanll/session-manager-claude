"""
SessionStart Hook（v10）：扫描未确认和未命名的会话，提醒用户确认。
检查 meta 中 status="auto" 的会话，以及当前 workspace 中无 custom-title 的会话。
"""
import json
import sys
import os
import datetime
sys.stdout.reconfigure(encoding='utf-8')

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META_PATH = os.path.join(_SKILL_DIR, "session-meta.json")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

PROMPT_FRAGMENTS = ["根据以下对话内容", "生成一个简洁的", "会话名称", "不超过20个字"]


def is_prompt_pollution(name):
    if not name:
        return True
    return sum(1 for f in PROMPT_FRAGMENTS if f in name) >= 2


def get_pending_from_meta():
    if not os.path.exists(META_PATH):
        return [], []

    try:
        with open(META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return [], []

    sessions = meta.get("sessions", {})
    pending = []
    corrupted = []

    for sid, info in sessions.items():
        status = info.get("namingStatus", "")
        name = info.get("autoName", "")

        if status == "user_confirmed":
            continue

        if is_prompt_pollution(name):
            corrupted.append(sid)
            continue

        if status == "auto" and name:
            pending.append((sid, name, info.get("namedAt", "")))

    pending.sort(key=lambda x: x[2], reverse=True)
    return pending, corrupted


def get_unnamed_sessions(current_sid=""):
    if not os.path.exists(PROJECTS_DIR):
        return []

    if not os.path.exists(META_PATH):
        known_sids = set()
    else:
        try:
            with open(META_PATH, "r", encoding="utf-8") as f:
                meta = json.load(f)
            known_sids = set(meta.get("sessions", {}).keys())
        except Exception:
            known_sids = set()

    unnamed = []
    now = datetime.datetime.now()

    for workspace_key in os.listdir(PROJECTS_DIR):
        workspace_path = os.path.join(PROJECTS_DIR, workspace_key)
        if not os.path.isdir(workspace_path):
            continue

        for fname in os.listdir(workspace_path):
            if not fname.endswith(".jsonl"):
                continue
            sid = fname.replace(".jsonl", "")
            if sid in known_sids or sid == current_sid:
                continue

            fpath = os.path.join(workspace_path, fname)
            has_title = False
            msg_count = 0

            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        raw = line.strip()
                        if not raw:
                            continue
                        try:
                            entry = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(entry, dict):
                            if entry.get("type") == "custom-title":
                                has_title = True
                                break
                            msg = entry.get("message")
                            if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
                                msg_count += 1
            except Exception:
                continue

            if has_title or msg_count < 3:
                continue

            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
            age_days = (now - mtime).days

            unnamed.append((sid, msg_count, age_days))

    unnamed.sort(key=lambda x: x[2])
    return unnamed[:5]


def main():
    try:
        stdin_data = sys.stdin.read()
    except Exception:
        stdin_data = ""

    cwd = ""
    data = {}
    try:
        data = json.loads(stdin_data)
        cwd = data.get("cwd", "")
    except Exception:
        pass

    try:
        current_sid = data.get("session_id", "")
        pending, corrupted = get_pending_from_meta()
        unnamed = get_unnamed_sessions(current_sid)

        if not pending and not corrupted and not unnamed:
            return

        parts = []

        if pending:
            parts.append("[session-manager] 以下会话已自动命名，需要向用户确认:")
            for sid, name, _ in pending[:5]:
                parts.append(f"  {sid[:8]}... -> {name}")

        if corrupted:
            parts.append(f"[session-manager] 有 {len(corrupted)} 个会话命名异常（已自动忽略）")

        if unnamed:
            parts.append("[session-manager] 以下会话尚未命名:")
            for sid, count, days in unnamed:
                parts.append(f"  {sid[:8]}... ({count}条消息, {days}天前)")

        if pending or unnamed:
            parts.append("请向用户确认以上会话名称是否正确。用户可以:")
            parts.append('  1. 说"确认"来确认最近一个')
            parts.append('  2. 说"重命名会话 [ID前缀] 为 [新名称]"来修改')
            parts.append("提示：下次退出前使用 /bye 可以交互式命名当前会话。")

        if parts:
            print("\n".join(parts))

    except Exception:
        pass


if __name__ == "__main__":
    main()
