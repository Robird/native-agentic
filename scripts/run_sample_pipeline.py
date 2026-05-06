#!/usr/bin/env python3

import argparse
import collections
import copy
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packet_review_kernel import (
    AttemptContext,
    ProvenancePaths,
    build_sample_packet,
    rel_to_root,
    resolve_provenance_paths,
    scenario_snapshot,
    validate_packet,
)


DEFAULT_RESULTS_DIR = "results"
DEFAULT_RUN_ID_PREFIX = "sample-pipeline"
DEFAULT_SAMPLE_PACKET_SCHEMA_PATH = "schemas/sample_packet_v1.json"
DEFAULT_REPAIR_INSTRUCTION_SCHEMA_PATH = "schemas/repair_instruction_v1.json"
DEFAULT_TEACHER_INPUT_SCHEMA_PATH = "schemas/teacher_agent_input_v1.json"
DEFAULT_EVALUATOR_INPUT_SCHEMA_PATH = "schemas/evaluator_agent_input_v1.json"
DEFAULT_EVALUATION_PROFILE_PATH = "profiles/evaluation_profile_constitutional_v1.json"
DEFAULT_EVALUATION_SCHEMA_PATH = "schemas/trajectory_evaluation_v1.json"
DEFAULT_TRAJECTORY_PROFILE = "analysis_teacher_compress_v1"
DEFAULT_TRAJECTORY_PIPELINE = "teacher_compress"
PIPELINE_MANIFEST_VERSION = "pipeline_manifest_index_v1"
GENERATOR_STAGE_RUN_ID = "generate"
EVALUATOR_STAGE_RUN_ID = "evaluate"
DEFAULT_SAMPLES = 3
DEFAULT_MAX_REPAIR_ATTEMPTS = 1
DEFAULT_MIN_REPAIR_SCORE_DELTA = 1.0

EXECUTABLE_REPAIR_ACTIONS = {
    "revise_prompt_local",
    "regenerate_from_teacher",
}

VERDICT_ORDER = {
    "": 0,
    "reject": 0,
    "manual_review": 1,
    "revise": 2,
    "keep": 3,
}


@dataclass(frozen=True)
class Config:
    root_dir: Path
    scenario_dir: Path
    results_dir: Path
    run_id: str
    run_dir: Path
    repairs_dir: Path
    packet_dir: Path
    teacher_input_dir: Path
    evaluator_input_dir: Path
    sample_packets_file: Path
    repair_summary_file: Path
    repair_report_file: Path
    summary_file: Path
    manifest_file: Path
    sample_packet_schema_path: Path
    repair_instruction_schema_path: Path
    teacher_input_schema_path: Path
    evaluator_input_schema_path: Path
    generator_script: Path
    evaluator_script: Path
    samples: int
    write_interfaces: bool
    auto_repair: bool
    max_repair_attempts: int
    stop_on_no_progress: bool
    min_repair_score_delta: float
    trajectory_profile: str
    trajectory_pipeline: str
    evaluation_profile_file: str
    evaluation_schema_file: str

    @property
    def generator_run_id(self) -> str:
        return GENERATOR_STAGE_RUN_ID

    @property
    def evaluator_run_id(self) -> str:
        return EVALUATOR_STAGE_RUN_ID


@dataclass(frozen=True)
class RepairAttemptResult:
    attempt_dir: Path
    instruction: dict[str, Any]
    instruction_path: Path
    reused_teacher_analysis: bool
    generation_manifest: dict[str, Any]
    trajectory_row: dict[str, Any]
    teacher_row: dict[str, Any] | None
    evaluation_manifest: dict[str, Any]
    evaluation_row: dict[str, Any] | None
    provenance_paths: ProvenancePaths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run generation and evaluation as one packetized sample pipeline.",
    )
    parser.add_argument("scenarios", nargs="*", help="Optional scenario ids to run.")
    return parser.parse_args()


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> Config:
    root_dir = Path(__file__).resolve().parent.parent
    results_dir = Path(os.environ.get("RESULTS_DIR", str(root_dir / DEFAULT_RESULTS_DIR)))
    run_id = os.environ.get(
        "RUN_ID",
        datetime.now(timezone.utc).strftime(f"{DEFAULT_RUN_ID_PREFIX}-%Y%m%dT%H%M%SZ"),
    )
    run_dir = results_dir / run_id
    return Config(
        root_dir=root_dir,
        scenario_dir=root_dir / "data" / "scenarios",
        results_dir=results_dir,
        run_id=run_id,
        run_dir=run_dir,
        repairs_dir=run_dir / "repair_attempts",
        packet_dir=run_dir / "sample_packets",
        teacher_input_dir=run_dir / "interfaces" / "teacher_inputs",
        evaluator_input_dir=run_dir / "interfaces" / "evaluator_inputs",
        sample_packets_file=run_dir / "sample_packets.jsonl",
        repair_summary_file=run_dir / "repair_summary.jsonl",
        repair_report_file=run_dir / "repair_summary.txt",
        summary_file=run_dir / "pipeline_summary.txt",
        manifest_file=run_dir / "pipeline_manifest.json",
        sample_packet_schema_path=root_dir
        / os.environ.get("SAMPLE_PACKET_SCHEMA_FILE", DEFAULT_SAMPLE_PACKET_SCHEMA_PATH),
        repair_instruction_schema_path=root_dir
        / os.environ.get("REPAIR_INSTRUCTION_SCHEMA_FILE", DEFAULT_REPAIR_INSTRUCTION_SCHEMA_PATH),
        teacher_input_schema_path=root_dir
        / os.environ.get("TEACHER_INPUT_SCHEMA_FILE", DEFAULT_TEACHER_INPUT_SCHEMA_PATH),
        evaluator_input_schema_path=root_dir
        / os.environ.get("EVALUATOR_INPUT_SCHEMA_FILE", DEFAULT_EVALUATOR_INPUT_SCHEMA_PATH),
        generator_script=root_dir / "scripts" / "generate_state_trajectories.py",
        evaluator_script=root_dir / "scripts" / "evaluate_trajectories.py",
        samples=int(os.environ.get("SAMPLES", str(DEFAULT_SAMPLES))),
        write_interfaces=env_flag("WRITE_INTERFACES", False),
        auto_repair=env_flag("AUTO_REPAIR", False),
        max_repair_attempts=int(
            os.environ.get("MAX_REPAIR_ATTEMPTS", str(DEFAULT_MAX_REPAIR_ATTEMPTS))
        ),
        stop_on_no_progress=env_flag("STOP_ON_NO_PROGRESS", True),
        min_repair_score_delta=float(
            os.environ.get("MIN_REPAIR_SCORE_DELTA", str(DEFAULT_MIN_REPAIR_SCORE_DELTA))
        ),
        trajectory_profile=os.environ.get("TRAJECTORY_PROFILE", DEFAULT_TRAJECTORY_PROFILE),
        trajectory_pipeline=os.environ.get("TRAJECTORY_PIPELINE", DEFAULT_TRAJECTORY_PIPELINE),
        evaluation_profile_file=os.environ.get(
            "EVALUATION_PROFILE_FILE",
            DEFAULT_EVALUATION_PROFILE_PATH,
        ),
        evaluation_schema_file=os.environ.get(
            "EVALUATION_SCHEMA_FILE",
            DEFAULT_EVALUATION_SCHEMA_PATH,
        ),
    )


