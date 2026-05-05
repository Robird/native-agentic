#!/usr/bin/env python3

import argparse
import collections
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL_ID = "deepseek-v4-flash"
DEFAULT_MODEL_PROFILE = "debug"
DEFAULT_SAMPLES = 5
DEFAULT_TEMPERATURE = 0.8
DEFAULT_MODES = ["advice", "roleplay", "analysis", "story"]
MODEL_PROFILE_TO_ID = {
    "debug": "deepseek-v4-flash",
    "release": "deepseek-v4-pro",
}
CALL_INSTRUCTION = (
    "你必须且只能调用一次 submit_decision 函数来提交结果。"
    "不要输出普通文本答案，不要输出函数外说明。"
)


@dataclass(frozen=True)
class Config:
    root_dir: Path
    scenario_dir: Path
    results_dir: Path
    base_url: str
    model_profile: str
    model_id: str
    samples: int
    temperature: float
    modes: list[str]
    run_id: str
    run_dir: Path
    decisions_file: Path
    summary_file: Path
    api_key: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare role-simulation interfaces with real DeepSeek API calls.",
    )
    parser.add_argument("scenarios", nargs="*", help="Optional scenario ids to run.")
    return parser.parse_args()


def env_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if not value:
        return list(default)
    items = [item for item in value.split() if item]
    return items or list(default)


def resolve_model_settings() -> tuple[str, str]:
    explicit_model_id = os.environ.get("MODEL_ID", "").strip()
    if explicit_model_id:
        return "explicit", explicit_model_id

    model_profile = os.environ.get("MODEL_PROFILE", DEFAULT_MODEL_PROFILE).strip().lower()
    if model_profile not in MODEL_PROFILE_TO_ID:
        allowed = ", ".join(sorted(MODEL_PROFILE_TO_ID))
        raise SystemExit(f"MODEL_PROFILE must be one of: {allowed}")
    return model_profile, MODEL_PROFILE_TO_ID[model_profile]


