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

def _extract_text_only(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p)
    return ""


def _is_noise(text):
    if not text:
        return True
    t = text.strip()
    if len(t) < 3:
        return True
    noise_prefixes = (
        "<local-command-", "<command-", "<command-name>",
        "<system-reminder>", "Caveat:", "[Request interrupted",
        "No response requested", "Tool loaded",
    )
    for p in noise_prefixes:
        if p in t[:120]:
            return True
    return False


def _strip_code_blocks(text):
    return re.sub(r'```[\s\S]*?```', '[代码]', text)


def extract_meaningful_messages(transcript_path, max_count=30):
    all_messages = []
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
                if entry_type in ("custom-title", "agent-name", "queue-operation",
                                  "permission-mode", "file-history-snapshot",
                                  "system", "file-snapshot", "attachment"):
                    continue

                if entry_type == "summary":
                    summary_text = entry.get("summary", "")
                    if summary_text:
                        all_messages.append(f"[摘要]: {summary_text.strip()[:300]}")
                    continue

                msg = entry.get("message")
                if not isinstance(msg, dict):
                    continue

                role = msg.get("role", "")
                text = _extract_text_only(msg.get("content", ""))
                if not text or _is_noise(text):
                    continue

                text = _strip_code_blocks(text)
                text = text.strip()[:200]

                if role == "user":
                    all_messages.append(f"用户: {text}")
                elif role == "assistant":
                    all_messages.append(f"助手: {text}")
    except Exception as e:
        log(f"extract_meaningful_messages error: {e}")

    return all_messages[-max_count:] if all_messages else []


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