def validate_config(config: Config) -> None:
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise SystemExit("DEEPSEEK_API_KEY is required.")
    if config.samples <= 0:
        raise SystemExit("SAMPLES must be a positive integer.")
    for path in [
        config.sample_packet_schema_path,
        config.repair_instruction_schema_path,
        config.teacher_input_schema_path,
        config.evaluator_input_schema_path,
        config.generator_script,
        config.evaluator_script,
        config.root_dir / config.evaluation_profile_file,
        config.root_dir / config.evaluation_schema_file,
    ]:
        if not path.is_file():
            raise SystemExit(f"Required file not found: {path}")
    if config.max_repair_attempts < 0:
        raise SystemExit("MAX_REPAIR_ATTEMPTS must be a non-negative integer.")
    if config.min_repair_score_delta < 0:
        raise SystemExit("MIN_REPAIR_SCORE_DELTA must be a non-negative float.")
    if config.run_dir.exists():
        raise SystemExit(f"Pipeline run directory already exists: {config.run_dir}")


def discover_scenarios(config: Config, selected_ids: list[str]) -> list[dict[str, Any]]:
    if selected_ids:
        scenarios = []
        for scenario_id in selected_ids:
            path = config.scenario_dir / f"{scenario_id}.json"
            if not path.is_file():
                raise SystemExit(f"Scenario not found: {scenario_id}")
            scenarios.append(load_json(path))
        return scenarios

    scenario_files = sorted(config.scenario_dir.glob("*.json"))
    if not scenario_files:
        raise SystemExit("No scenarios found.")
    return [load_json(path) for path in scenario_files]


def ensure_output_dirs(config: Config) -> None:
    config.packet_dir.mkdir(parents=True, exist_ok=False)
    if config.write_interfaces:
        config.teacher_input_dir.mkdir(parents=True, exist_ok=False)
        config.evaluator_input_dir.mkdir(parents=True, exist_ok=False)
    if config.auto_repair:
        config.repairs_dir.mkdir(parents=True, exist_ok=False)
    config.sample_packets_file.write_text("", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def materialize_interface_input(
    path: Path | None,
    payload: dict[str, Any],
    *,
    enabled: bool,
) -> Path | None:
    if path is None:
        return None
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, payload)
    return path


def sample_key(scenario_id: str, sample_index: int) -> str:
    return f"{scenario_id}__{sample_index:02d}"


def stage_env(base_env: dict[str, str], prefix: str) -> dict[str, str]:
    env = dict(base_env)
    model_id = os.environ.get(f"{prefix}_MODEL_ID", "").strip()
    model_profile = os.environ.get(f"{prefix}_MODEL_PROFILE", "").strip()
    temperature = os.environ.get(f"{prefix}_TEMPERATURE", "").strip()
    if model_id:
        env["MODEL_ID"] = model_id
        env.pop("MODEL_PROFILE", None)
    elif model_profile:
        env["MODEL_PROFILE"] = model_profile
        env.pop("MODEL_ID", None)
    if temperature:
        env["TEMPERATURE"] = temperature
    return env


def run_stage(command: list[str], env: dict[str, str], cwd: Path) -> None:
    subprocess.run(command, cwd=str(cwd), env=env, check=True)


def build_generator_env(
    config: Config,
    *,
    results_dir: Path | None = None,
    samples: int | None = None,
    repair_instruction_path: Path | None = None,
    reuse_teacher_analysis_path: Path | None = None,
) -> dict[str, str]:
    env = stage_env(os.environ, "GENERATOR")
    env["RESULTS_DIR"] = str(results_dir or config.run_dir)
    env["RUN_ID"] = GENERATOR_STAGE_RUN_ID
    env["SAMPLES"] = str(samples if samples is not None else config.samples)
    env["TRAJECTORY_PROFILE"] = config.trajectory_profile
    env["TRAJECTORY_PIPELINE"] = config.trajectory_pipeline
    if repair_instruction_path is not None:
        env["REPAIR_INSTRUCTION_FILE"] = str(repair_instruction_path)
    else:
        env.pop("REPAIR_INSTRUCTION_FILE", None)
    if reuse_teacher_analysis_path is not None:
        env["REUSE_TEACHER_ANALYSIS_FILE"] = str(reuse_teacher_analysis_path)
    else:
        env.pop("REUSE_TEACHER_ANALYSIS_FILE", None)
    return env