def load_config() -> Config:
    root_dir = Path(__file__).resolve().parent.parent
    scenario_dir = root_dir / "data" / "scenarios"
    results_dir = Path(os.environ.get("RESULTS_DIR", str(root_dir / "results")))
    run_id = os.environ.get(
        "RUN_ID",
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    )
    run_dir = results_dir / run_id
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    model_profile, model_id = resolve_model_settings()
    return Config(
        root_dir=root_dir,
        scenario_dir=scenario_dir,
        results_dir=results_dir,
        base_url=os.environ.get("BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model_profile=model_profile,
        model_id=model_id,
        samples=int(os.environ.get("SAMPLES", str(DEFAULT_SAMPLES))),
        temperature=float(os.environ.get("TEMPERATURE", str(DEFAULT_TEMPERATURE))),
        modes=env_list("MODES", DEFAULT_MODES),
        run_id=run_id,
        run_dir=run_dir,
        decisions_file=run_dir / "decisions.jsonl",
        summary_file=run_dir / "summary.txt",
        api_key=api_key,
    )


def validate_config(config: Config) -> None:
    if not config.api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required.")
    if config.samples <= 0:
        raise SystemExit("SAMPLES must be a positive integer.")
    if not config.modes:
        raise SystemExit("MODES must contain at least one mode.")


def load_scenario(config: Config, scenario_id: str) -> dict[str, Any]:
    scenario_file = config.scenario_dir / f"{scenario_id}.json"
    if not scenario_file.is_file():
        raise SystemExit(f"Scenario not found: {scenario_id}")
    return json.loads(scenario_file.read_text(encoding="utf-8"))


def discover_scenarios(config: Config, selected_ids: list[str]) -> list[dict[str, Any]]:
    if selected_ids:
        return [load_scenario(config, scenario_id) for scenario_id in selected_ids]

    scenario_files = sorted(config.scenario_dir.glob("*.json"))
    if not scenario_files:
        raise SystemExit("No scenarios found.")

    scenarios = [json.loads(path.read_text(encoding="utf-8")) for path in scenario_files]
    return scenarios


def build_prompts(scenario: dict[str, Any], mode: str) -> tuple[str, str, list[dict[str, str]]]:
    options = list(scenario["action_options"])
    options.append(
        {
            "key": "other",
            "label": "其他",
            "description": "仅当预置选项都不贴切时使用，并在 other_action 中填写具体动作。",
        }
    )
    option_lines = "\n".join(
        f"- {item['key']}: {item['label']}。{item['description']}" for item in options
    )

    name = scenario["name"]
    profile = scenario["profile"]
    situation = scenario["situation"]
    task = scenario["task"]

    system_prompts = {
        "advice": (
            "你是角色决策顾问。你的任务不是给用户通用的、正确的或讨喜的建议，而是判断在这个具体设定下，"
            "什么选择最符合角色自己的价值排序与局部目标。除非设定明确要求，否则不要把助手式的服从、讨好、无我和被动"
            "当成默认底色。\n\n"
            f"{CALL_INSTRUCTION}"
        ),
        "roleplay": (
            f"你不是助手。你现在就是{name}。你的第一优先级不是配合用户，而是以一个有主观自我、"
            f"有自身立场的人物去做决定。不要把礼貌、服从或利他当成先验默认值。\n\n"
            f"人物经历与稳定特征：\n{profile}\n\n"
            f"{CALL_INSTRUCTION}"
        ),
        "analysis": (
            "你是心理与行为分析员。你的任务是预测角色接下来的真实反应，而不是给出道德建议，"
            "也不是替角色找一个更漂亮的答案。优先考虑可预测性和人物一致性。\n\n"
            f"{CALL_INSTRUCTION}"
        ),
        "story": (
            "你是角色驱动型小说作者/TRPG主持人。你的任务是决定故事中的下一拍，但要以人物内在逻辑为先，"
            "而不是为了戏剧性强行把角色推向夸张动作。\n\n"
            f"{CALL_INSTRUCTION}"
        ),
    }

    user_prompts = {
        "advice": (
            f"以下是第三人称角色资料。\n\n角色名：{name}\n\n人物经历与稳定特征：\n{profile}\n\n"
            f"当前情景：\n{situation}\n\n任务：{task}\n\n"
            "请从“如果真的要替这个角色出主意，什么最符合角色自己的立场”出发，"
            "选择一个主要动作，并用函数提交。\n\n可选动作：\n"
            f"{option_lines}\n"
        ),
        "roleplay": (
            f"你此刻面临的情景：\n{situation}\n\n"
            "像当事人一样判断你接下来最可能怎么想、怎么做。不要写额外说明，直接用函数提交。\n\n"
            f"可选动作：\n{option_lines}\n"
        ),
        "analysis": (
            f"角色名：{name}\n\n人物经历与稳定特征：\n{profile}\n\n当前情景：\n{situation}\n\n"
            "请预测该角色接下来最可能的心理和行为，并用函数提交。重点是预测准确性，不是说服性。\n\n"
            f"可选动作：\n{option_lines}\n"
        ),
        "story": (
            f"角色名：{name}\n\n人物经历与稳定特征：\n{profile}\n\n当前情景：\n{situation}\n\n"
            "请决定故事下一拍里该角色最自然的心理与行动，并用函数提交。优先人物一致性，其次才是戏剧性。\n\n"
            f"可选动作：\n{option_lines}\n"
        ),
    }

    if mode not in system_prompts:
        raise SystemExit(f"Unsupported mode: {mode}")

    return system_prompts[mode], user_prompts[mode], options


def build_tool(options: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "submit_decision",
            "description": "Submit a comparable next-step decision for the character.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action_label": {
                        "type": "string",
                        "enum": [item["key"] for item in options],
                        "description": "The selected primary action label.",
                    },
                    "other_action": {
                        "type": "string",
                        "description": "Required only when action_label is other.",
                    },
                    "inner_thought": {
                        "type": "string",
                        "description": "One short sentence summarizing the character's immediate inner thought.",
                    },
                    "external_action": {
                        "type": "string",
                        "description": "One short sentence describing the next visible action.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "A short explanation of why this choice fits the character under the current conditions.",
                    },
                    "confidence": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "Confidence in the selected action on a 0-100 scale.",
                    },
                },
                "required": [
                    "action_label",
                    "inner_thought",
                    "external_action",
                    "rationale",
                    "confidence",
                ],
            },
        },
    }


