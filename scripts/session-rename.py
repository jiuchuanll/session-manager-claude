"""
会话重命名脚本：通过 ID 前缀匹配会话，追加 custom-title 到 .jsonl 文件。
用法：
  python session-rename.py --id <前缀> --name "新名称"
  python session-rename.py --confirm-latest   # 确认最近的自动命名
"""
import json
import os
import sys
import argparse
import datetime

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META_PATH = os.path.join(_SKILL_DIR, "session-meta.json")


def read_meta():
    if os.path.exists(META_PATH):
        with open(META_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"sessions": {}}


def write_meta(meta):
    tmp = META_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, META_PATH)


def find_session_file(session_prefix):
    """通过 ID 前缀在所有工作区中查找 .jsonl 文件"""
    matches = []
    if not os.path.exists(PROJECTS_DIR):
        return matches

    for workspace_key in os.listdir(PROJECTS_DIR):
        workspace_path = os.path.join(PROJECTS_DIR, workspace_key)
        if not os.path.isdir(workspace_path):
            continue
        for fname in os.listdir(workspace_path):
            if fname.endswith(".jsonl") and fname.startswith(session_prefix):
                matches.append({
                    "sessionId": fname.replace(".jsonl", ""),
                    "path": os.path.join(workspace_path, fname),
                    "workspaceKey": workspace_key
                })
    return matches


def append_title(jsonl_path, session_id, title):
    """追加 custom-title 和 agent-name"""
    entries = [
        json.dumps({"type": "custom-title", "customTitle": title, "sessionId": session_id}, ensure_ascii=False),
        json.dumps({"type": "agent-name", "agentName": title, "sessionId": session_id}, ensure_ascii=False),
    ]
    with open(jsonl_path, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(entry + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", help="Session ID or prefix")
    parser.add_argument("--name", help="New session name")
    parser.add_argument("--confirm-latest", action="store_true", help="Confirm the latest auto-named session")
    args = parser.parse_args()

    if args.confirm_latest:
        meta = read_meta()
        pending = [
            (sid, info) for sid, info in meta.get("sessions", {}).items()
            if info.get("namingStatus") == "auto"
        ]
        if not pending:
            print(json.dumps({"status": "error", "message": "没有待确认的会话"}, ensure_ascii=False))
            return

        pending.sort(key=lambda x: x[1].get("namedAt", ""), reverse=True)
        sid, info = pending[0]
        info["namingStatus"] = "confirmed"
        write_meta(meta)
        print(json.dumps({
            "status": "ok",
            "action": "confirmed",
            "sessionId": sid,
            "name": info.get("autoName", "")
        }, ensure_ascii=False))
        return

    if not args.id or not args.name:
        print(json.dumps({"status": "error", "message": "需要 --id 和 --name 参数"}, ensure_ascii=False))
        sys.exit(1)

    matches = find_session_file(args.id)

    if len(matches) == 0:
        print(json.dumps({"status": "error", "message": f"未找到匹配 '{args.id}' 的会话"}, ensure_ascii=False))
        sys.exit(1)

    if len(matches) > 1:
        ids = [m["sessionId"][:12] for m in matches]
        print(json.dumps({
            "status": "error",
            "message": f"匹配到多个会话，请提供更长的前缀: {ids}"
        }, ensure_ascii=False))
        sys.exit(1)

    match = matches[0]
    append_title(match["path"], match["sessionId"], args.name)

    # 更新 meta
    meta = read_meta()
    if match["sessionId"] in meta.get("sessions", {}):
        meta["sessions"][match["sessionId"]]["namingStatus"] = "renamed"
        meta["sessions"][match["sessionId"]]["customName"] = args.name
    else:
        meta["sessions"][match["sessionId"]] = {
            "autoName": args.name,
            "namingStatus": "renamed",
            "workspace": "",
            "workspaceKey": match["workspaceKey"],
            "namedAt": datetime.datetime.now().isoformat()
        }
    write_meta(meta)

    print(json.dumps({
        "status": "ok",
        "action": "renamed",
        "sessionId": match["sessionId"],
        "name": args.name
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