def build_evaluator_env(
    config: Config,
    *,
    results_dir: Path | None = None,
) -> dict[str, str]:
    env = stage_env(os.environ, "EVALUATOR")
    env["RESULTS_DIR"] = str(results_dir or config.run_dir)
    env["RUN_ID"] = EVALUATOR_STAGE_RUN_ID
    env["EVALUATION_PROFILE_FILE"] = config.evaluation_profile_file
    env["EVALUATION_SCHEMA_FILE"] = config.evaluation_schema_file
    return env


def build_teacher_input(
    scenario: dict[str, Any],
    generation_manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "teacher_agent_input_v1",
        "scenario_id": scenario["id"],
        "role_name": scenario["name"],
        "profile": scenario["profile"],
        "situation": scenario["situation"],
        "task": scenario["task"],
        "action_options": list(scenario.get("action_options", [])),
        "generation_context": {
            "trajectory_profile": generation_manifest["profile"],
            "pipeline_mode": generation_manifest["pipeline_mode"],
            "teacher_output_schema": generation_manifest["teacher_schema_version"],
            "trajectory_output_schema": generation_manifest["schema_version"],
        },
    }


def build_evaluator_input(
    scenario: dict[str, Any],
    trajectory_row: dict[str, Any],
    evaluation_manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "evaluator_agent_input_v1",
        "evaluation_profile": evaluation_manifest["evaluation_profile"],
        "scenario": scenario_snapshot(scenario),
        "trajectory_meta": {
            "sample_index": trajectory_row["sample_index"],
            "trajectory_profile": trajectory_row.get("profile", ""),
            "pipeline_mode": trajectory_row.get("pipeline_mode", ""),
            "trajectory_schema_version": trajectory_row["schema_version"],
        },
        "trajectory": trajectory_row["trajectory"],
    }


def snapshot_review_state(review_state: dict[str, Any]) -> dict[str, str]:
    return {
        "status": review_state.get("status", ""),
        "next_action": review_state.get("next_action", ""),
        "reason": review_state.get("reason", ""),
    }


def average_axis_scores(axis_scores: dict[str, Any]) -> float | None:
    numeric_scores = [score for score in axis_scores.values() if isinstance(score, int)]
    if not numeric_scores:
        return None
    return sum(numeric_scores) / len(numeric_scores)


def packet_axis_average(packet: dict[str, Any]) -> float | None:
    return average_axis_scores(packet.get("summary", {}).get("axis_scores", {}))


def verdict_rank(verdict: str) -> int:
    return VERDICT_ORDER.get(verdict, 0)


def build_repair_analysis(
    config: Config,
    source_packet: dict[str, Any],
    result_packet: dict[str, Any],
    repair_result: RepairAttemptResult,
) -> dict[str, Any]:
    source_verdict = source_packet["summary"].get("overall_verdict", "")
    result_verdict = result_packet["summary"].get("overall_verdict", "")
    source_primary_failure = source_packet["summary"].get("primary_failure_id") or "none"
    result_primary_failure = result_packet["summary"].get("primary_failure_id") or "none"
    source_axis_average = packet_axis_average(source_packet)
    result_axis_average = packet_axis_average(result_packet)

    axis_average_delta = None
    score_improved = False
    if source_axis_average is None and result_axis_average is not None:
        axis_average_delta = result_axis_average
        score_improved = True
    elif source_axis_average is not None and result_axis_average is not None:
        axis_average_delta = result_axis_average - source_axis_average
        score_improved = axis_average_delta >= config.min_repair_score_delta

    approved_after_repair = result_packet["review_state"]["status"] == "approved"
    primary_failure_changed = source_primary_failure != result_primary_failure
    verdict_improved = verdict_rank(result_verdict) > verdict_rank(source_verdict)
    result_is_repairable = result_packet["review_state"]["next_action"] in EXECUTABLE_REPAIR_ACTIONS

    progress_signals = {
        "approved_after_repair": approved_after_repair,
        "primary_failure_changed": primary_failure_changed,
        "score_improved": score_improved,
        "verdict_improved": verdict_improved,
    }

    return {
        "attempt_index": result_packet["attempt_metadata"]["attempt_index"],
        "sample_id": result_packet["sample_id"],
        "repair_action": repair_result.instruction["next_action"],
        "repair_target": repair_result.instruction["repair_target"],
        "source_overall_verdict": source_verdict,
        "result_overall_verdict": result_verdict,
        "source_primary_failure": source_primary_failure,
        "result_primary_failure": result_primary_failure,
        "source_axis_average": source_axis_average,
        "result_axis_average": result_axis_average,
        "axis_average_delta": axis_average_delta,
        "progress_signals": progress_signals,
        "made_progress": any(progress_signals.values()),
        "result_is_repairable": result_is_repairable,
    }


