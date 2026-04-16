"""
会话清理脚本：识别可清理的会话，安全删除。
用法：
  python session-clean.py --list                    # 列出候选
  python session-clean.py --delete id1 id2 ...      # 删除指定会话
"""
import json
import os
import sys
import argparse
import datetime
import shutil

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
META_PATH = os.path.expanduser("~/.claude/session-meta.json")

DAYS_INACTIVE = 90
MIN_MESSAGES = 3
DAYS_PENDING = 30


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


def _iter_messages(jsonl_path):
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
                yield entry
    except Exception:
        return


def count_messages(jsonl_path):
    """统计有意义的用户/助手消息数"""
    count = 0
    for entry in _iter_messages(jsonl_path):
        etype = entry.get("type", "")
        if etype not in ("user", "assistant"):
            continue
        msg = entry.get("message", {})
        text = _extract_text(msg.get("content", "")) if isinstance(msg, dict) else ""
        if not text or _is_system(text):
            continue
        count += 1
    return count


def get_display_name(jsonl_path):
    """获取会话显示名"""
    custom_title = None
    first_msg = None
    for entry in _iter_messages(jsonl_path):
        etype = entry.get("type", "")
        if etype == "custom-title":
            custom_title = entry.get("customTitle", "")
            continue
        if etype != "user" or first_msg is not None:
            continue
        msg = entry.get("message", {})
        text = _extract_text(msg.get("content", "")) if isinstance(msg, dict) else ""
        if text and not _is_system(text):
            first_msg = text.strip()[:60]
    return custom_title or first_msg or "(未命名)"


def list_candidates():
    """列出所有清理候选"""
    now = datetime.datetime.now()
    meta = read_meta()
    candidates = []

    if not os.path.exists(PROJECTS_DIR):
        return candidates

    for workspace_key in os.listdir(PROJECTS_DIR):
        workspace_path = os.path.join(PROJECTS_DIR, workspace_key)
        if not os.path.isdir(workspace_path):
            continue

        for fname in os.listdir(workspace_path):
            if not fname.endswith(".jsonl"):
                continue

            jsonl_path = os.path.join(workspace_path, fname)
            session_id = fname.replace(".jsonl", "")
            file_stat = os.stat(jsonl_path)
            last_modified = datetime.datetime.fromtimestamp(file_stat.st_mtime)
            days_old = (now - last_modified).days

            reasons = []

            # 条件1：超过 90 天未活动
            if days_old > DAYS_INACTIVE:
                reasons.append(f"{days_old}天未活动")

            # 条件2：消息数 < 3
            msg_count = count_messages(jsonl_path)
            if msg_count < MIN_MESSAGES:
                reasons.append(f"仅{msg_count}条消息")

            # 条件3：auto 状态超 30 天未确认
            meta_info = meta.get("sessions", {}).get(session_id, {})
            if meta_info.get("namingStatus") == "auto":
                named_at = meta_info.get("namedAt", "")
                if named_at:
                    try:
                        named_time = datetime.datetime.fromisoformat(named_at)
                        pending_days = (now - named_time).days
                        if pending_days > DAYS_PENDING:
                            reasons.append(f"自动命名{pending_days}天未确认")
                    except Exception:
                        pass

            # 条件4：检查孤立目录
            session_dir = os.path.join(workspace_path, session_id)
            has_dir = os.path.isdir(session_dir)

            if reasons:
                display_name = get_display_name(jsonl_path)
                candidates.append({
                    "sessionId": session_id,
                    "workspaceKey": workspace_key,
                    "displayName": display_name,
                    "messageCount": msg_count,
                    "daysOld": days_old,
                    "hasSubagentDir": has_dir,
                    "reasons": reasons,
                    "jsonlPath": jsonl_path
                })

    # 检查孤立目录（有目录但无 .jsonl）
    for workspace_key in os.listdir(PROJECTS_DIR):
        workspace_path = os.path.join(PROJECTS_DIR, workspace_key)
        if not os.path.isdir(workspace_path):
            continue
        for dname in os.listdir(workspace_path):
            dir_path = os.path.join(workspace_path, dname)
            if not os.path.isdir(dir_path):
                continue
            if dname in ("memory",):
                continue
            jsonl_path = os.path.join(workspace_path, dname + ".jsonl")
            if not os.path.exists(jsonl_path):
                candidates.append({
                    "sessionId": dname,
                    "workspaceKey": workspace_key,
                    "displayName": "(孤立目录)",
                    "messageCount": 0,
                    "daysOld": 0,
                    "hasSubagentDir": True,
                    "reasons": ["孤立目录(无对应.jsonl)"],
                    "jsonlPath": None
                })

    return candidates


def _find_session_files(sid_prefix):
    """根据 ID 前缀查找 .jsonl 文件，返回 (workspace_path, full_id) 列表"""
    matches = []
    for workspace_key in os.listdir(PROJECTS_DIR):
        workspace_path = os.path.join(PROJECTS_DIR, workspace_key)
        if not os.path.isdir(workspace_path):
            continue
        for fname in os.listdir(workspace_path):
            if fname.endswith(".jsonl") and fname.startswith(sid_prefix):
                full_id = fname[:-6]  # 去掉 .jsonl
                matches.append((workspace_path, full_id))
    return matches


def delete_sessions(session_ids):
    """删除指定会话（支持 ID 前缀匹配）"""
    meta = read_meta()
    results = []

    for sid_prefix in session_ids:
        deleted = []
        errors = []

        matches = _find_session_files(sid_prefix)
        if not matches:
            errors.append(f"未找到匹配 {sid_prefix} 的会话文件")

        for workspace_path, full_id in matches:
            jsonl_path = os.path.join(workspace_path, full_id + ".jsonl")
            dir_path = os.path.join(workspace_path, full_id)

            # 删除 .jsonl
            if os.path.exists(jsonl_path):
                try:
                    os.remove(jsonl_path)
                    deleted.append(jsonl_path)
                except Exception as e:
                    errors.append(f"删除 .jsonl 失败: {e}")

            # 删除目录
            if os.path.isdir(dir_path):
                try:
                    shutil.rmtree(dir_path)
                    deleted.append(dir_path)
                except Exception as e:
                    errors.append(f"删除目录失败: {e}")

            # 从 sessions-index.json 移除
            index_path = os.path.join(workspace_path, "sessions-index.json")
            if os.path.exists(index_path):
                try:
                    with open(index_path, "r", encoding="utf-8") as f:
                        entries = json.load(f)
                    original_len = len(entries)
                    entries = [e for e in entries if e.get("sessionId") != full_id]
                    if len(entries) < original_len:
                        tmp = index_path + ".tmp"
                        with open(tmp, "w", encoding="utf-8") as f:
                            json.dump(entries, f, ensure_ascii=False, indent=2)
                        os.replace(tmp, index_path)
                        deleted.append("sessions-index.json entry")
                except Exception as e:
                    errors.append(f"清理 index 失败: {e}")

        # 从 meta 移除
        for full_id in [fid for _, fid in matches]:
            if full_id in meta.get("sessions", {}):
                del meta["sessions"][full_id]
                deleted.append("session-meta.json entry")

        results.append({
            "sessionId": sid_prefix,
            "deleted": deleted,
            "errors": errors
        })

    write_meta(meta)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="List cleanup candidates")
    parser.add_argument("--delete", nargs="+", help="Delete specified session IDs")
    args = parser.parse_args()

    if args.list:
        candidates = list_candidates()
        print(json.dumps(candidates, ensure_ascii=False, indent=2))
    elif args.delete:
        results = delete_sessions(args.delete)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
