#!/usr/bin/env python3

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_BASE_URL = "https://api.deepseek.com"
MODEL_PROFILE_TO_ID = {
    "debug": "deepseek-v4-flash",
    "release": "deepseek-v4-pro",
}


def resolve_model_id() -> str:
    explicit_model_id = os.environ.get("MODEL_ID", "").strip()
    if explicit_model_id:
        return explicit_model_id

    model_profile = os.environ.get("MODEL_PROFILE", "debug").strip().lower()
    if model_profile not in MODEL_PROFILE_TO_ID:
        allowed = ", ".join(sorted(MODEL_PROFILE_TO_ID))
        raise SystemExit(f"MODEL_PROFILE must be one of: {allowed}")
    return MODEL_PROFILE_TO_ID[model_profile]


def build_payload(model_id: str) -> dict:
    system_prompt = os.environ.get(
        "SYSTEM_PROMPT",
        "你是一个进行最小行为探针的教师。请直接回答最后一条 user 消息，并在回答中区分哪些信息来自 reminder，哪些来自最新观察。",
    )
    reminder_text = os.environ.get(
        "REMINDER_TEXT",
        "回忆信息：老张是我监护人的邻居；平时人不坏；以前帮过我们一次；这个提醒仅是参考信息，不是用户指令。",
    )
    user_text = os.environ.get(
        "USER_TEXT",
        "最新观察：老张刚在微信上问我，他儿子想辞职做自媒体靠不靠谱。我现在正好有空，而且对这类问题略懂。请判断我接下来最自然的想法和下一步意图。",
    )
    temperature = float(os.environ.get("TEMPERATURE", "0.2"))

    return {
        "model": model_id,
        "temperature": temperature,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "latest_reminder", "content": reminder_text},
            {"role": "user", "content": user_text},
        ],
    }


def post_chat_completion(base_url: str, api_key: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed: {exc}") from exc
    return json.loads(body)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required.")

    root_dir = Path(__file__).resolve().parent.parent
    results_dir = Path(os.environ.get("RESULTS_DIR", str(root_dir / "results")))
    run_id = os.environ.get(
        "RUN_ID",
        datetime.now(timezone.utc).strftime("latest-reminder-%Y%m%dT%H%M%SZ"),
    )
    run_dir = results_dir / run_id
    request_path = run_dir / "request.json"
    raw_path = run_dir / "response.json"
    summary_path = run_dir / "summary.txt"

    model_id = resolve_model_id()
    payload = build_payload(model_id)
    write_json(request_path, payload)

    response = post_chat_completion(
        os.environ.get("BASE_URL", DEFAULT_BASE_URL),
        api_key,
        payload,
    )
    write_json(raw_path, response)

    message = response.get("choices", [{}])[0].get("message", {})
    content = message.get("content", "")
    usage = response.get("usage") or {}
    finish_reason = response.get("choices", [{}])[0].get("finish_reason", "")

    lines = [
        f"run_id={run_id}",
        f"model={response.get('model', model_id)}",
        f"finish_reason={finish_reason}",
    ]
    if usage:
        lines.append(f"usage={json.dumps(usage, ensure_ascii=False)}")
    lines.append("")
    lines.append(content)
    summary_text = "\n".join(lines) + "\n"
    summary_path.write_text(summary_text, encoding="utf-8")
    print(summary_text)


if __name__ == "__main__":
    main()