def evaluate_repair_gate(
    config: Config,
    result_packet: dict[str, Any],
    repair_analysis: dict[str, Any],
) -> dict[str, Any]:
    if repair_analysis["progress_signals"]["approved_after_repair"]:
        return {
            "continue_repair": False,
            "stop_reason": "approved",
        }
    if not repair_analysis["result_is_repairable"]:
        return {
            "continue_repair": False,
            "stop_reason": "non_repairable_route",
        }
    if result_packet["attempt_metadata"]["attempt_index"] >= config.max_repair_attempts:
        return {
            "continue_repair": False,
            "stop_reason": "max_attempts_reached",
        }
    if config.stop_on_no_progress and not repair_analysis["made_progress"]:
        return {
            "continue_repair": False,
            "stop_reason": "no_progress",
        }
    return {
        "continue_repair": True,
        "stop_reason": None,
    }


def build_repair_summary_record(
    config: Config,
    source_packet: dict[str, Any],
    result_packet: dict[str, Any],
    repair_result: RepairAttemptResult,
    repair_analysis: dict[str, Any],
    repair_gate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "pipeline_run_id": config.run_id,
        "sample_id": result_packet["sample_id"],
        "scenario_id": result_packet["scenario_id"],
        "sample_index": result_packet["sample_index"],
        "attempt_index": result_packet["attempt_metadata"]["attempt_index"],
        "repair_action": repair_analysis["repair_action"],
        "repair_target": repair_analysis["repair_target"],
        "reused_teacher_analysis": repair_result.reused_teacher_analysis,
        "source_review_state": snapshot_review_state(source_packet["review_state"]),
        "result_review_state": snapshot_review_state(result_packet["review_state"]),
        "source_overall_verdict": repair_analysis["source_overall_verdict"],
        "result_overall_verdict": repair_analysis["result_overall_verdict"],
        "source_primary_failure": repair_analysis["source_primary_failure"],
        "result_primary_failure": repair_analysis["result_primary_failure"],
        "source_axis_average": repair_analysis["source_axis_average"],
        "result_axis_average": repair_analysis["result_axis_average"],
        "axis_average_delta": repair_analysis["axis_average_delta"],
        "progress_signals": repair_analysis["progress_signals"],
        "made_progress": repair_analysis["made_progress"],
        "continue_repair": repair_gate["continue_repair"],
        "stop_reason": repair_gate["stop_reason"],
        "instruction_ref": rel_to_root(config, repair_result.instruction_path),
        "generation_manifest_ref": rel_to_root(
            config,
            repair_result.provenance_paths.generation_manifest_path,
        ),
        "evaluation_manifest_ref": rel_to_root(
            config,
            repair_result.provenance_paths.evaluation_manifest_path,
        ),
    }


def write_repair_summary(repair_summary_file: Path, repair_report_file: Path) -> str:
    rows = load_jsonl(repair_summary_file)
    lines = [f"records={len(rows)}"]

    action_counts: collections.Counter[str] = collections.Counter()
    stop_reason_counts: collections.Counter[str] = collections.Counter()
    continue_counts: collections.Counter[bool] = collections.Counter()
    progress_signal_counts: collections.Counter[str] = collections.Counter()
    by_scenario: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    axis_average_deltas: list[float] = []

    for row in rows:
        action_counts[row.get("repair_action") or "<empty>"] += 1
        stop_reason_counts[row.get("stop_reason") or "<empty>"] += 1
        continue_counts[row.get("continue_repair", False)] += 1
        by_scenario[row["scenario_id"]].append(row)
        for signal_name, signal_value in (row.get("progress_signals") or {}).items():
            if signal_value is True:
                progress_signal_counts[signal_name] += 1
        axis_average_delta = row.get("axis_average_delta")
        if isinstance(axis_average_delta, (int, float)):
            axis_average_deltas.append(float(axis_average_delta))

    lines.append(f"repair_action={dict(sorted(action_counts.items()))}")
    lines.append(f"stop_reason={dict(sorted(stop_reason_counts.items()))}")
    lines.append(f"continue_repair={dict(sorted(continue_counts.items()))}")
    lines.append(f"progress_signals={dict(sorted(progress_signal_counts.items()))}")
    if axis_average_deltas:
        average_delta = sum(axis_average_deltas) / len(axis_average_deltas)
        lines.append(f"avg_axis_average_delta={average_delta:.2f}")

    for scenario_id in sorted(by_scenario):
        subset = by_scenario[scenario_id]
        scenario_stop_reasons: collections.Counter[str] = collections.Counter()
        for row in subset:
            scenario_stop_reasons[row.get("stop_reason") or "<empty>"] += 1
        lines.append("")
        lines.append(f"[{scenario_id}] repairs={len(subset)}")
        lines.append(f"stop_reason={dict(sorted(scenario_stop_reasons.items()))}")

    summary_text = "\n".join(lines) + "\n"
    repair_report_file.write_text(summary_text, encoding="utf-8")
    return summary_text


def should_auto_repair(config: Config, packet: dict[str, Any]) -> bool:
    if not config.auto_repair:
        return False
    if packet["attempt_metadata"]["attempt_index"] >= config.max_repair_attempts:
        return False
    return packet["review_state"]["next_action"] in EXECUTABLE_REPAIR_ACTIONS


