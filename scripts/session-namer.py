"""
SessionEnd Hook: 自动为会话生成名称
通过 LLM 分析对话内容，生成 ≤30 字的中文会话名称，
追加 custom-title + agent-name 到 .jsonl 文件（等效 /rename）。

LLM 配置读取 skill 目录下的 session-namer-config.json，支持任何 OpenAI 兼容 API。
首次使用需运行: python session-namer.py --setup 进行配置。
"""
import json
import sys
import os
import datetime
import traceback
import urllib.request
import urllib.error

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(_SKILL_DIR, "logs", "session-namer.log")
META_PATH = os.path.join(_SKILL_DIR, "session-meta.json")
CONFIG_PATH = os.path.join(_SKILL_DIR, "session-namer-config.json")
MAX_CONTEXT_CHARS = 4000  # 前2000 + 后2000

DEFAULT_CONFIG = {
    "api_base": "",
    "api_key": "",
    "model": "",
    "max_tokens": 100
}

# 预设模板：快速填充 api_base 和 model
PROVIDER_PRESETS = [
    {
        "key": "zhipu-flash",
        "name": "智谱 GLM-4-Flash (免费)",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash"
    },
    {
        "key": "zhipu-glm5",
        "name": "智谱 GLM-5",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-5"
    },
    {
        "key": "deepseek",
        "name": "DeepSeek",
        "api_base": "https://api.deepseek.com/v1",
        "model": "deepseek-chat"
    },
    {
        "key": "qwen",
        "name": "通义千问 Qwen-Turbo",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-turbo"
    },
    {
        "key": "openai",
        "name": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini"
    },
    {
        "key": "anthropic",
        "name": "Anthropic Claude",
        "api_base": "https://api.anthropic.com",
        "model": "claude-haiku-4-5-20251001"
    },
]


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().isoformat()}] {msg}\n")


def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            config.update(user_config)
        except Exception as e:
            log(f"Error loading config: {e}")
    return config


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