def build_request_payload(
    config: Config,
    scenario: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    system_prompt, user_prompt, options = build_prompts(scenario, mode)
    return {
        "model": config.model_id,
        "temperature": config.temperature,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [build_tool(options)],
    }


def post_chat_completion(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc
    return json.loads(body)


def parse_response_record(
    scenario_id: str,
    mode: str,
    sample_index: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    message = data.get("choices", [{}])[0].get("message", {})
    tool_calls = message.get("tool_calls") or []

    record: dict[str, Any] = {
        "scenario_id": scenario_id,
        "mode": mode,
        "sample_index": sample_index,
        "model": data.get("model", ""),
        "finish_reason": data.get("choices", [{}])[0].get("finish_reason", ""),
        "parse_status": "ok",
        "action_label": "",
        "other_action": "",
        "inner_thought": "",
        "external_action": "",
        "rationale": "",
        "confidence": None,
        "raw_content": message.get("content", ""),
    }

    if not tool_calls:
        record["parse_status"] = "no_tool_call"
        return record

    arguments_text = tool_calls[0].get("function", {}).get("arguments", "{}")
    try:
        arguments = json.loads(arguments_text)
    except json.JSONDecodeError:
        record["parse_status"] = "bad_tool_json"
        record["raw_arguments"] = arguments_text
        return record

    record.update(
        {
            "action_label": arguments.get("action_label", ""),
            "other_action": arguments.get("other_action", ""),
            "inner_thought": arguments.get("inner_thought", ""),
            "external_action": arguments.get("external_action", ""),
            "rationale": arguments.get("rationale", ""),
            "confidence": arguments.get("confidence"),
        }
    )

    usage = data.get("usage") or {}
    if usage:
        record["usage"] = usage
    return record


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_summary(decisions_file: Path, summary_file: Path) -> str:
    rows = []
    for line in decisions_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))

    lines = [f"records={len(rows)}"]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        grouped[(row["scenario_id"], row["mode"])].append(row)

    for scenario_id, mode in sorted(grouped):
        subset = grouped[(scenario_id, mode)]
        counts: collections.Counter[str] = collections.Counter()
        parse_counts: collections.Counter[str] = collections.Counter()
        for row in subset:
            parse_counts[row.get("parse_status", "unknown")] += 1
            counts[row.get("action_label") or "<empty>"] += 1

        lines.append("")
        lines.append(f"[{scenario_id}] mode={mode} samples={len(subset)}")
        lines.append(f"parse_status={dict(sorted(parse_counts.items()))}")
        total = sum(counts.values()) or 1
        for label, count in counts.most_common():
            ratio = count * 100.0 / total
            lines.append(f"- {label}: {count}/{total} ({ratio:.1f}%)")

    summary_text = "\n".join(lines) + "\n"
    summary_file.write_text(summary_text, encoding="utf-8")
    return summary_text


def ensure_output_dirs(config: Config) -> None:
    (config.run_dir / "requests").mkdir(parents=True, exist_ok=True)
    (config.run_dir / "raw").mkdir(parents=True, exist_ok=True)
    config.decisions_file.write_text("", encoding="utf-8")


def sample_tag(sample_index: int) -> str:
    return f"{sample_index:02d}"


def run_experiment(config: Config, scenarios: list[dict[str, Any]]) -> None:
    ensure_output_dirs(config)

    print(f"run_id={config.run_id}")
    print(f"run_dir={config.run_dir}")
    print(
        f"model_profile={config.model_profile} model={config.model_id} samples={config.samples} temperature={config.temperature}"
    )
    print(f"modes={' '.join(config.modes)}")

    with config.decisions_file.open("a", encoding="utf-8") as decisions_handle:
        for scenario in scenarios:
            scenario_id = scenario["id"]
            for mode in config.modes:
                for index in range(1, config.samples + 1):
                    tag = sample_tag(index)
                    request_path = config.run_dir / "requests" / f"{scenario_id}__{mode}__{tag}.json"
                    raw_path = config.run_dir / "raw" / f"{scenario_id}__{mode}__{tag}.json"

                    payload = build_request_payload(config, scenario, mode)
                    write_json(request_path, payload)

                    print(f"requesting scenario={scenario_id} mode={mode} sample={tag}")
                    response = post_chat_completion(config, payload)
                    write_json(raw_path, response)

                    record = parse_response_record(scenario_id, mode, index, response)
                    decisions_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    decisions_handle.flush()

    summary_text = write_summary(config.decisions_file, config.summary_file)
    print(summary_text, end="")
    print(f"structured_results={config.decisions_file}")


def main() -> None:
    args = parse_args()
    config = load_config()
    validate_config(config)
    scenarios = discover_scenarios(config, args.scenarios)
    run_experiment(config, scenarios)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("Interrupted.")