def build_repair_instruction(packet: dict[str, Any]) -> dict[str, Any]:
    evaluation = packet["artifacts"].get("evaluation") or {}
    summary = evaluation.get("summary") or {}
    global_assessment = evaluation.get("global_assessment") or {}
    failure_assessment = packet.get("failure_assessment") or {}
    feedback_decision = packet.get("feedback_decision") or {}
    next_action = packet["review_state"]["next_action"]
    repair_target = "trajectory_prompt" if next_action == "revise_prompt_local" else "teacher_stage"

    repair_focus = []
    for item in global_assessment.get("recommended_focus") or []:
        repair_focus.append(item)
    for failure_tag in failure_assessment.get("failure_tags") or []:
        guidance = failure_tag.get("guidance", "")
        if guidance:
            repair_focus.append(guidance)
    for issue in summary.get("issues") or []:
        repair_focus.append(issue)

    preserve_strengths = list((summary.get("strengths") or [])[:3])
    must_keep = []
    chosen_action_label = packet["summary"].get("chosen_action_label") or None
    if chosen_action_label and repair_target == "trajectory_prompt":
        must_keep.append(f"尽量保留 chosen_action.action_label={chosen_action_label}")
    if packet["pipeline"]["pipeline_mode"] == "teacher_compress" and repair_target == "teacher_stage":
        must_keep.append("保留角色长期承诺、关系责任和世界约束的核心骨架，但允许重推 recommended_packet")

    failure_ids = [
        failure_tag.get("failure_id", "")
        for failure_tag in failure_assessment.get("failure_tags") or []
        if failure_tag.get("failure_id", "")
    ]

    return {
        "schema_version": "repair_instruction_v1",
        "sample_id": packet["sample_id"],
        "scenario_id": packet["scenario_id"],
        "sample_index": packet["sample_index"],
        "source_attempt_index": packet["attempt_metadata"]["attempt_index"],
        "next_action": next_action,
        "repair_target": repair_target,
        "trigger": {
            "overall_verdict": packet["summary"].get("overall_verdict", ""),
            "rewrite_priority": packet["summary"].get("rewrite_priority", ""),
            "feedback_rule_id": feedback_decision.get("rule_id", ""),
            "primary_failure_id": failure_assessment.get("primary_failure_id", ""),
            "highest_failure_severity": failure_assessment.get("highest_severity", ""),
            "failure_ids": failure_ids,
        },
        "repair_focus": repair_focus,
        "preserve": {
            "chosen_action_label": chosen_action_label,
            "strengths": preserve_strengths,
            "must_keep": must_keep,
        },
        "rationale": feedback_decision.get("rationale", ""),
    }


