"""
SessionEnd Hook（v10）：/exit 时静默生成会话名称。
直接调用 LLM API（非 claude -p），原位修改 .jsonl。
若 namingStatus=="user_confirmed"（/bye 已确认），则跳过。
"""
import json
import sys
import os
import datetime
import traceback
import re

sys.stdout.reconfigure(encoding='utf-8')

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(_SKILL_DIR, "logs", "session-namer.log")
META_PATH = os.path.join(_SKILL_DIR, "session-meta.json")
CONFIG_PATH = os.path.join(_SKILL_DIR, "session-namer-config.json")

PROMPT_FRAGMENTS = ["根据以下对话内容", "生成一个简洁的", "会话名称", "不超过20个字", "直接输出名称"]


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().isoformat()}] {msg}\n")


def read_meta():
    if os.path.exists(META_PATH):
        try:
            with open(META_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"Error reading meta: {e}")
    return {"sessions": {}}


def write_meta(meta):
    tmp = META_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, META_PATH)


def read_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


# ── 上下文提取 ──────────────────────────────────────────────

_SKIP_TYPES = frozenset([
    "custom-title", "agent-name", "queue-operation", "permission-mode",
    "file-history-snapshot", "system", "file-snapshot", "attachment",
])

_NOISE_PREFIXES = (
    "<local-command-", "<command-", "<command-name>",
    "<system-reminder>", "Caveat:", "[Request interrupted",
    "No response requested", "Tool loaded",
)


def _is_noise(text):
    if not text:
        return True
    t = text.strip()
    if len(t) < 3:
        return True
    return any(p in t[:120] for p in _NOISE_PREFIXES)


def _extract_user_text(content):
    """提取用户消息的完整文本（限制 500 字）。"""
    if isinstance(content, str):
        return content.strip()[:500]
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "").strip()
                if t:
                    parts.append(t)
        return "\n".join(parts)[:500]
    return ""


def _extract_assistant_summary(content):
    """只提取助手的总结性文字，跳过工具调用。返回 (text, tool_info)。

    - text: 助手的文字回复（不含工具调用细节）
    - tool_info: 工具调用摘要列表，如 ['Edit session-namer.py', 'Read config.json']
    """
    if isinstance(content, str):
        return content.strip()[:400], []

    if not isinstance(content, list):
        return "", []

    text_parts = []
    tool_info = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")

        if btype == "text":
            t = block.get("text", "").strip()
            if t:
                text_parts.append(t)

        elif btype == "tool_use":
            tool_name = block.get("name", "")
            inp = block.get("input", {})
            summary = _summarize_tool_call(tool_name, inp)
            if summary:
                tool_info.append(summary)

    text = "\n".join(text_parts).strip()[:400]
    return text, tool_info


def _summarize_tool_call(tool_name, inp):
    """从工具调用参数中提取文件名和操作摘要。"""
    if not isinstance(inp, dict):
        return None

    # 提取文件路径
    fpath = inp.get("file_path") or inp.get("path") or ""
    fname = os.path.basename(fpath) if fpath else ""

    if tool_name == "Edit":
        old = inp.get("old_string", "")
        hint = f"改 {old[:30]}" if old else ""
        return f"Edit {fname} {hint}".strip()
    elif tool_name == "Write":
        return f"Write {fname}"
    elif tool_name == "Read":
        return f"Read {fname}"
    elif tool_name == "Bash":
        cmd = inp.get("command", "")
        desc = inp.get("description", "")
        label = desc if desc else cmd[:40]
        return f"Bash: {label}"
    elif tool_name in ("Grep", "Glob"):
        pattern = inp.get("pattern", "")
        return f"{tool_name} '{pattern}'"
    return None


def extract_meaningful_messages(transcript_path, max_count=30):
    """提取会话上下文：开头 3 条 + 最后 N 条，总预算 ~4000 字。"""
    all_entries = []
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")
                if entry_type in _SKIP_TYPES:
                    continue

                if entry_type == "summary":
                    summary_text = entry.get("summary", "")
                    if summary_text:
                        all_entries.append(("[摘要]", summary_text.strip()[:300], []))
                    continue

                msg = entry.get("message")
                if not isinstance(msg, dict):
                    continue

                role = msg.get("role", "")
                content = msg.get("content", "")

                if role == "user":
                    text = _extract_user_text(content)
                    if text and not _is_noise(text):
                        all_entries.append(("用户", text, []))
                elif role == "assistant":
                    text, tool_info = _extract_assistant_summary(content)
                    if text or tool_info:
                        all_entries.append(("助手", text, tool_info))

    except Exception as e:
        log(f"extract_meaningful_messages error: {e}")

    if not all_entries:
        return []

    # 取开头 3 条（话题建立）+ 最后 max_count 条（近期活动）
    head_count = 3
    if len(all_entries) <= head_count + max_count:
        selected = all_entries
    else:
        selected = all_entries[:head_count] + all_entries[-max_count:]

    # 格式化，总字数预算 ~4000
    result = []
    total_chars = 0
    budget = 4000

    for role, text, tool_info in selected:
        parts = []
        if text:
            parts.append(text)
        if tool_info:
            # 最多保留 5 个工具摘要
            for ti in tool_info[:5]:
                parts.append(f"[{ti}]")

        line_text = f"{role}: " + " | ".join(parts)
        if total_chars + len(line_text) > budget:
            break
        result.append(line_text)
        total_chars += len(line_text)

    return result


