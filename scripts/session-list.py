"""
会话列表脚本：扫描 .jsonl 文件，输出结构化 JSON 供 Claude 格式化展示。
用法：
  python session-list.py --workspace current   # 当前工作区（默认）
  python session-list.py --workspace all        # 所有工作区
"""
import json
import os
import sys
import argparse
import datetime
sys.stdout.reconfigure(encoding='utf-8')

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META_PATH = os.path.join(_SKILL_DIR, "session-meta.json")


def read_meta():
    if os.path.exists(META_PATH):
        with open(META_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"sessions": {}}


SKIP_TYPES = {"custom-title", "agent-name", "queue-operation",
              "permission-mode", "file-history-snapshot", "system",
              "summary", "file-snapshot"}

SYSTEM_PREFIXES = ("<local-command-", "<command-", "<command-name>",
                   "<command-message>", "<command-args>", "<system-reminder>",
                   "Caveat:", "[Request interrupted")


def _extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p)
    return ""


def _is_system(text):
    head = text[:100] if text else ""
    return any(p in head for p in SYSTEM_PREFIXES)


def get_session_info(jsonl_path):
    """从 .jsonl 文件提取会话信息"""
    session_id = os.path.basename(jsonl_path).replace(".jsonl", "")
    file_stat = os.stat(jsonl_path)
    last_modified = datetime.datetime.fromtimestamp(file_stat.st_mtime).isoformat()
    file_size = file_stat.st_size

    custom_title = None
    first_user_msg = None
    message_count = 0

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")

                if entry_type == "custom-title":
                    custom_title = entry.get("customTitle", "")
                    continue

                if entry_type in SKIP_TYPES:
                    continue

                if entry_type not in ("user", "assistant"):
                    continue

                msg = entry.get("message", {})
                text = _extract_text(msg.get("content", "")) if isinstance(msg, dict) else ""
                if not text or _is_system(text):
                    continue

                if entry_type == "user" and first_user_msg is None:
                    first_user_msg = text.strip()[:100]

                message_count += 1
    except Exception:
        pass

    return {
        "sessionId": session_id,
        "customTitle": custom_title,
        "firstMessage": first_user_msg,
        "displayName": custom_title or first_user_msg or "(未命名)",
        "messageCount": message_count,
        "lastModified": last_modified,
        "fileSize": file_size
    }


def get_current_workspace_key():
    """从当前工作目录推断 workspace key"""
    cwd = os.getcwd()
    # 尝试匹配 projects 目录下的 key
    if os.path.exists(PROJECTS_DIR):
        for key in os.listdir(PROJECTS_DIR):
            key_path = os.path.join(PROJECTS_DIR, key)
            if os.path.isdir(key_path):
                # 简单匹配：将 cwd 转为 key 格式进行比较
                cwd_key = cwd.replace(":", "-").replace("\\", "-").replace("/", "-")
                if cwd_key.lower() == key.lower():
                    return key
    # 回退：直接转换
    return cwd.replace(":", "-").replace("\\", "-").replace("/", "-")


def scan_workspace(workspace_path):
    """扫描一个工作区目录下的所有 .jsonl 文件"""
    sessions = []
    if not os.path.isdir(workspace_path):
        return sessions

    for fname in os.listdir(workspace_path):
        if fname.endswith(".jsonl"):
            jsonl_path = os.path.join(workspace_path, fname)
            info = get_session_info(jsonl_path)
            sessions.append(info)

    # 按最后修改时间倒序
    sessions.sort(key=lambda x: x["lastModified"], reverse=True)
    return sessions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="current", choices=["current", "all"])
    parser.add_argument("--json", action="store_true", default=True)
    args = parser.parse_args()

    meta = read_meta()
    result = {}

    if args.workspace == "current":
        workspace_key = get_current_workspace_key()
        workspace_path = os.path.join(PROJECTS_DIR, workspace_key)
        sessions = scan_workspace(workspace_path)
        result[workspace_key] = {
            "path": workspace_path,
            "sessions": sessions
        }
    else:
        # 扫描所有工作区
        if os.path.exists(PROJECTS_DIR):
            for key in sorted(os.listdir(PROJECTS_DIR)):
                workspace_path = os.path.join(PROJECTS_DIR, key)
                if not os.path.isdir(workspace_path):
                    continue
                # 跳过 memory 目录等
                has_jsonl = any(f.endswith(".jsonl") for f in os.listdir(workspace_path))
                if not has_jsonl:
                    continue
                sessions = scan_workspace(workspace_path)
                if sessions:
                    result[key] = {
                        "path": workspace_path,
                        "sessions": sessions
                    }

    # 合并 meta 信息，确保每条会话都有完整字段
    for workspace_key, workspace_data in result.items():
        for session in workspace_data["sessions"]:
            sid = session["sessionId"]
            if sid in meta.get("sessions", {}):
                session["namingStatus"] = meta["sessions"][sid].get("namingStatus", "") or "none"
                session["autoName"] = meta["sessions"][sid].get("autoName", "")
            else:
                session["namingStatus"] = "none"
                session["autoName"] = ""

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