def load_generation_stage_outputs(
    root_dir: Path,
    base_run_dir: Path,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    generation_manifest_path = base_run_dir / GENERATOR_STAGE_RUN_ID / "manifest.json"
    generation_manifest = load_json(generation_manifest_path)
    trajectories_file = root_dir / generation_manifest["output_files"]["trajectories"]
    trajectory_rows = load_jsonl(trajectories_file)
    teacher_index: dict[tuple[str, int], dict[str, Any]] = {}
    if generation_manifest["pipeline_mode"] == "teacher_compress":
        teacher_file = root_dir / generation_manifest["output_files"]["teacher_analyses"]
        teacher_rows = load_jsonl(teacher_file)
        teacher_index = {
            (row["scenario_id"], row["sample_index"]): row for row in teacher_rows
        }
    return generation_manifest_path, generation_manifest, trajectory_rows, teacher_index


def load_evaluation_stage_outputs(
    root_dir: Path,
    base_run_dir: Path,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    evaluation_manifest_path = base_run_dir / EVALUATOR_STAGE_RUN_ID / "manifest.json"
    evaluation_manifest = load_json(evaluation_manifest_path)
    evaluations_file = root_dir / evaluation_manifest["output_files"]["evaluations"]
    evaluation_rows = load_jsonl(evaluations_file)
    evaluation_index = {
        (row["scenario_id"], row["sample_index"]): row for row in evaluation_rows
    }
    return evaluation_manifest_path, evaluation_manifest, evaluation_rows, evaluation_index


def execute_repair_attempt(
    config: Config,
    scenario: dict[str, Any],
    source_packet: dict[str, Any],
    attempt_index: int,
) -> RepairAttemptResult:
    row_key = sample_key(source_packet["scenario_id"], source_packet["sample_index"])
    attempt_dir = config.repairs_dir / row_key / f"attempt_{attempt_index:02d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)

    repair_instruction = build_repair_instruction(source_packet)
    instruction_path = attempt_dir / "repair_instruction.json"
    write_json(instruction_path, repair_instruction)

    reuse_teacher_analysis_path = None
    if (
        repair_instruction["next_action"] == "revise_prompt_local"
        and source_packet["pipeline"]["pipeline_mode"] == "teacher_compress"
        and source_packet["artifacts"].get("teacher_analysis") is not None
    ):
        reuse_teacher_analysis_path = attempt_dir / "source_teacher_analysis.json"
        write_json(
            reuse_teacher_analysis_path,
            {
                "scenario_id": source_packet["scenario_id"],
                "sample_index": 1,
                "profile": source_packet["pipeline"]["trajectory_profile"],
                "teacher_analysis": source_packet["artifacts"]["teacher_analysis"],
                "recommended_action_label": source_packet["summary"].get(
                    "teacher_recommended_action_label",
                    "",
                ),
            },
        )

    generator_command = [sys.executable, str(config.generator_script), scenario["id"]]
    generator_env = build_generator_env(
        config,
        results_dir=attempt_dir,
        samples=1,
        repair_instruction_path=instruction_path,
        reuse_teacher_analysis_path=reuse_teacher_analysis_path,
    )
    print(
        f"repair_attempt={attempt_index:02d} sample={source_packet['sample_id']} next_action={repair_instruction['next_action']}"
    )
    run_stage(generator_command, generator_env, config.root_dir)

    generation_manifest_path, generation_manifest, trajectory_rows, teacher_index = load_generation_stage_outputs(
        config.root_dir,
        attempt_dir,
    )
    if len(trajectory_rows) != 1:
        raise RuntimeError(f"Expected exactly one repaired trajectory row, got {len(trajectory_rows)}")
    trajectory_row = trajectory_rows[0]
    teacher_row = teacher_index.get((trajectory_row["scenario_id"], trajectory_row["sample_index"]))

    evaluator_command = [
        sys.executable,
        str(config.evaluator_script),
        str(config.root_dir / generation_manifest["output_files"]["trajectories"]),
    ]
    evaluator_env = build_evaluator_env(
        config,
        results_dir=attempt_dir,
    )
    run_stage(evaluator_command, evaluator_env, config.root_dir)

    evaluation_manifest_path, evaluation_manifest, _, evaluation_index = load_evaluation_stage_outputs(
        config.root_dir,
        attempt_dir,
    )
    evaluation_row = evaluation_index.get((trajectory_row["scenario_id"], trajectory_row["sample_index"]))

    teacher_input_path = None
    if generation_manifest["pipeline_mode"] == "teacher_compress":
        teacher_input_path = materialize_interface_input(
            attempt_dir / "interfaces" / "teacher_input.json",
            build_teacher_input(scenario, generation_manifest),
            enabled=config.write_interfaces,
        )

    evaluator_input_path = None
    if trajectory_row.get("parse_status") == "ok" and trajectory_row.get("trajectory"):
        evaluator_input_path = materialize_interface_input(
            attempt_dir / "interfaces" / "evaluator_input.json",
            build_evaluator_input(scenario, trajectory_row, evaluation_manifest),
            enabled=config.write_interfaces,
        )

    return RepairAttemptResult(
        attempt_dir=attempt_dir,
        instruction=repair_instruction,
        instruction_path=instruction_path,
        reused_teacher_analysis=reuse_teacher_analysis_path is not None,
        generation_manifest=generation_manifest,
        trajectory_row=trajectory_row,
        teacher_row=teacher_row,
        evaluation_manifest=evaluation_manifest,
        evaluation_row=evaluation_row,
        provenance_paths=resolve_provenance_paths(
            config,
            generation_manifest_path=generation_manifest_path,
            generation_manifest=generation_manifest,
            evaluation_manifest_path=evaluation_manifest_path,
            evaluation_manifest=evaluation_manifest,
            row_key=row_key,
            teacher_input_path=teacher_input_path,
            evaluator_input_path=evaluator_input_path,
        ),
    )


def build_repair_history_entry(
    config: Config,
    source_packet: dict[str, Any],
    result_packet: dict[str, Any],
    repair_result: RepairAttemptResult,
) -> dict[str, Any]:
    return {
        "attempt_index": result_packet["attempt_metadata"]["attempt_index"],
        "next_action": repair_result.instruction["next_action"],
        "repair_target": repair_result.instruction["repair_target"],
        "instruction_ref": rel_to_root(config, repair_result.instruction_path),
        "reused_teacher_analysis": repair_result.reused_teacher_analysis,
        "source_review_state": snapshot_review_state(source_packet["review_state"]),
        "source_primary_failure": source_packet["summary"].get("primary_failure_id"),
        "result_review_state": snapshot_review_state(result_packet["review_state"]),
        "result_primary_failure": result_packet["summary"].get("primary_failure_id"),
        "generation_manifest_ref": rel_to_root(
            config,
            repair_result.provenance_paths.generation_manifest_path,
        ),
        "evaluation_manifest_ref": rel_to_root(
            config,
            repair_result.provenance_paths.evaluation_manifest_path,
        ),
    }


def resolve_daily_model_profile(
    generation_manifest: dict[str, Any],
    evaluation_manifest: dict[str, Any],
) -> str | None:
    requested_profile = os.environ.get("MODEL_PROFILE", "").strip()
    if requested_profile:
        return requested_profile

    generation_profile = generation_manifest.get("model_profile", "")
    evaluation_profile = evaluation_manifest.get("model_profile", "")
    if generation_profile and generation_profile == evaluation_profile:
        return generation_profile
    return None


def write_pipeline_manifest(
    config: Config,
    scenario_ids: list[str],
    generation_manifest: dict[str, Any],
    evaluation_manifest: dict[str, Any],
    packet_count: int,
    auto_repaired_packet_count: int,
    repair_attempt_count: int,
) -> None:
    has_repair_summary = repair_attempt_count > 0
    manifest = {
        "manifest_version": PIPELINE_MANIFEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": {
            "run_id": config.run_id,
            "scenario_ids": scenario_ids,
            "scenario_count": len(scenario_ids),
            "packet_count": packet_count,
            "sample_packet_schema": rel_to_root(config, config.sample_packet_schema_path),
        },
        "daily_knobs": {
            "model_profile": resolve_daily_model_profile(generation_manifest, evaluation_manifest),
            "samples": config.samples,
            "auto_repair": config.auto_repair,
            "max_repair_attempts": config.max_repair_attempts,
        },
        "stage_manifests": {
            "generate": rel_to_root(config, config.run_dir / GENERATOR_STAGE_RUN_ID / "manifest.json"),
            "evaluate": rel_to_root(config, config.run_dir / EVALUATOR_STAGE_RUN_ID / "manifest.json"),
        },
        "daily_artifacts": {
            "pipeline_summary": rel_to_root(config, config.summary_file),
            "sample_packets_jsonl": rel_to_root(config, config.sample_packets_file),
            "sample_packet_dir": rel_to_root(config, config.packet_dir),
            "repair_summary": rel_to_root(config, config.repair_report_file)
            if has_repair_summary
            else None,
            "repair_summary_jsonl": rel_to_root(config, config.repair_summary_file)
            if has_repair_summary
            else None,
        },
        "repair_overview": {
            "enabled": config.auto_repair,
            "repair_attempt_count": repair_attempt_count,
            "auto_repaired_packet_count": auto_repaired_packet_count,
        },
    }
    write_json(config.manifest_file, manifest)

def write_summary(sample_packets_file: Path, summary_file: Path) -> str:
    rows = load_jsonl(sample_packets_file)
    lines = [f"records={len(rows)}"]

    attempt_kind_counts: collections.Counter[str] = collections.Counter()
    status_counts: collections.Counter[str] = collections.Counter()
    next_action_counts: collections.Counter[str] = collections.Counter()
    verdict_counts: collections.Counter[str] = collections.Counter()
    primary_failure_counts: collections.Counter[str] = collections.Counter()
    failure_tag_counts: collections.Counter[str] = collections.Counter()
    axis_scores: dict[str, list[int]] = collections.defaultdict(list)
    by_scenario: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)

    for row in rows:
        attempt_kind_counts[row["attempt_metadata"]["attempt_kind"]] += 1
        status_counts[row["review_state"]["status"]] += 1
        next_action_counts[row["review_state"]["next_action"]] += 1
        verdict_counts[row["summary"].get("overall_verdict") or "<empty>"] += 1
        primary_failure_counts[row["summary"].get("primary_failure_id") or "<empty>"] += 1
        for failure_id in row["summary"].get("failure_ids", []):
            failure_tag_counts[failure_id] += 1
        by_scenario[row["scenario_id"]].append(row)
        for axis_id, score in row["summary"].get("axis_scores", {}).items():
            if isinstance(score, int):
                axis_scores[axis_id].append(score)

    auto_repaired = sum(1 for row in rows if row["attempt_metadata"].get("auto_repaired"))
    repair_attempts = sum(len(row.get("repair_history", [])) for row in rows)

    lines.append(f"attempt_kind={dict(sorted(attempt_kind_counts.items()))}")
    lines.append(f"auto_repaired={auto_repaired}")
    lines.append(f"repair_attempts={repair_attempts}")
    lines.append(f"review_state={dict(sorted(status_counts.items()))}")
    lines.append(f"next_action={dict(sorted(next_action_counts.items()))}")
    lines.append(f"overall_verdict={dict(sorted(verdict_counts.items()))}")
    lines.append(f"primary_failure={dict(sorted(primary_failure_counts.items()))}")
    lines.append(f"failure_tags={dict(sorted(failure_tag_counts.items()))}")
    lines.append("")
    lines.append("axis_average_scores:")
    for axis_id in sorted(axis_scores):
        average = sum(axis_scores[axis_id]) / len(axis_scores[axis_id])
        lines.append(f"- {axis_id}: {average:.1f}")

    for scenario_id in sorted(by_scenario):
        subset = by_scenario[scenario_id]
        scenario_status_counts: collections.Counter[str] = collections.Counter()
        scenario_verdict_counts: collections.Counter[str] = collections.Counter()
        for row in subset:
            scenario_status_counts[row["review_state"]["status"]] += 1
            scenario_verdict_counts[row["summary"].get("overall_verdict") or "<empty>"] += 1
        lines.append("")
        lines.append(f"[{scenario_id}] records={len(subset)}")
        lines.append(f"review_state={dict(sorted(scenario_status_counts.items()))}")
        lines.append(f"overall_verdict={dict(sorted(scenario_verdict_counts.items()))}")

    summary_text = "\n".join(lines) + "\n"
    summary_file.write_text(summary_text, encoding="utf-8")
    return summary_text


