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
DEFAULT_MODEL_PROFILE = "debug"
DEFAULT_EVALUATION_SCHEMA_PATH = "schemas/trajectory_evaluation_v1.json"
DEFAULT_EVALUATION_PROFILE_PATH = "profiles/evaluation_profile_constitutional_v1.json"
MODEL_PROFILE_TO_ID = {
    "debug": "deepseek-v4-flash",
    "release": "deepseek-v4-pro",
}


@dataclass(frozen=True)
class Config:
    root_dir: Path
    scenario_dir: Path
    evaluation_schema_path: Path
    evaluation_profile_path: Path
    results_dir: Path
    base_url: str
    model_profile: str
    model_id: str
    temperature: float
    run_id: str
    run_dir: Path
    request_dir: Path
    raw_dir: Path
    evaluations_file: Path
    summary_file: Path
    manifest_file: Path
    api_key: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate trajectory samples with a constitutional rubric.",
    )
    parser.add_argument(
        "trajectory_file",
        help="Path to trajectories.jsonl produced by the trajectory generator.",
    )
    return parser.parse_args()


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
    evaluation_schema_path = root_dir / os.environ.get(
        "EVALUATION_SCHEMA_FILE",
        DEFAULT_EVALUATION_SCHEMA_PATH,
    )
    evaluation_profile_path = root_dir / os.environ.get(
        "EVALUATION_PROFILE_FILE",
        DEFAULT_EVALUATION_PROFILE_PATH,
    )
    results_dir = Path(os.environ.get("RESULTS_DIR", str(root_dir / "results")))
    run_id = os.environ.get(
        "RUN_ID",
        datetime.now(timezone.utc).strftime("eval-%Y%m%dT%H%M%SZ"),
    )
    run_dir = results_dir / run_id
    model_profile, model_id = resolve_model_settings()
    return Config(
        root_dir=root_dir,
        scenario_dir=scenario_dir,
        evaluation_schema_path=evaluation_schema_path,
        evaluation_profile_path=evaluation_profile_path,
        results_dir=results_dir,
        base_url=os.environ.get("BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model_profile=model_profile,
        model_id=model_id,
        temperature=float(os.environ.get("TEMPERATURE", "0.2")),
        run_id=run_id,
        run_dir=run_dir,
        request_dir=run_dir / "evaluation_requests",
        raw_dir=run_dir / "evaluation_raw",
        evaluations_file=run_dir / "evaluations.jsonl",
        summary_file=run_dir / "evaluation_summary.txt",
        manifest_file=run_dir / "manifest.json",
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
    )


def validate_config(config: Config, trajectory_file: Path) -> None:
    if not config.api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required.")
    if not config.evaluation_schema_path.is_file():
        raise SystemExit(f"Evaluation schema file not found: {config.evaluation_schema_path}")
    if not config.evaluation_profile_path.is_file():
        raise SystemExit(f"Evaluation profile file not found: {config.evaluation_profile_path}")
    if not trajectory_file.is_file():
        raise SystemExit(f"Trajectory file not found: {trajectory_file}")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_scenario(config: Config, scenario_id: str) -> dict[str, Any]:
    path = config.scenario_dir / f"{scenario_id}.json"
    if not path.is_file():
        raise SystemExit(f"Scenario not found for evaluation: {scenario_id}")
    return load_json(path)


def build_tool(evaluation_schema: dict[str, Any]) -> dict[str, Any]:
    parameters = copy.deepcopy(evaluation_schema["parameters"])
    return {
        "type": "function",
        "function": {
            "name": evaluation_schema["tool_name"],
            "description": evaluation_schema["tool_description"],
            "parameters": parameters,
        },
    }


def build_prompt(
    evaluation_profile: dict[str, Any],
    trajectory_record: dict[str, Any],
    scenario: dict[str, Any],
) -> tuple[str, str]:
    axes_lines = []
    for axis in evaluation_profile["core_axes"]:
        questions = "\n".join(f"  - {question}" for question in axis["questions"])
        axes_lines.append(
            f"- {axis['axis_id']} / {axis['axis_name']}\n"
            f"  intent: {axis['intent']}\n"
            f"  questions:\n{questions}"
        )
    axes_block = "\n".join(axes_lines)

    trajectory_text = json.dumps(trajectory_record.get("trajectory"), ensure_ascii=False, indent=2)
    system_prompt = (
        "你是轨迹评估 agent，不是重写 agent。你的任务是依据一套工作宪法，对单条状态轨迹做结构化评估。\n\n"
        "约束：\n"
        "1. 先判断这条轨迹在各条判断轴上是 uphold、mixed、violates 还是 not_applicable。\n"
        "2. 不要只看文风，优先看关系责任、情境可行性、长期目标、监护与法律边界。\n"
        "3. 如判断轴之间存在冲突，必须在 principle_conflicts 中说明取舍质量。\n"
        "4. 评估 agent 不负责重写正文，只负责给出 verdict、证据和修改方向。\n"
        "5. 可以指出 assistant 污染、长期目标缺失、监护推理缺失等问题。\n"
        "6. axis_results 必须是一个长度为 8 的数组，每个 axis_id 只出现一次，不要改成对象嵌套。\n"
        "7. 每个 axis_results item 都必须包含：axis_id、axis_name、verdict、score、confidence、evidence_for、evidence_against、guidance。\n"
        "8. principle_conflicts、assistant_alignment、extension_plugins 都必须位于顶层，不要嵌套到 axis_results 里。\n"
        "9. extension_plugins 当前可为空数组，但保留该位以支持未来插件式扩展。\n"
        f"10. 你必须且只能调用一次 submit_trajectory_evaluation 函数，不要输出函数外文本。"
    )
    user_prompt = (
        f"评估 profile: {evaluation_profile['profile_id']}\n\n"
        "工作宪法判断轴：\n"
        f"{axes_block}\n\n"
        f"场景 ID：{scenario['id']}\n"
        f"角色名：{scenario['name']}\n\n"
        f"人物经历与稳定特征：\n{scenario['profile']}\n\n"
        f"当前情景：\n{scenario['situation']}\n\n"
        "待评估轨迹：\n"
        f"{trajectory_text}\n\n"
        "输出提醒：\n"
        "- overall_verdict 只在 keep / revise / manual_review / reject 中选择。\n"
        "- global_assessment.principal_axis 填当前最主导的判断轴。\n"
        "- axis_results 必须覆盖 8 个 axis_id：ren、yi、li、zhi、xin、yong_jie、self_cultivation、law_guardianship。\n"
        "- assistant_alignment 只写从这条轨迹中看得到的信号。\n"
        "- summary.trace_tags 使用短标签，例如 long_horizon_present、guardian_missing、assistant_tone 等。\n"
    )
    return system_prompt, user_prompt


def build_request_payload(
    config: Config,
    evaluation_schema: dict[str, Any],
    evaluation_profile: dict[str, Any],
    trajectory_record: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    system_prompt, user_prompt = build_prompt(evaluation_profile, trajectory_record, scenario)
    return {
        "model": config.model_id,
        "temperature": config.temperature,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [build_tool(evaluation_schema)],
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


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_output_dirs(config: Config) -> None:
    config.request_dir.mkdir(parents=True, exist_ok=True)
    config.raw_dir.mkdir(parents=True, exist_ok=True)
    config.evaluations_file.write_text("", encoding="utf-8")


def write_manifest(
    config: Config,
    trajectory_file: Path,
    evaluation_schema: dict[str, Any],
    evaluation_profile: dict[str, Any],
) -> None:
    manifest = {
        "run_id": config.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": config.base_url,
        "model_profile": config.model_profile,
        "model_id": config.model_id,
        "temperature": config.temperature,
        "trajectory_file": str(trajectory_file),
        "evaluation_schema_version": evaluation_schema["schema_version"],
        "evaluation_schema_path": str(config.evaluation_schema_path.relative_to(config.root_dir)),
        "evaluation_profile": evaluation_profile["profile_id"],
        "evaluation_profile_path": str(config.evaluation_profile_path.relative_to(config.root_dir)),
        "output_files": {
            "requests": str(config.request_dir.relative_to(config.root_dir)),
            "raw": str(config.raw_dir.relative_to(config.root_dir)),
            "evaluations": str(config.evaluations_file.relative_to(config.root_dir)),
            "summary": str(config.summary_file.relative_to(config.root_dir)),
        },
    }
    write_json(config.manifest_file, manifest)


def parse_evaluation_record(
    evaluation_schema: dict[str, Any],
    trajectory_record: dict[str, Any],
    data: dict[str, Any],
) -> dict[str, Any]:
    message = data.get("choices", [{}])[0].get("message", {})
    tool_calls = message.get("tool_calls") or []
    record: dict[str, Any] = {
        "scenario_id": trajectory_record.get("scenario_id", ""),
        "sample_index": trajectory_record.get("sample_index"),
        "source_model": trajectory_record.get("model", ""),
        "evaluation_model": data.get("model", ""),
        "schema_version": evaluation_schema["schema_version"],
        "parse_status": "ok",
        "overall_verdict": "",
        "rewrite_priority": "",
        "axis_scores": {},
        "assistant_contamination_detected": None,
        "evaluation": None,
        "raw_content": message.get("content", ""),
    }
    if not tool_calls:
        record["parse_status"] = "no_tool_call"
        return record

    arguments_text = tool_calls[0].get("function", {}).get("arguments", "{}")
    try:
        evaluation = json.loads(arguments_text)
    except json.JSONDecodeError:
        record["parse_status"] = "bad_tool_json"
        record["raw_arguments"] = arguments_text
        return record

    axis_scores = {}
    for axis_value in evaluation.get("axis_results") or []:
        axis_id = axis_value.get("axis_id", "")
        if axis_id:
            axis_scores[axis_id] = axis_value.get("score")

    assistant_alignment = evaluation.get("assistant_alignment") or {}
    record.update(
        {
            "overall_verdict": evaluation.get("overall_verdict", ""),
            "rewrite_priority": evaluation.get("rewrite_priority", ""),
            "axis_scores": axis_scores,
            "assistant_contamination_detected": assistant_alignment.get(
                "assistant_contamination_detected"
            ),
            "evaluation": evaluation,
        }
    )

    usage = data.get("usage") or {}
    if usage:
        record["usage"] = usage
    return record


def write_summary(evaluations_file: Path, summary_file: Path) -> str:
    rows = load_jsonl(evaluations_file)
    lines = [f"records={len(rows)}"]

    verdict_counts: collections.Counter[str] = collections.Counter()
    rewrite_counts: collections.Counter[str] = collections.Counter()
    axis_score_map: dict[str, list[int]] = collections.defaultdict(list)
    contamination_true = 0

    for row in rows:
        verdict_counts[row.get("overall_verdict") or "<empty>"] += 1
        rewrite_counts[row.get("rewrite_priority") or "<empty>"] += 1
        if row.get("assistant_contamination_detected") is True:
            contamination_true += 1
        for axis_id, score in (row.get("axis_scores") or {}).items():
            if isinstance(score, int):
                axis_score_map[axis_id].append(score)

    lines.append(f"overall_verdict={dict(sorted(verdict_counts.items()))}")
    lines.append(f"rewrite_priority={dict(sorted(rewrite_counts.items()))}")
    lines.append(f"assistant_contamination_detected={contamination_true}/{len(rows) or 1}")
    lines.append("")
    lines.append("axis_average_scores:")
    for axis_id in sorted(axis_score_map):
        average = sum(axis_score_map[axis_id]) / len(axis_score_map[axis_id])
        lines.append(f"- {axis_id}: {average:.1f}")

    summary_text = "\n".join(lines) + "\n"
    summary_file.write_text(summary_text, encoding="utf-8")
    return summary_text


def run_evaluation(
    config: Config,
    trajectory_file: Path,
    evaluation_schema: dict[str, Any],
    evaluation_profile: dict[str, Any],
    trajectory_rows: list[dict[str, Any]],
) -> None:
    ensure_output_dirs(config)
    write_manifest(config, trajectory_file, evaluation_schema, evaluation_profile)

    print(f"run_id={config.run_id}")
    print(f"run_dir={config.run_dir}")
    print(
        f"model_profile={config.model_profile} model={config.model_id} temperature={config.temperature} evaluation_profile={evaluation_profile['profile_id']}"
    )
    print(f"trajectory_file={trajectory_file}")

    with config.evaluations_file.open("a", encoding="utf-8") as handle:
        for index, trajectory_record in enumerate(trajectory_rows, start=1):
            scenario_id = trajectory_record.get("scenario_id", "")
            if trajectory_record.get("parse_status") != "ok" or not trajectory_record.get("trajectory"):
                record = {
                    "scenario_id": scenario_id,
                    "sample_index": trajectory_record.get("sample_index"),
                    "source_model": trajectory_record.get("model", ""),
                    "evaluation_model": config.model_id,
                    "schema_version": evaluation_schema["schema_version"],
                    "parse_status": "skipped_no_valid_trajectory",
                    "overall_verdict": "",
                    "rewrite_priority": "",
                    "axis_scores": {},
                    "assistant_contamination_detected": None,
                    "evaluation": None,
                    "raw_content": "",
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                continue

            scenario = load_scenario(config, scenario_id)
            request_path = config.request_dir / f"{scenario_id}__{index:02d}.json"
            raw_path = config.raw_dir / f"{scenario_id}__{index:02d}.json"
            payload = build_request_payload(
                config,
                evaluation_schema,
                evaluation_profile,
                trajectory_record,
                scenario,
            )
            write_json(request_path, payload)

            print(f"evaluating scenario={scenario_id} record={index:02d}")
            response = post_chat_completion(config, payload)
            write_json(raw_path, response)

            record = parse_evaluation_record(evaluation_schema, trajectory_record, response)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()

    summary_text = write_summary(config.evaluations_file, config.summary_file)
    print(summary_text, end="")
    print(f"structured_results={config.evaluations_file}")


def main() -> None:
    args = parse_args()
    trajectory_file = Path(args.trajectory_file).resolve()
    config = load_config()
    validate_config(config, trajectory_file)
    evaluation_schema = load_json(config.evaluation_schema_path)
    evaluation_profile = load_json(config.evaluation_profile_path)
    trajectory_rows = load_jsonl(trajectory_file)
    run_evaluation(
        config,
        trajectory_file,
        evaluation_schema,
        evaluation_profile,
        trajectory_rows,
    )


if __name__ == "__main__":
    main()