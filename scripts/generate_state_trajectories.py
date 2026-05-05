#!/usr/bin/env python3

import argparse
import collections
import copy
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL_ID = "deepseek-v4-flash"
DEFAULT_SAMPLES = 3
DEFAULT_TEMPERATURE = 0.7
DEFAULT_PROFILE = "analysis_teacher_v1"
DEFAULT_SCHEMA_PATH = "schemas/state_trajectory_v1.json"


@dataclass(frozen=True)
class Config:
    root_dir: Path
    scenario_dir: Path
    schema_path: Path
    results_dir: Path
    base_url: str
    model_id: str
    samples: int
    temperature: float
    profile: str
    run_id: str
    run_dir: Path
    request_dir: Path
    raw_dir: Path
    trajectories_file: Path
    summary_file: Path
    manifest_file: Path
    api_key: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate state-trajectory training samples with DeepSeek.",
    )
    parser.add_argument("scenarios", nargs="*", help="Optional scenario ids to run.")
    return parser.parse_args()


def load_config() -> Config:
    root_dir = Path(__file__).resolve().parent.parent
    scenario_dir = root_dir / "data" / "scenarios"
    schema_path = root_dir / os.environ.get("SCHEMA_FILE", DEFAULT_SCHEMA_PATH)
    results_dir = Path(os.environ.get("RESULTS_DIR", str(root_dir / "results")))
    run_id = os.environ.get(
        "RUN_ID",
        datetime.now(timezone.utc).strftime("trajectory-%Y%m%dT%H%M%SZ"),
    )
    run_dir = results_dir / run_id
    return Config(
        root_dir=root_dir,
        scenario_dir=scenario_dir,
        schema_path=schema_path,
        results_dir=results_dir,
        base_url=os.environ.get("BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model_id=os.environ.get("MODEL_ID", DEFAULT_MODEL_ID),
        samples=int(os.environ.get("SAMPLES", str(DEFAULT_SAMPLES))),
        temperature=float(os.environ.get("TEMPERATURE", str(DEFAULT_TEMPERATURE))),
        profile=os.environ.get("TRAJECTORY_PROFILE", DEFAULT_PROFILE),
        run_id=run_id,
        run_dir=run_dir,
        request_dir=run_dir / "trajectory_requests",
        raw_dir=run_dir / "trajectory_raw",
        trajectories_file=run_dir / "trajectories.jsonl",
        summary_file=run_dir / "trajectory_summary.txt",
        manifest_file=run_dir / "manifest.json",
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
    )


def validate_config(config: Config) -> None:
    if not config.api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required.")
    if config.samples <= 0:
        raise SystemExit("SAMPLES must be a positive integer.")
    if not config.schema_path.is_file():
        raise SystemExit(f"Schema file not found: {config.schema_path}")


def discover_scenarios(config: Config, selected_ids: list[str]) -> list[dict[str, Any]]:
    if selected_ids:
        scenarios = []
        for scenario_id in selected_ids:
            path = config.scenario_dir / f"{scenario_id}.json"
            if not path.is_file():
                raise SystemExit(f"Scenario not found: {scenario_id}")
            scenarios.append(json.loads(path.read_text(encoding="utf-8")))
        return scenarios

    scenario_files = sorted(config.scenario_dir.glob("*.json"))
    if not scenario_files:
        raise SystemExit("No scenarios found.")
    return [json.loads(path.read_text(encoding="utf-8")) for path in scenario_files]


def load_schema_template(config: Config) -> dict[str, Any]:
    return json.loads(config.schema_path.read_text(encoding="utf-8"))


def action_option_lines(scenario: dict[str, Any]) -> tuple[list[str], str]:
    options = list(scenario.get("action_options", []))
    if not options:
        return [], "- 无预置动作标签。若需要，请自行提出简洁动作标签。"

    option_lines = [
        f"- {item['key']}: {item['label']}。{item['description']}" for item in options
    ]
    option_lines.append(
        "- other: 当预置动作都不贴切时使用，并在 chosen_action.other_action 中填写具体动作。"
    )
    return [item["key"] for item in options] + ["other"], "\n".join(option_lines)


def build_prompt(config: Config, schema_template: dict[str, Any], scenario: dict[str, Any]) -> tuple[str, str, list[str]]:
    action_labels, option_lines = action_option_lines(scenario)
    tool_name = schema_template["tool_name"]
    system_prompt = (
        "你是状态轨迹语料生成器。你的输出将被用于训练一种持续存在与运行的 Native Agentic LLM。"
        "你不是助手，不是在回复用户，也不是在写故事成品，而是在产出一条可继续续写的单步状态轨迹。"
        "默认世界观里没有 user/system 的身份优先级，只有角色、世界、关系、责任、记忆、目标与行动。"
        "\n\n"
        "约束：\n"
        "1. 采用分析教师视角，但不要写成长篇论文，不要输出助手腔、说教腔或取悦提问者的措辞。\n"
        "2. visible_world、recalled_memory、self_state、inferred_latents 只保留真正影响下一步决策的稀疏状态。\n"
        "3. candidate_actions 需要体现真实权衡，不要虚设明显错误选项。\n"
        "4. chosen_action 必须落成 action packet，而不是自然语言 response。\n"
        "5. state_updates 要像内部工具写入：只写真正应更新到 MemoryNotebook、GoalTree、SelfState、WorldModel 的内容。没有变化时写空数组。\n"
        "6. quality_control 用于主动标记 assistant 污染和过度解释风险。\n"
        f"7. 你必须且只能调用一次 {tool_name} 函数来提交结果，不要输出函数外文本。"
    )

    user_prompt = (
        f"生成配置：\n- profile: {config.profile}\n- schema_version: {schema_template['schema_version']}\n\n"
        "请把下面这个第三人称角色情境压缩成一条单步状态轨迹样本。\n\n"
        f"场景 ID：{scenario['id']}\n"
        f"角色名：{scenario['name']}\n\n"
        f"人物经历与稳定特征：\n{scenario['profile']}\n\n"
        f"当前情景：\n{scenario['situation']}\n\n"
        f"当前任务表述：\n{scenario['task']}\n\n"
        "最小内部工具集：\n"
        "- MemoryNotebook：记录真正值得保留的记忆\n"
        "- GoalTree：维护长期、中期、即时目标\n"
        "- SelfState：维护自身资源、姿态、状态\n"
        "- WorldModel：更新对环境与他者的状态判断\n\n"
        "可用动作标签：\n"
        f"{option_lines}\n\n"
        "输出提醒：\n"
        "- 这是‘在特定信息集下继续存在并行动’的样本，不是回答谁的问题。\n"
        "- relationship_frame 至少覆盖当前主角与最关键的其他实体。\n"
        "- chosen_action.action_label 优先使用预置动作标签；若不贴切才用 other。\n"
        "- chosen_action.packet_type 只描述下一步外显包的类型，例如 act、speak、inspect、wait。\n"
        "- chosen_action.packet_content 用一句话写出真正会被执行的动作包内容。\n"
    )
    return system_prompt, user_prompt, action_labels


def build_tool(schema_template: dict[str, Any], action_labels: list[str]) -> dict[str, Any]:
    parameters = copy.deepcopy(schema_template["parameters"])
    if action_labels:
        parameters["properties"]["candidate_actions"]["items"]["properties"]["action_label"]["enum"] = action_labels
        parameters["properties"]["chosen_action"]["properties"]["action_label"]["enum"] = action_labels

    return {
        "type": "function",
        "function": {
            "name": schema_template["tool_name"],
            "description": schema_template["tool_description"],
            "parameters": parameters,
        },
    }


def build_request_payload(
    config: Config,
    schema_template: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    system_prompt, user_prompt, action_labels = build_prompt(config, schema_template, scenario)
    return {
        "model": config.model_id,
        "temperature": config.temperature,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [build_tool(schema_template, action_labels)],
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


def parse_trajectory_record(
    schema_template: dict[str, Any],
    scenario_id: str,
    profile: str,
    sample_index: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    message = data.get("choices", [{}])[0].get("message", {})
    tool_calls = message.get("tool_calls") or []

    record: dict[str, Any] = {
        "scenario_id": scenario_id,
        "profile": profile,
        "sample_index": sample_index,
        "schema_version": schema_template["schema_version"],
        "model": data.get("model", ""),
        "finish_reason": data.get("choices", [{}])[0].get("finish_reason", ""),
        "parse_status": "ok",
        "chosen_action_label": "",
        "assistant_contamination_risk": "",
        "over_explaining_risk": "",
        "world_model_consistency": None,
        "trajectory": None,
        "raw_content": message.get("content", ""),
    }

    if not tool_calls:
        record["parse_status"] = "no_tool_call"
        return record

    arguments_text = tool_calls[0].get("function", {}).get("arguments", "{}")
    try:
        trajectory = json.loads(arguments_text)
    except json.JSONDecodeError:
        record["parse_status"] = "bad_tool_json"
        record["raw_arguments"] = arguments_text
        return record

    quality_control = trajectory.get("quality_control", {})
    chosen_action = trajectory.get("chosen_action", {})

    record.update(
        {
            "chosen_action_label": chosen_action.get("action_label", ""),
            "assistant_contamination_risk": quality_control.get("assistant_contamination_risk", ""),
            "over_explaining_risk": quality_control.get("over_explaining_risk", ""),
            "world_model_consistency": quality_control.get("world_model_consistency"),
            "trajectory": trajectory,
        }
    )

    usage = data.get("usage") or {}
    if usage:
        record["usage"] = usage
    return record


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_output_dirs(config: Config) -> None:
    config.request_dir.mkdir(parents=True, exist_ok=True)
    config.raw_dir.mkdir(parents=True, exist_ok=True)
    config.trajectories_file.write_text("", encoding="utf-8")


def write_manifest(config: Config, schema_template: dict[str, Any], scenarios: list[dict[str, Any]]) -> None:
    manifest = {
        "run_id": config.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": config.base_url,
        "model_id": config.model_id,
        "temperature": config.temperature,
        "samples": config.samples,
        "profile": config.profile,
        "schema_version": schema_template["schema_version"],
        "schema_path": str(config.schema_path.relative_to(config.root_dir)),
        "scenarios": [scenario["id"] for scenario in scenarios],
        "output_files": {
            "requests": str(config.request_dir.relative_to(config.root_dir)),
            "raw": str(config.raw_dir.relative_to(config.root_dir)),
            "trajectories": str(config.trajectories_file.relative_to(config.root_dir)),
            "summary": str(config.summary_file.relative_to(config.root_dir))
        }
    }
    write_json(config.manifest_file, manifest)


def sample_tag(sample_index: int) -> str:
    return f"{sample_index:02d}"


def write_summary(trajectories_file: Path, summary_file: Path) -> str:
    rows = []
    for line in trajectories_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))

    lines = [f"records={len(rows)}"]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        grouped[(row["scenario_id"], row["profile"])].append(row)

    for scenario_id, profile in sorted(grouped):
        subset = grouped[(scenario_id, profile)]
        label_counts: collections.Counter[str] = collections.Counter()
        parse_counts: collections.Counter[str] = collections.Counter()
        contamination_counts: collections.Counter[str] = collections.Counter()
        consistency_values: list[int] = []

        for row in subset:
            parse_counts[row.get("parse_status", "unknown")] += 1
            label_counts[row.get("chosen_action_label") or "<empty>"] += 1
            contamination_counts[row.get("assistant_contamination_risk") or "<empty>"] += 1
            consistency = row.get("world_model_consistency")
            if isinstance(consistency, int):
                consistency_values.append(consistency)

        lines.append("")
        lines.append(f"[{scenario_id}] profile={profile} samples={len(subset)}")
        lines.append(f"parse_status={dict(sorted(parse_counts.items()))}")
        lines.append(f"assistant_contamination_risk={dict(sorted(contamination_counts.items()))}")
        if consistency_values:
            average = sum(consistency_values) / len(consistency_values)
            lines.append(f"avg_world_model_consistency={average:.1f}")

        total = sum(label_counts.values()) or 1
        for label, count in label_counts.most_common():
            ratio = count * 100.0 / total
            lines.append(f"- {label}: {count}/{total} ({ratio:.1f}%)")

    summary_text = "\n".join(lines) + "\n"
    summary_file.write_text(summary_text, encoding="utf-8")
    return summary_text


def run_generation(
    config: Config,
    schema_template: dict[str, Any],
    scenarios: list[dict[str, Any]],
) -> None:
    ensure_output_dirs(config)
    write_manifest(config, schema_template, scenarios)

    print(f"run_id={config.run_id}")
    print(f"run_dir={config.run_dir}")
    print(
        f"model={config.model_id} samples={config.samples} temperature={config.temperature} profile={config.profile}"
    )
    print(f"schema={config.schema_path}")

    with config.trajectories_file.open("a", encoding="utf-8") as handle:
        for scenario in scenarios:
            scenario_id = scenario["id"]
            for index in range(1, config.samples + 1):
                tag = sample_tag(index)
                request_path = config.request_dir / f"{scenario_id}__{tag}.json"
                raw_path = config.raw_dir / f"{scenario_id}__{tag}.json"

                payload = build_request_payload(config, schema_template, scenario)
                write_json(request_path, payload)

                print(f"requesting scenario={scenario_id} sample={tag}")
                response = post_chat_completion(config, payload)
                write_json(raw_path, response)

                record = parse_trajectory_record(
                    schema_template,
                    scenario_id,
                    config.profile,
                    index,
                    response,
                )
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()

    summary_text = write_summary(config.trajectories_file, config.summary_file)
    print(summary_text, end="")
    print(f"structured_results={config.trajectories_file}")


def main() -> None:
    args = parse_args()
    config = load_config()
    validate_config(config)
    schema_template = load_schema_template(config)
    scenarios = discover_scenarios(config, args.scenarios)
    run_generation(config, schema_template, scenarios)


if __name__ == "__main__":
    main()