def _extract_text_from_content(content):
    """从 message.content 提取纯文本（content 可能是 str 或 list of blocks）"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                # 工具调用：取工具名
                parts.append(f"[调用工具: {block.get('name', '')}]")
            # 跳过 tool_result、thinking 等
        return "\n".join(p for p in parts if p)
    return ""


def _is_system_message(text):
    """判断是否为系统消息（command 输出、system reminder 等）"""
    if not text:
        return True
    t = text.strip()
    # 过滤命令输出、系统提醒等
    prefixes = (
        "<local-command-",
        "<command-",
        "<command-name>",
        "<command-message>",
        "<command-args>",
        "<system-reminder>",
        "Caveat:",
        "[Request interrupted",
    )
    for p in prefixes:
        if p in t[:100]:
            return True
    return False


def extract_context(transcript_path):
    """从 .jsonl 文件提取对话内容用于 LLM 摘要（兼容新旧格式）"""
    messages = []
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")

                # 跳过元数据和系统条目
                if entry_type in (
                    "custom-title", "agent-name", "queue-operation",
                    "permission-mode", "file-history-snapshot", "system",
                    "summary", "file-snapshot"
                ):
                    continue

                # 新版格式：message.content
                msg = entry.get("message")
                if isinstance(msg, dict):
                    role = msg.get("role", "")
                    text = _extract_text_from_content(msg.get("content", ""))
                    if not text or _is_system_message(text):
                        continue
                    label = "用户" if role == "user" else "助手"
                    messages.append(f"{label}: {text.strip()[:300]}")
                    continue

                # 旧版格式：display 字段
                display = entry.get("display", "")
                if display and isinstance(display, str) and not _is_system_message(display):
                    messages.append(f"用户: {display.strip()[:300]}")
    except Exception as e:
        log(f"Error reading transcript: {e}")

    if not messages:
        return ""

    half = MAX_CONTEXT_CHARS // 2
    text = "\n".join(messages)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:half] + "\n...(中间省略)...\n" + text[-half:]

    return text


def detect_api_format(api_base):
    """根据 URL 自动检测 API 格式"""
    if "anthropic" in api_base.lower():
        return "anthropic"
    return "openai"


def generate_name_llm(context, config):
    """通过 LLM API 生成会话名称，自动适配 OpenAI 和 Anthropic 两种格式"""
    api_key = config.get("api_key", "")
    if not api_key:
        log("No api_key configured. Run: python ~/.claude/scripts/session-namer.py --setup")
        return None

    api_base = config.get("api_base", "").rstrip("/")
    model = config.get("model", "glm-4-flash")
    max_tokens = config.get("max_tokens", 100)
    api_format = config.get("api_format") or detect_api_format(api_base)

    prompt = f"根据以下对话内容，生成一个简洁的中文会话名称，不超过30个字，直接输出名称，不加任何标点符号和解释：\n\n{context}"

    if api_format == "anthropic":
        url = f"{api_base}/v1/messages"
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }
    else:
        url = f"{api_base}/chat/completions"
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        log(f"Calling {api_format} API: {url}, model={model}")
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # 解析响应（两种格式）
        if api_format == "anthropic":
            name = result["content"][0]["text"].strip()
        else:
            name = result["choices"][0]["message"]["content"].strip()

        # 清理可能的引号和标点
        name = name.strip('"\'""''「」【】')
        if len(name) > 30:
            name = name[:30]
        log(f"LLM returned: {name}")
        return name
    except Exception as e:
        log(f"LLM API call failed ({api_format}): {e}")
        return None


def generate_name_fallback(transcript_path):
    """回退方案：取最后一条有意义的用户消息"""
    last_msg = ""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                display = entry.get("display", "")
                if display and isinstance(display, str) and len(display.strip()) > 2:
                    last_msg = display.strip()
    except Exception:
        pass

    if last_msg:
        return last_msg[:30]
    return None


def append_title_to_jsonl(transcript_path, session_id, title):
    """追加 custom-title 和 agent-name 到 .jsonl 文件"""
    entries = [
        json.dumps({"type": "custom-title", "customTitle": title, "sessionId": session_id}, ensure_ascii=False),
        json.dumps({"type": "agent-name", "agentName": title, "sessionId": session_id}, ensure_ascii=False),
    ]
    with open(transcript_path, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(entry + "\n")


def detect_workspace(transcript_path):
    """从 transcript_path 推断 workspace key"""
    parent = os.path.dirname(transcript_path)
    workspace_key = os.path.basename(parent)
    return workspace_key


def run_setup():
    """非交互式配置，通过命令行参数完成"""
    import argparse
    parser = argparse.ArgumentParser(description="Session Namer Config")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--api-base", help="API base URL")
    parser.add_argument("--api-key", help="API key")
    parser.add_argument("--model", help="Model name")
    parser.add_argument("--test", action="store_true", help="Test API connection")
    parser.add_argument("--show", action="store_true", help="Show current config")
    parser.add_argument("--presets", action="store_true", help="List available presets")
    parser.add_argument("--preset", help="Use a preset (zhipu-flash, zhipu-glm5, deepseek, qwen, openai, anthropic)")
    args = parser.parse_args()

    # --presets: 列出预设
    if args.presets:
        print(json.dumps(PROVIDER_PRESETS, ensure_ascii=False, indent=2))
        return

    # --show: 查看当前配置
    if args.show:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            safe = config.copy()
            key = safe.get("api_key", "")
            safe["api_key"] = (key[:8] + "..." + key[-4:]) if len(key) > 12 else "***"
            print(json.dumps(safe, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"error": "not_configured", "message": "Config not found. Run --setup with --api-base and --api-key"}, ensure_ascii=False))
        return

    # --test: 测试连接
    if args.test:
        config = load_config()
        if not config.get("api_key"):
            print(json.dumps({"error": "no_api_key", "message": "API key not configured"}, ensure_ascii=False))
            return
        result = generate_name_llm(
            "用户: 帮我写一个Python脚本\n助手: 好的\n用户: 写一个爬虫",
            config
        )
        if result:
            print(json.dumps({"status": "ok", "test_name": result}, ensure_ascii=False))
        else:
            print(json.dumps({"status": "error", "message": "API call failed, check logs"}, ensure_ascii=False))
        return

    # --setup: 配置
    config = DEFAULT_CONFIG.copy()

    # 如果指定了 preset，自动填充 api_base 和 model
    if args.preset:
        matched = [p for p in PROVIDER_PRESETS if p["key"] == args.preset]
        if matched:
            config["api_base"] = matched[0]["api_base"]
            config["model"] = matched[0]["model"]
        else:
            keys = [p["key"] for p in PROVIDER_PRESETS]
            print(json.dumps({"error": "invalid_preset", "available": keys}, ensure_ascii=False))
            return

    # 命令行参数覆盖
    if args.api_base:
        config["api_base"] = args.api_base
    if args.model:
        config["model"] = args.model
    if args.api_key:
        config["api_key"] = args.api_key

    # 校验必填项
    if not config.get("api_base"):
        print(json.dumps({
            "error": "missing_api_base",
            "message": "Missing --api-base or --preset",
            "usage": "python session-namer.py --setup --preset zhipu-glm5 --api-key YOUR_KEY",
            "presets": [p["key"] for p in PROVIDER_PRESETS]
        }, ensure_ascii=False, indent=2))
        return

    if not config.get("api_key"):
        print(json.dumps({
            "error": "missing_api_key",
            "message": "Missing --api-key",
            "usage": f"python session-namer.py --setup --preset zhipu-glm5 --api-key YOUR_KEY"
        }, ensure_ascii=False, indent=2))
        return

    if not config.get("model"):
        config["model"] = "glm-4-flash"  # safe default

    # 保存
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    safe = config.copy()
    key = safe["api_key"]
    safe["api_key"] = (key[:8] + "..." + key[-4:]) if len(key) > 12 else "***"
    print(json.dumps({"status": "ok", "config": safe}, ensure_ascii=False, indent=2))


def main():
    # 处理 --setup / --show / --test / --presets 参数
    if any(a in sys.argv for a in ("--setup", "--show", "--test", "--presets")):
        run_setup()
        return

    try:
        # 1. 读取 stdin JSON
        raw = sys.stdin.read()
        log(f"stdin: {raw[:500]}")
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

        # 2. 检查是否已有 custom-title（避免重复命名）
        has_title = False
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    if '"custom-title"' in line:
                        has_title = True
                        break
        except Exception:
            pass

        if has_title:
            log("Session already has custom-title, skipping")
            print("[session-namer] 会话已有自定义名称，跳过")
            return

        # 3. 提取对话内容
        context = extract_context(transcript_path)
        if not context:
            log("No meaningful content found in transcript")
            return

        # 4. 加载配置
        config = load_config()

        # 5. 生成名称（LLM 优先，失败回退）
        name = generate_name_llm(context, config)
        method = "llm"
        if not name:
            name = generate_name_fallback(transcript_path)
            method = "fallback"
        if not name:
            log("Failed to generate name by any method")
            return

        log(f"Generated name ({method}): {name}")

        # 6. 追加到 .jsonl
        append_title_to_jsonl(transcript_path, session_id, name)
        log(f"Appended custom-title to {transcript_path}")

        # 7. 更新 session-meta.json
        workspace_key = detect_workspace(transcript_path)
        meta = read_meta()
        meta["sessions"][session_id] = {
            "autoName": name,
            "namingStatus": "auto",
            "namingMethod": method,
            "workspace": cwd,
            "workspaceKey": workspace_key,
            "namedAt": datetime.datetime.now().isoformat()
        }
        write_meta(meta)
        log("Updated session-meta.json")

        # 8. 输出结果
        print(f"[session-namer] 会话已命名为: {name}")

    except Exception:
        log(f"FATAL: {traceback.format_exc()}")
        sys.exit(0)


if __name__ == "__main__":
    main()