def format_context(messages):
    return "\n".join(messages)


# ── LLM API 调用 ──────────────────────────────────────────

def generate_name_api(context, config, timeout=15):
    try:
        import urllib.request
        import urllib.error

        url = f"{config['api_base'].rstrip('/')}/v1/messages"
        prompt = f"根据以下对话内容，生成一个简洁的中文会话名称，不超过20个字，直接输出名称，不加引号和解释：\n\n{context}"

        payload = json.dumps({
            "model": config.get("model", "glm-5"),
            "max_tokens": config.get("max_tokens", 100),
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers={
            "x-api-key": config["api_key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if "content" in data and len(data["content"]) > 0:
            name = data["content"][0].get("text", "").strip()
            name = name.split("\n")[0]
            name = name.strip('"\'""\'\'「」【】<>*·`')[:30]
            return name if name else None, None

        log(f"API response missing content: {json.dumps(data, ensure_ascii=False)[:200]}")
        return None, "api_error"

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        if e.code == 400 and ("too long" in body.lower() or "context" in body.lower() or "token" in body.lower()):
            log(f"API context too long (HTTP {e.code}): {body}")
            return None, "context_too_long"
        log(f"API HTTP error {e.code}: {body}")
        return None, "api_error"
    except Exception as e:
        log(f"API call failed: {e}")
        return None, "api_error"


def is_prompt_pollution(name):
    if not name:
        return True
    hits = sum(1 for f in PROMPT_FRAGMENTS if f in name)
    return hits >= 2


# ── .jsonl 清理并写入终态标题 ────────────────────────────

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
    # Windows 下 Claude Code 退出时可能短暂持有文件锁，重试几次
    import time
    for attempt in range(5):
        try:
            os.replace(tmp, transcript_path)
            return True
        except PermissionError:
            if attempt < 4:
                time.sleep(0.5)
            else:
                raise
    return True


def detect_workspace(transcript_path):
    return os.path.basename(os.path.dirname(transcript_path))


# ── 主流程 ──────────────────────────────────────────────

def main():
    try:
        raw = sys.stdin.read()
        log(f"stdin: {raw[:300]}")
        data = json.loads(raw)

        session_id = data.get("session_id", "")
        transcript_path = data.get("transcript_path", "")
        cwd = data.get("cwd", "")

        if not session_id or not transcript_path:
            log("ERROR: missing session_id or transcript_path")
            return

        if not os.path.exists(transcript_path):
            log(f"ERROR: transcript not found: {transcript_path}")
            return

        log(f"Processing session: {session_id}")

        meta = read_meta()
        sm = meta.get("sessions", {}).get(session_id, {})

        if sm.get("namingStatus") == "user_confirmed":
            confirmed_name = sm.get("autoName", "")
            if confirmed_name:
                modify_title_in_jsonl(transcript_path, session_id, confirmed_name)
                log(f"namingStatus=user_confirmed, wrote final title: {confirmed_name}")
            else:
                log("namingStatus=user_confirmed but no name stored, skipping")
            return

        config = read_config()
        if not config or not config.get("api_key"):
            log("No API config, skipping auto-naming")
            return

        name = None
        for max_count in (30, 20, 10):
            messages = extract_meaningful_messages(transcript_path, max_count)
            if not messages:
                log("No meaningful messages found")
                return

            context = format_context(messages)
            log(f"Calling API with {len(messages)} messages, {len(context)} chars")

            name, err = generate_name_api(context, config)

            if name and not is_prompt_pollution(name):
                log(f"API returned name: {name}")
                break
            if name and is_prompt_pollution(name):
                log(f"Prompt pollution detected, discarding: {name}")
                name = None
                break
            if err == "context_too_long":
                log(f"Context too long with {max_count} messages, retrying with fewer")
                continue
            log(f"API failed with error: {err}")
            break

        if not name:
            log("Failed to generate valid name")
            return

        modify_title_in_jsonl(transcript_path, session_id, name)
        log(f"Modified .jsonl with name: {name}")

        workspace_key = detect_workspace(transcript_path)
        meta.setdefault("sessions", {})[session_id] = {
            "autoName": name,
            "namingStatus": "auto",
            "lastNamedMsgCount": 0,
            "workspace": cwd,
            "workspaceKey": workspace_key,
            "namedAt": datetime.datetime.now().isoformat(),
        }
        write_meta(meta)
        log("Updated session-meta.json")

    except Exception:
        log(f"FATAL: {traceback.format_exc()}")
        sys.exit(0)


if __name__ == "__main__":
    main()
