#!/usr/bin/env python3

import argparse
import collections
import copy
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL_ID = "deepseek-v4-flash"
DEFAULT_MODEL_PROFILE = "debug"
DEFAULT_SAMPLES = 3
DEFAULT_TEMPERATURE = 0.7
DEFAULT_PROFILE = "analysis_teacher_compress_v1"
DEFAULT_SCHEMA_PATH = "schemas/state_trajectory_v1.json"
DEFAULT_TEACHER_SCHEMA_PATH = "schemas/teacher_analysis_v1.json"
DEFAULT_PIPELINE_MODE = "teacher_compress"
MODEL_PROFILE_TO_ID = {
    "debug": "deepseek-v4-flash",
    "release": "deepseek-v4-pro",
}


@dataclass(frozen=True)
class Config:
    root_dir: Path
    scenario_dir: Path
    schema_path: Path
    teacher_schema_path: Path
    results_dir: Path
    base_url: str
    model_profile: str
    model_id: str
    samples: int
    temperature: float
    profile: str
    pipeline_mode: str
    run_id: str
    run_dir: Path
    request_dir: Path
    raw_dir: Path
    teacher_request_dir: Path
    teacher_raw_dir: Path
    teacher_analysis_file: Path
    trajectories_file: Path
    summary_file: Path
    manifest_file: Path
    api_key: str
    repair_instruction_path: Path | None
    repair_instruction: dict[str, Any] | None
    reuse_teacher_analysis_path: Path | None
    reuse_teacher_record: dict[str, Any] | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate state-trajectory training samples with DeepSeek.",
    )
    parser.add_argument("scenarios", nargs="*", help="Optional scenario ids to run.")
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
    schema_path = root_dir / os.environ.get("SCHEMA_FILE", DEFAULT_SCHEMA_PATH)
    teacher_schema_path = root_dir / os.environ.get(
        "TEACHER_SCHEMA_FILE",
        DEFAULT_TEACHER_SCHEMA_PATH,
    )
    results_dir = Path(os.environ.get("RESULTS_DIR", str(root_dir / "results")))
    run_id = os.environ.get(
        "RUN_ID",
        datetime.now(timezone.utc).strftime("trajectory-%Y%m%dT%H%M%SZ"),
    )
    repair_instruction_path_text = os.environ.get("REPAIR_INSTRUCTION_FILE", "").strip()
    repair_instruction_path = None
    repair_instruction = None
    if repair_instruction_path_text:
        repair_instruction_path = Path(repair_instruction_path_text).expanduser().resolve()
        repair_instruction = json.loads(repair_instruction_path.read_text(encoding="utf-8"))

    reuse_teacher_analysis_path_text = os.environ.get("REUSE_TEACHER_ANALYSIS_FILE", "").strip()
    reuse_teacher_analysis_path = None
    reuse_teacher_record = None
    if reuse_teacher_analysis_path_text:
        reuse_teacher_analysis_path = Path(reuse_teacher_analysis_path_text).expanduser().resolve()
        reuse_teacher_record = load_reuse_teacher_record(reuse_teacher_analysis_path)

    run_dir = results_dir / run_id
    model_profile, model_id = resolve_model_settings()
    return Config(
        root_dir=root_dir,
        scenario_dir=scenario_dir,
        schema_path=schema_path,
        teacher_schema_path=teacher_schema_path,
        results_dir=results_dir,
        base_url=os.environ.get("BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model_profile=model_profile,
        model_id=model_id,
        samples=int(os.environ.get("SAMPLES", str(DEFAULT_SAMPLES))),
        temperature=float(os.environ.get("TEMPERATURE", str(DEFAULT_TEMPERATURE))),
        profile=os.environ.get("TRAJECTORY_PROFILE", DEFAULT_PROFILE),
        pipeline_mode=os.environ.get("TRAJECTORY_PIPELINE", DEFAULT_PIPELINE_MODE),
        run_id=run_id,
        run_dir=run_dir,
        request_dir=run_dir / "trajectory_requests",
        raw_dir=run_dir / "trajectory_raw",
        teacher_request_dir=run_dir / "teacher_requests",
        teacher_raw_dir=run_dir / "teacher_raw",
        teacher_analysis_file=run_dir / "teacher_analyses.jsonl",
        trajectories_file=run_dir / "trajectories.jsonl",
        summary_file=run_dir / "trajectory_summary.txt",
        manifest_file=run_dir / "manifest.json",
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        repair_instruction_path=repair_instruction_path,
        repair_instruction=repair_instruction,
        reuse_teacher_analysis_path=reuse_teacher_analysis_path,
        reuse_teacher_record=reuse_teacher_record,
    )


def validate_config(config: Config) -> None:
    if not config.api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required.")
    if config.samples <= 0:
        raise SystemExit("SAMPLES must be a positive integer.")
    if not config.schema_path.is_file():
        raise SystemExit(f"Schema file not found: {config.schema_path}")
    if config.pipeline_mode not in {"single_stage", "teacher_compress"}:
        raise SystemExit("TRAJECTORY_PIPELINE must be one of: single_stage, teacher_compress")
    if config.pipeline_mode == "teacher_compress" and not config.teacher_schema_path.is_file():
        raise SystemExit(f"Teacher schema file not found: {config.teacher_schema_path}")
    if config.repair_instruction_path is not None and not config.repair_instruction_path.is_file():
        raise SystemExit(f"Repair instruction file not found: {config.repair_instruction_path}")
    if config.reuse_teacher_analysis_path is not None:
        if config.pipeline_mode != "teacher_compress":
            raise SystemExit("REUSE_TEACHER_ANALYSIS_FILE requires TRAJECTORY_PIPELINE=teacher_compress")
        if config.samples != 1:
            raise SystemExit("REUSE_TEACHER_ANALYSIS_FILE currently requires SAMPLES=1")
        if not config.reuse_teacher_analysis_path.is_file():
            raise SystemExit(f"Reuse teacher analysis file not found: {config.reuse_teacher_analysis_path}")


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


def load_teacher_schema_template(config: Config) -> dict[str, Any]:
    return json.loads(config.teacher_schema_path.read_text(encoding="utf-8"))


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


def build_repair_instruction_block(config: Config, stage: str) -> str:
    repair_instruction = config.repair_instruction or {}
    if not repair_instruction or repair_instruction.get("repair_target") != stage:
        return ""

    trigger = repair_instruction.get("trigger") or {}
    preserve = repair_instruction.get("preserve") or {}
    lines = [
        "修复任务（这是定向重跑，不是从零自由发挥）：",
        f"- next_action: {repair_instruction.get('next_action', '')}",
        f"- repair_target: {repair_instruction.get('repair_target', '')}",
        f"- primary_failure: {trigger.get('primary_failure_id', '')}",
    ]

    failure_ids = trigger.get("failure_ids") or []
    if failure_ids:
        lines.append(f"- failure_ids: {', '.join(failure_ids)}")

    for focus in repair_instruction.get("repair_focus") or []:
        lines.append(f"- 优先修复: {focus}")

    chosen_action_label = preserve.get("chosen_action_label")
    if chosen_action_label and stage == "trajectory_prompt":
        lines.append(
            f"- 这是局部修复。若不与 failure 根因冲突，尽量保持 chosen_action.action_label={chosen_action_label}。"
        )

    for item in preserve.get("must_keep") or []:
        lines.append(f"- 尽量保留: {item}")

    for strength in preserve.get("strengths") or []:
        lines.append(f"- 已有优点不要打掉: {strength}")

    if stage == "teacher_stage":
        lines.append("- 这是教师阶段重推。必要时可以重新推导 action_assessment 和 recommended_packet，但要优先修根因。")
    else:
        lines.append("- 这是压缩/轨迹层局部修复。除非 failure 明确涉及动作选择或世界模型错误，不要无故重写关系框架、长期目标和主动作方向。")

    rationale = repair_instruction.get("rationale", "")
    if rationale:
        lines.append(f"- repair rationale: {rationale}")

    return "\n".join(lines)


def build_single_stage_prompt(
    config: Config,
    schema_template: dict[str, Any],
    scenario: dict[str, Any],
) -> tuple[str, str, list[str]]:
    action_labels, option_lines = action_option_lines(scenario)
    tool_name = schema_template["tool_name"]
    repair_block = build_repair_instruction_block(config, "trajectory_prompt")
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
    if repair_block:
        user_prompt += f"\n修复上下文：\n{repair_block}\n"
    return system_prompt, user_prompt, action_labels


def patch_action_label_enums(parameters: dict[str, Any], action_labels: list[str]) -> None:
    if not action_labels:
        return

    if "candidate_actions" in parameters.get("properties", {}):
        parameters["properties"]["candidate_actions"]["items"]["properties"]["action_label"]["enum"] = action_labels
    if "chosen_action" in parameters.get("properties", {}):
        parameters["properties"]["chosen_action"]["properties"]["action_label"]["enum"] = action_labels
    if "action_assessment" in parameters.get("properties", {}):
        parameters["properties"]["action_assessment"]["items"]["properties"]["action_label"]["enum"] = action_labels
    if "recommended_packet" in parameters.get("properties", {}):
        parameters["properties"]["recommended_packet"]["properties"]["action_label"]["enum"] = action_labels


def build_tool(schema_template: dict[str, Any], action_labels: list[str]) -> dict[str, Any]:
    parameters = copy.deepcopy(schema_template["parameters"])
    patch_action_label_enums(parameters, action_labels)

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
    system_prompt, user_prompt, action_labels = build_single_stage_prompt(
        config,
        schema_template,
        scenario,
    )
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


def build_teacher_prompt(
    config: Config,
    teacher_schema_template: dict[str, Any],
    scenario: dict[str, Any],
) -> tuple[str, str, list[str]]:
    action_labels, option_lines = action_option_lines(scenario)
    tool_name = teacher_schema_template["tool_name"]
    repair_block = build_repair_instruction_block(config, "teacher_stage")
    system_prompt = (
        "你是分析教师，不是聊天助手。你的任务是先产出一份较充分但仍聚焦的角色分析中间稿，"
        "供第二阶段压缩器进一步压成更短、更稀疏的训练样本。\n\n"
        "要求：\n"
        "1. 以角色一致性、世界模型一致性和动作可执行性为第一优先级。\n"
        "2. 保留足够的信息来支持压缩，但不要写故事散文，也不要写道德说教。\n"
        "3. action_assessment 要体现真实权衡与失败方式。\n"
        "4. compression_guidance.keep 里放压缩阶段必须保留的少数关键信息；trim 里放可删减的冗余。\n"
        "5. 默认世界里没有 user/system 身份优先级，只有角色、世界、关系、责任与目标。\n"
        f"6. 你必须且只能调用一次 {tool_name} 函数，不要输出函数外文本。"
    )
    user_prompt = (
        f"生成配置：\n- profile: {config.profile}\n- pipeline: {config.pipeline_mode}\n"
        f"- teacher_schema_version: {teacher_schema_template['schema_version']}\n\n"
        "请为下面这个第三人称场景写一份‘分析教师中间稿’。\n\n"
        f"场景 ID：{scenario['id']}\n"
        f"角色名：{scenario['name']}\n\n"
        f"人物经历与稳定特征：\n{scenario['profile']}\n\n"
        f"当前情景：\n{scenario['situation']}\n\n"
        f"当前任务表述：\n{scenario['task']}\n\n"
        "可用动作标签：\n"
        f"{option_lines}\n\n"
        "输出提醒：\n"
        "- 这是给压缩器看的教师草稿，不是最终训练样本。\n"
        "- recommended_packet 应给出你最看好的动作包。\n"
        "- compression_guidance.target_style 应指向‘短、稀疏、可续写、低 assistant 污染’。\n"
    )
    if repair_block:
        user_prompt += f"\n修复上下文：\n{repair_block}\n"
    return system_prompt, user_prompt, action_labels


def build_teacher_request_payload(
    config: Config,
    teacher_schema_template: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    system_prompt, user_prompt, action_labels = build_teacher_prompt(
        config,
        teacher_schema_template,
        scenario,
    )
    return {
        "model": config.model_id,
        "temperature": config.temperature,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [build_tool(teacher_schema_template, action_labels)],
    }


def build_compressor_prompt(
    config: Config,
    schema_template: dict[str, Any],
    scenario: dict[str, Any],
    teacher_analysis: dict[str, Any],
) -> tuple[str, str, list[str]]:
    action_labels, option_lines = action_option_lines(scenario)
    tool_name = schema_template["tool_name"]
    teacher_analysis_text = json.dumps(teacher_analysis, ensure_ascii=False, indent=2)
    repair_block = build_repair_instruction_block(config, "trajectory_prompt")
    system_prompt = (
        "你是状态轨迹压缩器。你的输入是一份分析教师中间稿，你的任务是把它压成更短、更稀疏、更适合未来训练的单步状态轨迹。\n\n"
        "压缩原则：\n"
        "1. 只保留真正会影响下一步行为分布的状态。\n"
        "2. 不要把教师分析原样搬运成长段文字；优先使用短句、低冗余条目。\n"
        "3. visible_world、recalled_memory、self_state、inferred_latents 尽量控制在 2-4 条。\n"
        "4. candidate_actions 尽量控制在 2-3 个，并保留真实权衡。\n"
        "5. state_updates 只写有必要的增量，不做思维链转储。\n"
        "6. 保持 assistant 污染低，不要出现‘为了帮助用户’‘作为助手’之类的外部协议语言。\n"
        f"7. 你必须且只能调用一次 {tool_name} 函数，不要输出函数外文本。"
    )
    user_prompt = (
        f"生成配置：\n- profile: {config.profile}\n- pipeline: {config.pipeline_mode}\n"
        f"- final_schema_version: {schema_template['schema_version']}\n\n"
        "请把下面的教师中间稿压成最终状态轨迹样本。\n\n"
        f"场景 ID：{scenario['id']}\n"
        f"角色名：{scenario['name']}\n\n"
        "可用动作标签：\n"
        f"{option_lines}\n\n"
        "教师中间稿：\n"
        f"{teacher_analysis_text}\n\n"
        "输出提醒：\n"
        "- chosen_action.action_label 应与教师推荐一致，除非教师分析内部自相矛盾。\n"
        "- relationship_frame 至少保留 1-2 个真正影响此步的关键关系。\n"
        "- quality_control.notes 用一句短说明解释压缩后的关键取舍。\n"
    )
    if repair_block:
        user_prompt += f"\n修复上下文：\n{repair_block}\n"
    return system_prompt, user_prompt, action_labels


def build_compressor_request_payload(
    config: Config,
    schema_template: dict[str, Any],
    scenario: dict[str, Any],
    teacher_analysis: dict[str, Any],
) -> dict[str, Any]:
    system_prompt, user_prompt, action_labels = build_compressor_prompt(
        config,
        schema_template,
        scenario,
        teacher_analysis,
    )
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


def repair_common_tool_json(arguments_text: str) -> str:
    repaired = arguments_text.strip()
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    repaired = re.sub(
        r',\s*"immediate"\s*:\s*\[[\s\S]*?\]\s*}\s*,\s*"inferred_latents"',
        ', "inferred_latents"',
        repaired,
    )
    repaired = append_missing_json_closers(repaired)
    return repaired


def append_missing_json_closers(arguments_text: str) -> str:
    stack: list[str] = []
    in_string = False
    is_escaped = False

    for char in arguments_text:
        if in_string:
            if is_escaped:
                is_escaped = False
            elif char == "\\":
                is_escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char in "[{":
            stack.append(char)
            continue
        if char == "}" and stack and stack[-1] == "{":
            stack.pop()
            continue
        if char == "]" and stack and stack[-1] == "[":
            stack.pop()

    closers: list[str] = []
    while stack:
        opener = stack.pop()
        closers.append("}" if opener == "{" else "]")
    return arguments_text + "".join(closers)


def load_tool_arguments(arguments_text: str) -> dict[str, Any]:
    try:
        return json.loads(arguments_text)
    except json.JSONDecodeError:
        repaired = repair_common_tool_json(arguments_text)
        if repaired != arguments_text:
            return json.loads(repaired)
        raise


def normalize_world_model_consistency(payload: dict[str, Any]) -> None:
    quality_control = payload.get("quality_control") or {}
    consistency = quality_control.get("world_model_consistency")
    if isinstance(consistency, str) and consistency.isdigit():
        quality_control["world_model_consistency"] = int(consistency)


def normalize_teacher_analysis_shape(teacher_analysis: dict[str, Any]) -> None:
    recommended_packet = teacher_analysis.get("recommended_packet") or {}
    if (
        "compression_guidance" not in teacher_analysis
        and isinstance(recommended_packet.get("compression_guidance"), dict)
    ):
        teacher_analysis["compression_guidance"] = recommended_packet.pop("compression_guidance")

    compression_guidance = teacher_analysis.get("compression_guidance") or {}
    if "quality_control" not in teacher_analysis:
        nested_quality_control = None
        if isinstance(compression_guidance.get("quality_control"), dict):
            nested_quality_control = compression_guidance.pop("quality_control")
        elif isinstance(recommended_packet.get("quality_control"), dict):
            nested_quality_control = recommended_packet.pop("quality_control")
        if nested_quality_control is not None:
            teacher_analysis["quality_control"] = nested_quality_control


def load_reuse_teacher_record(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "teacher_analysis" in payload:
        teacher_record = copy.deepcopy(payload)
    else:
        teacher_record = {
            "scenario_id": payload.get("scenario_id", ""),
            "profile": payload.get("profile", ""),
            "sample_index": payload.get("sample_index", 1),
            "schema_version": payload.get("schema_version", "teacher_analysis_v1"),
            "model": payload.get("model", "reused_teacher_analysis"),
            "finish_reason": payload.get("finish_reason", "reused_teacher_analysis"),
            "parse_status": payload.get("parse_status", "ok"),
            "recommended_action_label": payload.get("recommended_action_label", ""),
            "assistant_contamination_risk": payload.get("assistant_contamination_risk", ""),
            "over_explaining_risk": payload.get("over_explaining_risk", ""),
            "world_model_consistency": payload.get("world_model_consistency"),
            "teacher_analysis": payload,
            "raw_content": payload.get("raw_content", ""),
        }

    teacher_analysis = teacher_record.get("teacher_analysis") or {}
    normalize_teacher_analysis_shape(teacher_analysis)
    normalize_world_model_consistency(teacher_analysis)
    teacher_record["teacher_analysis"] = teacher_analysis
    teacher_record["recommended_action_label"] = teacher_record.get("recommended_action_label") or (
        teacher_analysis.get("recommended_packet") or {}
    ).get("action_label", "")
    quality_control = teacher_analysis.get("quality_control") or {}
    teacher_record["assistant_contamination_risk"] = teacher_record.get(
        "assistant_contamination_risk",
        "",
    ) or quality_control.get("assistant_contamination_risk", "")
    teacher_record["over_explaining_risk"] = teacher_record.get(
        "over_explaining_risk",
        "",
    ) or quality_control.get("over_explaining_risk", "")
    teacher_record["world_model_consistency"] = teacher_record.get(
        "world_model_consistency"
    ) or quality_control.get("world_model_consistency")
    teacher_record["parse_status"] = teacher_record.get("parse_status", "ok")
    return teacher_record


def materialize_reused_teacher_record(
    template_record: dict[str, Any],
    scenario_id: str,
    profile: str,
    sample_index: int,
) -> dict[str, Any]:
    teacher_record = copy.deepcopy(template_record)
    teacher_record["scenario_id"] = scenario_id
    teacher_record["profile"] = profile
    teacher_record["sample_index"] = sample_index
    teacher_record["parse_status"] = teacher_record.get("parse_status", "ok")
    teacher_record["finish_reason"] = teacher_record.get("finish_reason", "reused_teacher_analysis")
    teacher_record["model"] = teacher_record.get("model", "reused_teacher_analysis")
    return teacher_record


def parse_trajectory_record(
    schema_template: dict[str, Any],
    scenario_id: str,
    profile: str,
    pipeline_mode: str,
    sample_index: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    message = data.get("choices", [{}])[0].get("message", {})
    tool_calls = message.get("tool_calls") or []

    record: dict[str, Any] = {
        "scenario_id": scenario_id,
        "profile": profile,
        "pipeline_mode": pipeline_mode,
        "sample_index": sample_index,
        "schema_version": schema_template["schema_version"],
        "model": data.get("model", ""),
        "finish_reason": data.get("choices", [{}])[0].get("finish_reason", ""),
        "parse_status": "ok",
        "teacher_parse_status": "",
        "teacher_recommended_action_label": "",
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
        trajectory = load_tool_arguments(arguments_text)
    except json.JSONDecodeError:
        record["parse_status"] = "bad_tool_json"
        record["raw_arguments"] = arguments_text
        return record

    normalize_world_model_consistency(trajectory)
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


def parse_teacher_analysis_record(
    teacher_schema_template: dict[str, Any],
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
        "schema_version": teacher_schema_template["schema_version"],
        "model": data.get("model", ""),
        "finish_reason": data.get("choices", [{}])[0].get("finish_reason", ""),
        "parse_status": "ok",
        "recommended_action_label": "",
        "assistant_contamination_risk": "",
        "over_explaining_risk": "",
        "world_model_consistency": None,
        "teacher_analysis": None,
        "raw_content": message.get("content", ""),
    }

    if not tool_calls:
        record["parse_status"] = "no_tool_call"
        return record

    arguments_text = tool_calls[0].get("function", {}).get("arguments", "{}")
    try:
        teacher_analysis = load_tool_arguments(arguments_text)
    except json.JSONDecodeError:
        record["parse_status"] = "bad_tool_json"
        record["raw_arguments"] = arguments_text
        return record

    normalize_teacher_analysis_shape(teacher_analysis)
    normalize_world_model_consistency(teacher_analysis)
    quality_control = teacher_analysis.get("quality_control", {})
    recommended_packet = teacher_analysis.get("recommended_packet", {})
    record.update(
        {
            "recommended_action_label": recommended_packet.get("action_label", ""),
            "assistant_contamination_risk": quality_control.get("assistant_contamination_risk", ""),
            "over_explaining_risk": quality_control.get("over_explaining_risk", ""),
            "world_model_consistency": quality_control.get("world_model_consistency"),
            "teacher_analysis": teacher_analysis,
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
    if config.pipeline_mode == "teacher_compress":
        config.teacher_request_dir.mkdir(parents=True, exist_ok=True)
        config.teacher_raw_dir.mkdir(parents=True, exist_ok=True)
        config.teacher_analysis_file.write_text("", encoding="utf-8")
    config.trajectories_file.write_text("", encoding="utf-8")


def write_manifest(
    config: Config,
    schema_template: dict[str, Any],
    teacher_schema_template: dict[str, Any] | None,
    scenarios: list[dict[str, Any]],
) -> None:
    manifest = {
        "run_id": config.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": config.base_url,
        "model_profile": config.model_profile,
        "model_id": config.model_id,
        "temperature": config.temperature,
        "samples": config.samples,
        "profile": config.profile,
        "pipeline_mode": config.pipeline_mode,
        "schema_version": schema_template["schema_version"],
        "schema_path": str(config.schema_path.relative_to(config.root_dir)),
        "repair_instruction_ref": str(config.repair_instruction_path) if config.repair_instruction_path else None,
        "repair_target": (config.repair_instruction or {}).get("repair_target") if config.repair_instruction else None,
        "reuse_teacher_analysis_ref": str(config.reuse_teacher_analysis_path) if config.reuse_teacher_analysis_path else None,
        "scenarios": [scenario["id"] for scenario in scenarios],
        "output_files": {
            "requests": str(config.request_dir.relative_to(config.root_dir)),
            "raw": str(config.raw_dir.relative_to(config.root_dir)),
            "trajectories": str(config.trajectories_file.relative_to(config.root_dir)),
            "summary": str(config.summary_file.relative_to(config.root_dir))
        }
    }
    if teacher_schema_template is not None:
        manifest["teacher_schema_version"] = teacher_schema_template["schema_version"]
        manifest["teacher_schema_path"] = str(config.teacher_schema_path.relative_to(config.root_dir))
        manifest["output_files"]["teacher_requests"] = str(
            config.teacher_request_dir.relative_to(config.root_dir)
        )
        manifest["output_files"]["teacher_raw"] = str(
            config.teacher_raw_dir.relative_to(config.root_dir)
        )
        manifest["output_files"]["teacher_analyses"] = str(
            config.teacher_analysis_file.relative_to(config.root_dir)
        )
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
        visible_world_sizes: list[int] = []
        candidate_action_sizes: list[int] = []
        state_update_sizes: list[int] = []

        for row in subset:
            parse_counts[row.get("parse_status", "unknown")] += 1
            label_counts[row.get("chosen_action_label") or "<empty>"] += 1
            contamination_counts[row.get("assistant_contamination_risk") or "<empty>"] += 1
            consistency = row.get("world_model_consistency")
            if isinstance(consistency, int):
                consistency_values.append(consistency)
            trajectory = row.get("trajectory") or {}
            if trajectory:
                visible_world_sizes.append(len(trajectory.get("visible_world") or []))
                candidate_action_sizes.append(len(trajectory.get("candidate_actions") or []))
                state_updates = trajectory.get("state_updates") or {}
                state_update_sizes.append(
                    sum(len(state_updates.get(key) or []) for key in ["memory_notebook", "goal_tree", "self_state", "world_model"])
                )

        lines.append("")
        lines.append(f"[{scenario_id}] profile={profile} samples={len(subset)}")
        lines.append(f"parse_status={dict(sorted(parse_counts.items()))}")
        lines.append(f"assistant_contamination_risk={dict(sorted(contamination_counts.items()))}")
        if consistency_values:
            average = sum(consistency_values) / len(consistency_values)
            lines.append(f"avg_world_model_consistency={average:.1f}")
        if visible_world_sizes:
            lines.append(
                f"avg_visible_world_items={sum(visible_world_sizes) / len(visible_world_sizes):.1f}"
            )
        if candidate_action_sizes:
            lines.append(
                f"avg_candidate_actions={sum(candidate_action_sizes) / len(candidate_action_sizes):.1f}"
            )
        if state_update_sizes:
            lines.append(
                f"avg_state_update_entries={sum(state_update_sizes) / len(state_update_sizes):.1f}"
            )

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
    teacher_schema_template: dict[str, Any] | None,
    scenarios: list[dict[str, Any]],
) -> None:
    ensure_output_dirs(config)
    write_manifest(config, schema_template, teacher_schema_template, scenarios)

    print(f"run_id={config.run_id}")
    print(f"run_dir={config.run_dir}")
    print(
        f"model_profile={config.model_profile} model={config.model_id} samples={config.samples} temperature={config.temperature} profile={config.profile}"
    )
    print(f"pipeline={config.pipeline_mode}")
    print(f"schema={config.schema_path}")
    if teacher_schema_template is not None:
        print(f"teacher_schema={config.teacher_schema_path}")

    teacher_handle = None
    if config.pipeline_mode == "teacher_compress":
        teacher_handle = config.teacher_analysis_file.open("a", encoding="utf-8")

    with config.trajectories_file.open("a", encoding="utf-8") as trajectory_handle:
        try:
            for scenario in scenarios:
                scenario_id = scenario["id"]
                for index in range(1, config.samples + 1):
                    tag = sample_tag(index)

                    if config.pipeline_mode == "single_stage":
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
                            config.pipeline_mode,
                            index,
                            response,
                        )
                        trajectory_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        trajectory_handle.flush()
                        continue

                    teacher_request_path = config.teacher_request_dir / f"{scenario_id}__{tag}.json"
                    teacher_raw_path = config.teacher_raw_dir / f"{scenario_id}__{tag}.json"
                    if config.reuse_teacher_record is not None:
                        print(f"reusing_teacher_analysis scenario={scenario_id} sample={tag}")
                        teacher_record = materialize_reused_teacher_record(
                            config.reuse_teacher_record,
                            scenario_id,
                            config.profile,
                            index,
                        )
                    else:
                        teacher_payload = build_teacher_request_payload(
                            config,
                            teacher_schema_template,
                            scenario,
                        )
                        write_json(teacher_request_path, teacher_payload)

                        print(f"teacher_request scenario={scenario_id} sample={tag}")
                        teacher_response = post_chat_completion(config, teacher_payload)
                        write_json(teacher_raw_path, teacher_response)
                        teacher_record = parse_teacher_analysis_record(
                            teacher_schema_template,
                            scenario_id,
                            config.profile,
                            index,
                            teacher_response,
                        )
                    teacher_handle.write(json.dumps(teacher_record, ensure_ascii=False) + "\n")
                    teacher_handle.flush()

                    if teacher_record["parse_status"] != "ok" or not teacher_record.get("teacher_analysis"):
                        failed_record = {
                            "scenario_id": scenario_id,
                            "profile": config.profile,
                            "pipeline_mode": config.pipeline_mode,
                            "sample_index": index,
                            "schema_version": schema_template["schema_version"],
                            "model": teacher_record.get("model", ""),
                            "finish_reason": teacher_record.get("finish_reason", ""),
                            "parse_status": f"teacher_{teacher_record['parse_status']}",
                            "teacher_parse_status": teacher_record["parse_status"],
                            "teacher_recommended_action_label": teacher_record.get("recommended_action_label", ""),
                            "chosen_action_label": "",
                            "assistant_contamination_risk": teacher_record.get("assistant_contamination_risk", ""),
                            "over_explaining_risk": teacher_record.get("over_explaining_risk", ""),
                            "world_model_consistency": teacher_record.get("world_model_consistency"),
                            "trajectory": None,
                            "raw_content": teacher_record.get("raw_content", ""),
                        }
                        if teacher_record.get("usage"):
                            failed_record["teacher_usage"] = teacher_record["usage"]
                        trajectory_handle.write(json.dumps(failed_record, ensure_ascii=False) + "\n")
                        trajectory_handle.flush()
                        continue

                    request_path = config.request_dir / f"{scenario_id}__{tag}.json"
                    raw_path = config.raw_dir / f"{scenario_id}__{tag}.json"
                    compressor_payload = build_compressor_request_payload(
                        config,
                        schema_template,
                        scenario,
                        teacher_record["teacher_analysis"],
                    )
                    write_json(request_path, compressor_payload)

                    print(f"compress_request scenario={scenario_id} sample={tag}")
                    response = post_chat_completion(config, compressor_payload)
                    write_json(raw_path, response)

                    record = parse_trajectory_record(
                        schema_template,
                        scenario_id,
                        config.profile,
                        config.pipeline_mode,
                        index,
                        response,
                    )
                    record["teacher_parse_status"] = teacher_record["parse_status"]
                    record["teacher_recommended_action_label"] = teacher_record.get(
                        "recommended_action_label",
                        "",
                    )
                    if teacher_record.get("usage"):
                        record["teacher_usage"] = teacher_record["usage"]
                    trajectory_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    trajectory_handle.flush()
        finally:
            if teacher_handle is not None:
                teacher_handle.close()

    summary_text = write_summary(config.trajectories_file, config.summary_file)
    print(summary_text, end="")
    print(f"structured_results={config.trajectories_file}")


def main() -> None:
    args = parse_args()
    config = load_config()
    validate_config(config)
    schema_template = load_schema_template(config)
    teacher_schema_template = None
    if config.pipeline_mode == "teacher_compress":
        teacher_schema_template = load_teacher_schema_template(config)
    scenarios = discover_scenarios(config, args.scenarios)
    run_generation(config, schema_template, teacher_schema_template, scenarios)


if __name__ == "__main__":
    main()