def main() -> None:
    args = parse_args()
    config = load_config()
    validate_config(config)
    scenarios = discover_scenarios(config, args.scenarios)
    ensure_output_dirs(config)
    scenario_ids = [scenario["id"] for scenario in scenarios]
    scenarios_by_id = {scenario["id"]: scenario for scenario in scenarios}

    generator_command = [sys.executable, str(config.generator_script), *scenario_ids]
    generator_env = build_generator_env(config)
    print(f"pipeline_run_id={config.run_id}")
    print(f"pipeline_dir={config.run_dir}")
    print(f"generator_stage={GENERATOR_STAGE_RUN_ID} scenarios={scenario_ids} samples={config.samples}")
    run_stage(generator_command, generator_env, config.root_dir)

    generation_manifest_path, generation_manifest, trajectory_rows, teacher_index = load_generation_stage_outputs(
        config.root_dir,
        config.run_dir,
    )
    trajectories_file = config.root_dir / generation_manifest["output_files"]["trajectories"]

    evaluator_command = [sys.executable, str(config.evaluator_script), str(trajectories_file)]
    evaluator_env = build_evaluator_env(config)
    print(f"evaluator_stage={EVALUATOR_STAGE_RUN_ID} trajectory_file={trajectories_file}")
    run_stage(evaluator_command, evaluator_env, config.root_dir)

    evaluation_manifest_path, evaluation_manifest, _, evaluation_index = load_evaluation_stage_outputs(
        config.root_dir,
        config.run_dir,
    )
    failure_defaults: dict[str, dict[str, Any]] = {}
    feedback_protocol_id = evaluation_manifest.get("feedback_protocol_profile") or None
    failure_taxonomy_id = evaluation_manifest.get("failure_taxonomy_profile") or None
    failure_taxonomy_path = evaluation_manifest.get("failure_taxonomy_profile_path", "")
    if failure_taxonomy_path:
        failure_taxonomy_profile = load_json(config.root_dir / failure_taxonomy_path)
        failure_defaults = {
            item["failure_id"]: item for item in failure_taxonomy_profile.get("failures", [])
        }

    repair_attempt_count = 0
    auto_repaired_packet_count = 0
    repair_summary_handle = None

    try:
        with config.sample_packets_file.open("a", encoding="utf-8") as handle:
            for trajectory_row in trajectory_rows:
                key = (trajectory_row["scenario_id"], trajectory_row["sample_index"])
                scenario = scenarios_by_id[trajectory_row["scenario_id"]]
                row_key = sample_key(trajectory_row["scenario_id"], trajectory_row["sample_index"])
                teacher_row = teacher_index.get(key)
                evaluation_row = evaluation_index.get(key)

                teacher_input_path = None
                if generation_manifest["pipeline_mode"] == "teacher_compress":
                    teacher_input_path = materialize_interface_input(
                        config.teacher_input_dir / f"{row_key}.json",
                        build_teacher_input(scenario, generation_manifest),
                        enabled=config.write_interfaces,
                    )

                evaluator_input_path = None
                if trajectory_row.get("parse_status") == "ok" and trajectory_row.get("trajectory"):
                    evaluator_input_path = materialize_interface_input(
                        config.evaluator_input_dir / f"{row_key}.json",
                        build_evaluator_input(scenario, trajectory_row, evaluation_manifest),
                        enabled=config.write_interfaces,
                    )

                initial_provenance_paths = resolve_provenance_paths(
                    config,
                    generation_manifest_path=generation_manifest_path,
                    generation_manifest=generation_manifest,
                    evaluation_manifest_path=evaluation_manifest_path,
                    evaluation_manifest=evaluation_manifest,
                    row_key=row_key,
                    teacher_input_path=teacher_input_path,
                    evaluator_input_path=evaluator_input_path,
                )

                packet = build_sample_packet(
                    config=config,
                    scenario=scenario,
                    trajectory_row=trajectory_row,
                    teacher_row=teacher_row,
                    evaluation_row=evaluation_row,
                    generation_manifest=generation_manifest,
                    evaluation_manifest=evaluation_manifest,
                    provenance_paths=initial_provenance_paths,
                    protocol_id=feedback_protocol_id,
                    taxonomy_profile_id=failure_taxonomy_id,
                    failure_defaults=failure_defaults,
                    attempt_context=AttemptContext(),
                )

                current_packet = packet
                repair_history: list[dict[str, Any]] = []
                while should_auto_repair(config, current_packet):
                    next_attempt_index = current_packet["attempt_metadata"]["attempt_index"] + 1
                    repair_result = execute_repair_attempt(
                        config,
                        scenario,
                        current_packet,
                        next_attempt_index,
                    )
                    repair_attempt_count += 1

                    repaired_packet = build_sample_packet(
                        config=config,
                        scenario=scenario,
                        trajectory_row=repair_result.trajectory_row,
                        teacher_row=repair_result.teacher_row,
                        evaluation_row=repair_result.evaluation_row,
                        generation_manifest=repair_result.generation_manifest,
                        evaluation_manifest=repair_result.evaluation_manifest,
                        provenance_paths=repair_result.provenance_paths,
                        protocol_id=feedback_protocol_id,
                        taxonomy_profile_id=failure_taxonomy_id,
                        failure_defaults=failure_defaults,
                        attempt_context=AttemptContext(
                            attempt_index=next_attempt_index,
                            attempt_kind="repair",
                            auto_repaired=True,
                            repair_origin_sample_id=packet["sample_id"],
                        ),
                        repair_history=repair_history,
                    )

                    repair_analysis = build_repair_analysis(
                        config,
                        current_packet,
                        repaired_packet,
                        repair_result,
                    )
                    repair_gate = evaluate_repair_gate(
                        config,
                        repaired_packet,
                        repair_analysis,
                    )

                    repair_history.append(
                        build_repair_history_entry(
                            config,
                            current_packet,
                            repaired_packet,
                            repair_result,
                        )
                    )
                    repaired_packet["repair_history"] = copy.deepcopy(repair_history)
                    validate_packet(repaired_packet)

                    if repair_summary_handle is None:
                        repair_summary_handle = config.repair_summary_file.open(
                            "a",
                            encoding="utf-8",
                        )

                    repair_summary_record = build_repair_summary_record(
                        config,
                        current_packet,
                        repaired_packet,
                        repair_result,
                        repair_analysis,
                        repair_gate,
                    )
                    repair_summary_handle.write(
                        json.dumps(repair_summary_record, ensure_ascii=False) + "\n"
                    )
                    repair_summary_handle.flush()

                    current_packet = repaired_packet
                    if not repair_gate["continue_repair"]:
                        break

                if current_packet["attempt_metadata"]["attempt_index"] > 0:
                    auto_repaired_packet_count += 1

                packet_path = config.packet_dir / f"{row_key}.json"
                write_json(packet_path, current_packet)
                handle.write(json.dumps(current_packet, ensure_ascii=False) + "\n")
                handle.flush()
    finally:
        if repair_summary_handle is not None:
            repair_summary_handle.close()

    write_pipeline_manifest(
        config,
        scenario_ids,
        generation_manifest,
        evaluation_manifest,
        len(trajectory_rows),
        auto_repaired_packet_count,
        repair_attempt_count,
    )
    summary_text = write_summary(config.sample_packets_file, config.summary_file)
    print(summary_text, end="")
    if repair_attempt_count > 0:
        repair_summary_text = write_repair_summary(
            config.repair_summary_file,
            config.repair_report_file,
        )
        print(repair_summary_text, end="")
    print(f"sample_packets={config.sample_packets_file}")
    print(f"pipeline_manifest={config.manifest_file}")


if __name__ == "__main__":
    main()