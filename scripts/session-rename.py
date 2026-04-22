"""
会话重命名脚本：通过 ID 前缀匹配或当前工作目录定位会话，原位修改 .jsonl 文件。
用法：
  python session-rename.py --id <前缀> --name "新名称"
  python session-rename.py --current-dir "D:\\code\\project" --name "新名称"
  python session-rename.py --current-dir "D:\\code\\project" --check
  python session-rename.py --confirm-latest
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
        try:
            with open(META_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"sessions": {}}


def write_meta(meta):
    tmp = META_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, META_PATH)


def cwd_to_workspace_key(cwd):
    key = cwd.replace(":", "-").replace("\\", "-").replace("/", "-")
    if key.startswith("-"):
        key = key[1:]
    return key


def find_session_file(session_prefix):
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


def find_current_session(cwd):
    workspace_key = cwd_to_workspace_key(cwd)
    workspace_path = os.path.join(PROJECTS_DIR, workspace_key)
    if not os.path.isdir(workspace_path):
        return None
    jsonl_files = []
    for fname in os.listdir(workspace_path):
        if fname.endswith(".jsonl"):
            fpath = os.path.join(workspace_path, fname)
            jsonl_files.append((fpath, os.path.getmtime(fpath)))
    if not jsonl_files:
        return None
    jsonl_files.sort(key=lambda x: x[1], reverse=True)
    best = jsonl_files[0][0]
    sid = os.path.basename(best).replace(".jsonl", "")
    return {"sessionId": sid, "path": best, "workspaceKey": workspace_key}


def get_current_title(jsonl_path):
    last_title = None
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and entry.get("type") == "custom-title":
                    last_title = entry.get("customTitle")
    except Exception:
        pass
    return last_title


def modify_title_in_jsonl(transcript_path, session_id, title):
    """删除所有旧的 custom-title / agent-name，在文件末尾追加唯一的终态标题。"""
    output_lines = []

    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.rstrip("\n\r")
            if not raw.strip():
                output_lines.append(raw)
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                output_lines.append(raw)
                continue
            if isinstance(entry, dict) and entry.get("type") in ("custom-title", "agent-name"):
                continue  # 删除所有旧条目
            output_lines.append(raw)

    output_lines.append(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": session_id}, ensure_ascii=False))
    output_lines.append(json.dumps({"type": "agent-name", "agentName": title, "sessionId": session_id}, ensure_ascii=False))

    tmp = transcript_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
        if output_lines and output_lines[-1] != "":
            f.write("\n")
    os.replace(tmp, transcript_path)
    return True


def update_meta_confirmed(meta, session_id, name, workspace_key):
    now = datetime.datetime.now().isoformat()
    meta.setdefault("sessions", {})[session_id] = {
        "autoName": name,
        "namingStatus": "user_confirmed",
        "lastNamedMsgCount": 0,
        "workspace": "",
        "workspaceKey": workspace_key,
        "namedAt": now,
    }
    write_meta(meta)


def handle_check(match):
    meta = read_meta()
    sm = meta.get("sessions", {}).get(match["sessionId"], {})
    current_title = get_current_title(match["path"])
    naming_status = sm.get("namingStatus", "none")
    print(json.dumps({
        "status": "ok",
        "sessionId": match["sessionId"],
        "currentName": current_title or sm.get("autoName"),
        "namingStatus": naming_status,
    }, ensure_ascii=False))


def handle_rename(match, name):
    modify_title_in_jsonl(match["path"], match["sessionId"], name)
    meta = read_meta()
    update_meta_confirmed(meta, match["sessionId"], name, match["workspaceKey"])
    print(json.dumps({
        "status": "ok",
        "action": "renamed",
        "sessionId": match["sessionId"],
        "name": name,
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", help="Session ID or prefix")
    parser.add_argument("--name", help="New session name")
    parser.add_argument("--current-dir", help="Current working directory to find active session")
    parser.add_argument("--check", action="store_true", help="Check current session naming status")
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
        info["namingStatus"] = "user_confirmed"
        write_meta(meta)
        print(json.dumps({
            "status": "ok",
            "action": "confirmed",
            "sessionId": sid,
            "name": info.get("autoName", ""),
        }, ensure_ascii=False))
        return

    if args.current_dir:
        match = find_current_session(args.current_dir)
        if not match:
            print(json.dumps({"status": "error", "message": f"未找到工作目录 '{args.current_dir}' 的活跃会话"}, ensure_ascii=False))
            sys.exit(1)
        if args.check:
            handle_check(match)
            return
        if not args.name:
            print(json.dumps({"status": "error", "message": "需要 --name 参数"}, ensure_ascii=False))
            sys.exit(1)
        handle_rename(match, args.name)
        return

    if args.id:
        if not args.name:
            print(json.dumps({"status": "error", "message": "需要 --name 参数"}, ensure_ascii=False))
            sys.exit(1)
        matches = find_session_file(args.id)
        if len(matches) == 0:
            print(json.dumps({"status": "error", "message": f"未找到匹配 '{args.id}' 的会话"}, ensure_ascii=False))
            sys.exit(1)
        if len(matches) > 1:
            ids = [m["sessionId"][:12] for m in matches]
            print(json.dumps({"status": "error", "message": f"匹配到多个会话，请提供更长的前缀: {ids}"}, ensure_ascii=False))
            sys.exit(1)
        handle_rename(matches[0], args.name)
        return

    print(json.dumps({"status": "error", "message": "需要 --id、--current-dir 或 --confirm-latest 参数"}, ensure_ascii=False))
    sys.exit(1)


if __name__ == "__main__":
    main()
