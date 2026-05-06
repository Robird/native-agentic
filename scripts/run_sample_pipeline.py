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
DEFAULT_SAMPLES = 3
DEFAULT_MAX_REPAIR_ATTEMPTS = 1


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
    summary_file: Path
    manifest_file: Path
    sample_packet_schema_path: Path
    repair_instruction_schema_path: Path
    teacher_input_schema_path: Path
    evaluator_input_schema_path: Path
    generator_script: Path
    evaluator_script: Path
    generator_run_id: str
    evaluator_run_id: str
    samples: int
    auto_repair: bool
    max_repair_attempts: int
    trajectory_profile: str
    trajectory_pipeline: str
    evaluation_profile_file: str
    evaluation_schema_file: str


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
        generator_run_id="generate",
        evaluator_run_id="evaluate",
        samples=int(os.environ.get("SAMPLES", str(DEFAULT_SAMPLES))),
        auto_repair=env_flag("AUTO_REPAIR", False),
        max_repair_attempts=int(
            os.environ.get("MAX_REPAIR_ATTEMPTS", str(DEFAULT_MAX_REPAIR_ATTEMPTS))
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


def rel_to_root(config: Config, path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return str(path.relative_to(config.root_dir))


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
    run_id: str | None = None,
    samples: int | None = None,
    repair_instruction_path: Path | None = None,
    reuse_teacher_analysis_path: Path | None = None,
) -> dict[str, str]:
    env = stage_env(os.environ, "GENERATOR")
    env["RESULTS_DIR"] = str(results_dir or config.run_dir)
    env["RUN_ID"] = run_id or config.generator_run_id
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
    run_id: str | None = None,
) -> dict[str, str]:
    env = stage_env(os.environ, "EVALUATOR")
    env["RESULTS_DIR"] = str(results_dir or config.run_dir)
    env["RUN_ID"] = run_id or config.evaluator_run_id
    env["EVALUATION_PROFILE_FILE"] = config.evaluation_profile_file
    env["EVALUATION_SCHEMA_FILE"] = config.evaluation_schema_file
    return env


def scenario_snapshot(scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "profile": scenario["profile"],
        "situation": scenario["situation"],
        "task": scenario["task"],
        "action_options": list(scenario.get("action_options", [])),
    }


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


def should_auto_repair(config: Config, packet: dict[str, Any]) -> bool:
    if not config.auto_repair:
        return False
    if packet["attempt_metadata"]["attempt_index"] >= config.max_repair_attempts:
        return False
    return packet["review_state"]["next_action"] in {
        "revise_prompt_local",
        "regenerate_from_teacher",
    }


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
    generation_manifest_path = base_run_dir / "generate" / "manifest.json"
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
    evaluation_manifest_path = base_run_dir / "evaluate" / "manifest.json"
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
) -> dict[str, Any]:
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
        run_id=config.generator_run_id,
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
        run_id=config.evaluator_run_id,
    )
    run_stage(evaluator_command, evaluator_env, config.root_dir)

    evaluation_manifest_path, evaluation_manifest, _, evaluation_index = load_evaluation_stage_outputs(
        config.root_dir,
        attempt_dir,
    )
    evaluation_row = evaluation_index.get((trajectory_row["scenario_id"], trajectory_row["sample_index"]))

    interface_dir = attempt_dir / "interfaces"
    interface_dir.mkdir(parents=True, exist_ok=True)
    teacher_input_path = None
    if generation_manifest["pipeline_mode"] == "teacher_compress":
        teacher_input_path = interface_dir / "teacher_input.json"
        write_json(teacher_input_path, build_teacher_input(scenario, generation_manifest))

    evaluator_input_path = None
    if trajectory_row.get("parse_status") == "ok" and trajectory_row.get("trajectory"):
        evaluator_input_path = interface_dir / "evaluator_input.json"
        write_json(
            evaluator_input_path,
            build_evaluator_input(scenario, trajectory_row, evaluation_manifest),
        )

    return {
        "attempt_dir": attempt_dir,
        "instruction": repair_instruction,
        "instruction_path": instruction_path,
        "reused_teacher_analysis": reuse_teacher_analysis_path is not None,
        "generation_manifest_path": generation_manifest_path,
        "generation_manifest": generation_manifest,
        "trajectory_row": trajectory_row,
        "teacher_row": teacher_row,
        "evaluation_manifest_path": evaluation_manifest_path,
        "evaluation_manifest": evaluation_manifest,
        "evaluation_row": evaluation_row,
        "teacher_input_path": teacher_input_path,
        "evaluator_input_path": evaluator_input_path,
    }


def build_repair_history_entry(
    config: Config,
    source_packet: dict[str, Any],
    result_packet: dict[str, Any],
    repair_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "attempt_index": result_packet["attempt_metadata"]["attempt_index"],
        "next_action": repair_result["instruction"]["next_action"],
        "repair_target": repair_result["instruction"]["repair_target"],
        "instruction_ref": rel_to_root(config, repair_result["instruction_path"]),
        "reused_teacher_analysis": repair_result["reused_teacher_analysis"],
        "source_review_state": snapshot_review_state(source_packet["review_state"]),
        "source_primary_failure": source_packet["summary"].get("primary_failure_id"),
        "result_review_state": snapshot_review_state(result_packet["review_state"]),
        "result_primary_failure": result_packet["summary"].get("primary_failure_id"),
        "generation_manifest_ref": rel_to_root(config, repair_result["generation_manifest_path"]),
        "evaluation_manifest_ref": rel_to_root(config, repair_result["evaluation_manifest_path"]),
    }


def empty_failure_assessment(taxonomy_profile_id: str | None) -> dict[str, Any]:
    return {
        "taxonomy_profile": taxonomy_profile_id,
        "primary_failure_id": "none",
        "highest_severity": "none",
        "failure_tags": [],
    }


def build_failure_tag(
    failure_defaults: dict[str, dict[str, Any]],
    failure_id: str,
    evidence: list[str],
    guidance: str,
    *,
    severity: str | None = None,
    confidence: int = 100,
) -> dict[str, Any]:
    failure = failure_defaults.get(failure_id, {})
    return {
        "failure_id": failure_id,
        "label": failure.get("label", failure_id),
        "category": failure.get("category", "unknown"),
        "severity": severity or failure.get("default_severity", "high"),
        "confidence": confidence,
        "repair_stage": failure.get("preferred_repair_stage", "manual"),
        "evidence": evidence,
        "guidance": guidance or failure.get("description", ""),
    }


def fallback_failure_assessment(
    taxonomy_profile_id: str | None,
    failure_defaults: dict[str, dict[str, Any]],
    failure_id: str,
    evidence: list[str],
    guidance: str,
) -> dict[str, Any]:
    tag = build_failure_tag(failure_defaults, failure_id, evidence, guidance)
    return {
        "taxonomy_profile": taxonomy_profile_id,
        "primary_failure_id": failure_id,
        "highest_severity": tag["severity"],
        "failure_tags": [tag],
    }


def fallback_feedback_decision(
    protocol_id: str | None,
    rule_id: str,
    suggested_status: str,
    suggested_next_action: str,
    preferred_repair_stage: str,
    urgency: str,
    blocking: bool,
    rationale: str,
) -> dict[str, Any]:
    return {
        "protocol_id": protocol_id,
        "rule_id": rule_id,
        "suggested_status": suggested_status,
        "suggested_next_action": suggested_next_action,
        "preferred_repair_stage": preferred_repair_stage,
        "urgency": urgency,
        "blocking": blocking,
        "rationale": rationale,
    }


def resolve_feedback_and_failures(
    trajectory_row: dict[str, Any],
    evaluation_row: dict[str, Any] | None,
    teacher_used: bool,
    protocol_id: str | None,
    taxonomy_profile_id: str | None,
    failure_defaults: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if trajectory_row.get("parse_status") != "ok":
        parse_status = trajectory_row.get("parse_status", "")
        if teacher_used and trajectory_row.get("teacher_parse_status") == "ok":
            failure_assessment = fallback_failure_assessment(
                taxonomy_profile_id,
                failure_defaults,
                "schema_structure_instability",
                [f"trajectory parse_status={parse_status}", "teacher_parse_status=ok"],
                "Teacher stage succeeded but the final trajectory output was not structurally reliable; rerun from teacher or repair compressor prompting.",
            )
            feedback_decision = fallback_feedback_decision(
                protocol_id,
                "fallback_regenerate_after_parse_failure",
                "needs_revision",
                "regenerate_from_teacher",
                "teacher_stage",
                "high",
                True,
                "Final trajectory parse failed after a successful teacher stage; regenerate from teacher rather than approving or locally patching the packet.",
            )
            return failure_assessment, feedback_decision

        failure_assessment = fallback_failure_assessment(
            taxonomy_profile_id,
            failure_defaults,
            "schema_structure_instability",
            [f"trajectory parse_status={parse_status}"],
            "Generation output is structurally unstable and cannot be routed automatically with confidence.",
        )
        feedback_decision = fallback_feedback_decision(
            protocol_id,
            "fallback_rerun_generation",
            "manual_review",
            "rerun_generation",
            "generation_rerun",
            "high",
            True,
            "Generation output was structurally unusable before evaluation; rerun generation or inspect the transport/prompt failure.",
        )
        return failure_assessment, feedback_decision

    if evaluation_row is None or evaluation_row.get("parse_status") != "ok":
        parse_status = evaluation_row.get("parse_status", "missing_evaluation") if evaluation_row else "missing_evaluation"
        failure_assessment = fallback_failure_assessment(
            taxonomy_profile_id,
            failure_defaults,
            "schema_structure_instability",
            [f"evaluation parse_status={parse_status}"],
            "Evaluation output was not trustworthy enough for automatic routing; inspect the evaluator contract or rerun evaluation.",
        )
        feedback_decision = fallback_feedback_decision(
            protocol_id,
            "fallback_manual_review_evaluation_failure",
            "manual_review",
            "manual_review",
            "manual",
            "high",
            True,
            "Evaluator output could not be trusted automatically, so this sample must be reviewed manually.",
        )
        return failure_assessment, feedback_decision

    failure_assessment = evaluation_row.get("failure_assessment") or empty_failure_assessment(
        taxonomy_profile_id,
    )
    feedback_decision = evaluation_row.get("feedback_decision")
    if feedback_decision:
        return failure_assessment, feedback_decision

    verdict = evaluation_row.get("overall_verdict", "")
    if verdict == "keep":
        feedback_decision = fallback_feedback_decision(
            protocol_id,
            "fallback_approve_keep",
            "approved",
            "approve",
            "none",
            "low",
            False,
            "Keep verdict without an explicit feedback decision falls back to direct approval.",
        )
        return failure_assessment, feedback_decision

    if verdict == "reject":
        feedback_decision = fallback_feedback_decision(
            protocol_id,
            "fallback_reject_verdict",
            "rejected",
            "reject",
            "dataset_policy",
            "high",
            True,
            "Reject verdict without explicit feedback decision falls back to dataset rejection.",
        )
        return failure_assessment, feedback_decision

    if verdict == "manual_review":
        feedback_decision = fallback_feedback_decision(
            protocol_id,
            "fallback_manual_review_verdict",
            "manual_review",
            "manual_review",
            "manual",
            "high",
            True,
            "Manual-review verdict without explicit feedback decision falls back to manual review.",
        )
        return failure_assessment, feedback_decision

    next_action = "regenerate_from_teacher" if teacher_used else "revise_prompt_local"
    preferred_repair_stage = "teacher_stage" if teacher_used else "trajectory_prompt"
    primary_failure_id = failure_assessment.get("primary_failure_id")
    if primary_failure_id and primary_failure_id in failure_defaults:
        preferred_repair_stage = failure_defaults[primary_failure_id].get(
            "preferred_repair_stage",
            preferred_repair_stage,
        )
        default_next_action = failure_defaults[primary_failure_id].get("default_next_action")
        if default_next_action:
            next_action = default_next_action
        if next_action == "regenerate_from_teacher" and not teacher_used:
            next_action = "revise_prompt_local"

    feedback_decision = fallback_feedback_decision(
        protocol_id,
        "fallback_revise_verdict",
        "needs_revision",
        next_action,
        preferred_repair_stage,
        "medium",
        True,
        "Revise verdict without explicit feedback decision falls back to the dominant repair stage implied by the failure taxonomy.",
    )
    return failure_assessment, feedback_decision


def review_state(feedback_decision: dict[str, Any], failure_assessment: dict[str, Any]) -> dict[str, str]:
    reason = feedback_decision.get("rule_id") or "feedback_decision"
    primary_failure_id = failure_assessment.get("primary_failure_id")
    if primary_failure_id and primary_failure_id != "none":
        reason = f"{reason}:{primary_failure_id}"
    return {
        "status": feedback_decision.get("suggested_status", "manual_review"),
        "next_action": feedback_decision.get("suggested_next_action", "manual_review"),
        "reason": reason,
    }


def validate_packet(packet: dict[str, Any]) -> None:
    if packet.get("schema_version") != "sample_packet_v1":
        raise ValueError("sample packet schema_version mismatch")
    if packet["scenario_id"] != packet["scenario"]["id"]:
        raise ValueError("scenario_id does not match scenario snapshot")
    if packet["contracts"]["teacher_agent"]["status"] == "completed" and packet["artifacts"]["teacher_analysis"] is None:
        raise ValueError("teacher contract marked completed without teacher artifact")
    if packet["artifacts"]["trajectory"] is None and packet["summary"]["generation_parse_status"] == "ok":
        raise ValueError("trajectory parse status ok but trajectory artifact missing")
    if packet["contracts"]["evaluator_agent"]["status"] == "completed" and packet["artifacts"]["evaluation"] is None:
        raise ValueError("evaluator contract marked completed without evaluation artifact")
    if packet["review_state"]["status"] == "approved" and packet["summary"]["overall_verdict"] != "keep":
        raise ValueError("approved packet must have keep verdict")
    if packet["feedback_decision"]["suggested_status"] != packet["review_state"]["status"]:
        raise ValueError("review_state status must match feedback_decision")
    if packet["feedback_decision"]["suggested_next_action"] != packet["review_state"]["next_action"]:
        raise ValueError("review_state next_action must match feedback_decision")
    attempt_metadata = packet.get("attempt_metadata") or {}
    attempt_index = attempt_metadata.get("attempt_index")
    attempt_kind = attempt_metadata.get("attempt_kind")
    if attempt_kind == "initial" and attempt_index != 0:
        raise ValueError("initial packet must have attempt_index=0")
    if attempt_kind == "repair" and (not isinstance(attempt_index, int) or attempt_index < 1):
        raise ValueError("repair packet must have attempt_index>=1")
    repair_history = packet.get("repair_history") or []
    if attempt_index == 0 and repair_history:
        raise ValueError("initial packet cannot contain repair history")
    if isinstance(attempt_index, int) and len(repair_history) > attempt_index:
        raise ValueError("repair history cannot exceed current attempt index")


def maybe_path(base_dir: Path | None, key: str) -> Path | None:
    if base_dir is None:
        return None
    path = base_dir / f"{key}.json"
    if path.exists():
        return path
    return None


def build_sample_packet(
    config: Config,
    scenario: dict[str, Any],
    trajectory_row: dict[str, Any],
    teacher_row: dict[str, Any] | None,
    evaluation_row: dict[str, Any] | None,
    generation_manifest: dict[str, Any],
    evaluation_manifest: dict[str, Any],
    generation_manifest_path: Path,
    evaluation_manifest_path: Path,
    teacher_input_path: Path | None,
    evaluator_input_path: Path | None,
    teacher_request_path: Path | None,
    teacher_raw_path: Path | None,
    trajectory_request_path: Path | None,
    trajectory_raw_path: Path | None,
    evaluation_request_path: Path | None,
    evaluation_raw_path: Path | None,
    protocol_id: str | None,
    taxonomy_profile_id: str | None,
    failure_defaults: dict[str, dict[str, Any]],
    attempt_index: int = 0,
    attempt_kind: str = "initial",
    auto_repaired: bool = False,
    repair_origin_sample_id: str | None = None,
    repair_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    teacher_used = generation_manifest["pipeline_mode"] == "teacher_compress"
    teacher_status = "not_used"
    if teacher_used:
        if teacher_row is None:
            teacher_status = "missing"
        elif teacher_row.get("parse_status") == "ok":
            teacher_status = "completed"
        else:
            teacher_status = "parse_failed"

    evaluator_status = "missing"
    if evaluation_row is not None:
        parse_status = evaluation_row.get("parse_status", "")
        if parse_status == "ok":
            evaluator_status = "completed"
        elif parse_status == "skipped_no_valid_trajectory":
            evaluator_status = "skipped"
        else:
            evaluator_status = "parse_failed"

    failure_assessment, feedback_decision = resolve_feedback_and_failures(
        trajectory_row,
        evaluation_row,
        teacher_used,
        protocol_id,
        taxonomy_profile_id,
        failure_defaults,
    )
    state = review_state(feedback_decision, failure_assessment)
    failure_ids = [
        failure_tag.get("failure_id", "")
        for failure_tag in failure_assessment.get("failure_tags", [])
        if failure_tag.get("failure_id", "")
    ]
    packet = {
        "schema_version": "sample_packet_v1",
        "sample_id": f"{config.run_id}::{trajectory_row['scenario_id']}::{trajectory_row['sample_index']:02d}",
        "scenario_id": trajectory_row["scenario_id"],
        "sample_index": trajectory_row["sample_index"],
        "attempt_metadata": {
            "attempt_index": attempt_index,
            "attempt_kind": attempt_kind,
            "auto_repaired": auto_repaired,
            "repair_origin_sample_id": repair_origin_sample_id,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario_snapshot(scenario),
        "pipeline": {
            "run_id": config.run_id,
            "generation_run_id": config.generator_run_id,
            "evaluation_run_id": config.evaluator_run_id,
            "pipeline_mode": generation_manifest["pipeline_mode"],
            "trajectory_profile": generation_manifest["profile"],
            "trajectory_schema_version": generation_manifest["schema_version"],
            "teacher_schema_version": generation_manifest.get("teacher_schema_version"),
            "feedback_protocol_profile": protocol_id,
            "failure_taxonomy_profile": taxonomy_profile_id,
            "evaluation_profile": evaluation_manifest["evaluation_profile"],
            "evaluation_schema_version": evaluation_manifest["evaluation_schema_version"],
        },
        "contracts": {
            "teacher_agent": {
                "input_schema_version": "teacher_agent_input_v1" if teacher_used else None,
                "output_schema_version": generation_manifest.get("teacher_schema_version"),
                "input_ref": rel_to_root(config, teacher_input_path),
                "status": teacher_status,
            },
            "evaluator_agent": {
                "input_schema_version": "evaluator_agent_input_v1" if evaluator_input_path else None,
                "output_schema_version": evaluation_manifest["evaluation_schema_version"],
                "input_ref": rel_to_root(config, evaluator_input_path),
                "status": evaluator_status,
            },
        },
        "artifacts": {
            "teacher_analysis": teacher_row.get("teacher_analysis") if teacher_row else None,
            "trajectory": trajectory_row.get("trajectory"),
            "evaluation": evaluation_row.get("evaluation") if evaluation_row else None,
        },
        "repair_history": copy.deepcopy(repair_history or []),
        "failure_assessment": failure_assessment,
        "feedback_decision": feedback_decision,
        "summary": {
            "generation_parse_status": trajectory_row.get("parse_status", ""),
            "teacher_parse_status": trajectory_row.get("teacher_parse_status")
            or (teacher_row.get("parse_status", "") if teacher_row else ""),
            "evaluation_parse_status": evaluation_row.get("parse_status", "") if evaluation_row else "",
            "teacher_recommended_action_label": trajectory_row.get("teacher_recommended_action_label")
            or (teacher_row.get("recommended_action_label", "") if teacher_row else ""),
            "chosen_action_label": trajectory_row.get("chosen_action_label", ""),
            "overall_verdict": evaluation_row.get("overall_verdict", "") if evaluation_row else "",
            "rewrite_priority": evaluation_row.get("rewrite_priority", "") if evaluation_row else "",
            "feedback_rule_id": feedback_decision.get("rule_id"),
            "feedback_suggested_status": feedback_decision.get("suggested_status"),
            "feedback_next_action": feedback_decision.get("suggested_next_action"),
            "primary_failure_id": failure_assessment.get("primary_failure_id"),
            "highest_failure_severity": failure_assessment.get("highest_severity"),
            "failure_ids": failure_ids,
            "axis_scores": evaluation_row.get("axis_scores", {}) if evaluation_row else {},
        },
        "quality_signals": {
            "assistant_contamination_risk": trajectory_row.get("assistant_contamination_risk"),
            "over_explaining_risk": trajectory_row.get("over_explaining_risk"),
            "world_model_consistency": trajectory_row.get("world_model_consistency"),
            "assistant_contamination_detected": evaluation_row.get("assistant_contamination_detected")
            if evaluation_row
            else None,
        },
        "review_state": state,
        "provenance": {
            "generator_model_profile": generation_manifest["model_profile"],
            "generator_model": trajectory_row.get("model", generation_manifest["model_id"]),
            "evaluator_model_profile": evaluation_manifest["model_profile"],
            "evaluator_model": evaluation_row.get("evaluation_model", evaluation_manifest["model_id"])
            if evaluation_row
            else evaluation_manifest["model_id"],
            "generation_usage": trajectory_row.get("usage"),
            "teacher_usage": trajectory_row.get("teacher_usage"),
            "evaluation_usage": evaluation_row.get("usage") if evaluation_row else None,
            "source_refs": {
                "generation_manifest_ref": rel_to_root(config, generation_manifest_path),
                "evaluation_manifest_ref": rel_to_root(config, evaluation_manifest_path),
                "teacher_request_ref": rel_to_root(config, teacher_request_path),
                "teacher_raw_ref": rel_to_root(config, teacher_raw_path),
                "trajectory_request_ref": rel_to_root(config, trajectory_request_path),
                "trajectory_raw_ref": rel_to_root(config, trajectory_raw_path),
                "evaluation_request_ref": rel_to_root(config, evaluation_request_path),
                "evaluation_raw_ref": rel_to_root(config, evaluation_raw_path),
            },
        },
    }
    validate_packet(packet)
    return packet


def write_pipeline_manifest(
    config: Config,
    scenario_ids: list[str],
    generation_manifest: dict[str, Any],
    evaluation_manifest: dict[str, Any],
    packet_count: int,
    auto_repaired_packet_count: int,
    repair_attempt_count: int,
) -> None:
    manifest = {
        "run_id": config.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_ids": scenario_ids,
        "samples": config.samples,
        "sample_packet_schema": str(config.sample_packet_schema_path.relative_to(config.root_dir)),
        "repair_instruction_schema": str(
            config.repair_instruction_schema_path.relative_to(config.root_dir)
        ),
        "teacher_input_schema": str(config.teacher_input_schema_path.relative_to(config.root_dir)),
        "evaluator_input_schema": str(config.evaluator_input_schema_path.relative_to(config.root_dir)),
        "generation_manifest_ref": str(
            (config.run_dir / config.generator_run_id / "manifest.json").relative_to(config.root_dir)
        ),
        "evaluation_manifest_ref": str(
            (config.run_dir / config.evaluator_run_id / "manifest.json").relative_to(config.root_dir)
        ),
        "generation": {
            "run_id": generation_manifest["run_id"],
            "model_profile": generation_manifest["model_profile"],
            "model_id": generation_manifest["model_id"],
            "profile": generation_manifest["profile"],
            "pipeline_mode": generation_manifest["pipeline_mode"],
        },
        "evaluation": {
            "run_id": evaluation_manifest["run_id"],
            "model_profile": evaluation_manifest["model_profile"],
            "model_id": evaluation_manifest["model_id"],
            "evaluation_profile": evaluation_manifest["evaluation_profile"],
            "feedback_protocol_profile": evaluation_manifest.get("feedback_protocol_profile", ""),
            "feedback_protocol_profile_path": evaluation_manifest.get("feedback_protocol_profile_path", ""),
            "failure_taxonomy_profile": evaluation_manifest.get("failure_taxonomy_profile", ""),
            "failure_taxonomy_profile_path": evaluation_manifest.get("failure_taxonomy_profile_path", ""),
        },
        "auto_repair": {
            "enabled": config.auto_repair,
            "max_repair_attempts": config.max_repair_attempts,
            "auto_repaired_packet_count": auto_repaired_packet_count,
            "repair_attempt_count": repair_attempt_count,
        },
        "packet_count": packet_count,
        "output_files": {
            "sample_packets": str(config.sample_packets_file.relative_to(config.root_dir)),
            "sample_packet_dir": str(config.packet_dir.relative_to(config.root_dir)),
            "teacher_inputs": str(config.teacher_input_dir.relative_to(config.root_dir)),
            "evaluator_inputs": str(config.evaluator_input_dir.relative_to(config.root_dir)),
            "summary": str(config.summary_file.relative_to(config.root_dir)),
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
    print(f"generator_stage={config.generator_run_id} scenarios={scenario_ids} samples={config.samples}")
    run_stage(generator_command, generator_env, config.root_dir)

    generation_manifest_path, generation_manifest, trajectory_rows, teacher_index = load_generation_stage_outputs(
        config.root_dir,
        config.run_dir,
    )
    trajectories_file = config.root_dir / generation_manifest["output_files"]["trajectories"]

    evaluator_command = [sys.executable, str(config.evaluator_script), str(trajectories_file)]
    evaluator_env = build_evaluator_env(config)
    print(f"evaluator_stage={config.evaluator_run_id} trajectory_file={trajectories_file}")
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

    teacher_request_dir = None
    teacher_raw_dir = None
    if generation_manifest["pipeline_mode"] == "teacher_compress":
        teacher_request_dir = config.root_dir / generation_manifest["output_files"]["teacher_requests"]
        teacher_raw_dir = config.root_dir / generation_manifest["output_files"]["teacher_raw"]
    trajectory_request_dir = config.root_dir / generation_manifest["output_files"]["requests"]
    trajectory_raw_dir = config.root_dir / generation_manifest["output_files"]["raw"]
    evaluation_request_dir = config.root_dir / evaluation_manifest["output_files"]["requests"]
    evaluation_raw_dir = config.root_dir / evaluation_manifest["output_files"]["raw"]
    repair_attempt_count = 0
    auto_repaired_packet_count = 0

    with config.sample_packets_file.open("a", encoding="utf-8") as handle:
        for trajectory_row in trajectory_rows:
            key = (trajectory_row["scenario_id"], trajectory_row["sample_index"])
            scenario = scenarios_by_id[trajectory_row["scenario_id"]]
            row_key = sample_key(trajectory_row["scenario_id"], trajectory_row["sample_index"])
            teacher_row = teacher_index.get(key)
            evaluation_row = evaluation_index.get(key)

            teacher_input_path = None
            if generation_manifest["pipeline_mode"] == "teacher_compress":
                teacher_input_path = config.teacher_input_dir / f"{row_key}.json"
                write_json(teacher_input_path, build_teacher_input(scenario, generation_manifest))

            evaluator_input_path = None
            if trajectory_row.get("parse_status") == "ok" and trajectory_row.get("trajectory"):
                evaluator_input_path = config.evaluator_input_dir / f"{row_key}.json"
                write_json(
                    evaluator_input_path,
                    build_evaluator_input(scenario, trajectory_row, evaluation_manifest),
                )

            packet = build_sample_packet(
                config=config,
                scenario=scenario,
                trajectory_row=trajectory_row,
                teacher_row=teacher_row,
                evaluation_row=evaluation_row,
                generation_manifest=generation_manifest,
                evaluation_manifest=evaluation_manifest,
                generation_manifest_path=generation_manifest_path,
                evaluation_manifest_path=evaluation_manifest_path,
                teacher_input_path=teacher_input_path,
                evaluator_input_path=evaluator_input_path,
                teacher_request_path=maybe_path(teacher_request_dir, row_key),
                teacher_raw_path=maybe_path(teacher_raw_dir, row_key),
                trajectory_request_path=maybe_path(trajectory_request_dir, row_key),
                trajectory_raw_path=maybe_path(trajectory_raw_dir, row_key),
                evaluation_request_path=maybe_path(evaluation_request_dir, row_key),
                evaluation_raw_path=maybe_path(evaluation_raw_dir, row_key),
                protocol_id=feedback_protocol_id,
                taxonomy_profile_id=failure_taxonomy_id,
                failure_defaults=failure_defaults,
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

                repair_generation_manifest = repair_result["generation_manifest"]
                repair_evaluation_manifest = repair_result["evaluation_manifest"]
                repair_trajectory_row = repair_result["trajectory_row"]
                repair_teacher_row = repair_result["teacher_row"]
                repair_evaluation_row = repair_result["evaluation_row"]

                repair_teacher_request_dir = None
                repair_teacher_raw_dir = None
                if repair_generation_manifest["pipeline_mode"] == "teacher_compress":
                    repair_teacher_request_dir = config.root_dir / repair_generation_manifest["output_files"]["teacher_requests"]
                    repair_teacher_raw_dir = config.root_dir / repair_generation_manifest["output_files"]["teacher_raw"]
                repair_trajectory_request_dir = config.root_dir / repair_generation_manifest["output_files"]["requests"]
                repair_trajectory_raw_dir = config.root_dir / repair_generation_manifest["output_files"]["raw"]
                repair_evaluation_request_dir = config.root_dir / repair_evaluation_manifest["output_files"]["requests"]
                repair_evaluation_raw_dir = config.root_dir / repair_evaluation_manifest["output_files"]["raw"]

                repaired_packet = build_sample_packet(
                    config=config,
                    scenario=scenario,
                    trajectory_row=repair_trajectory_row,
                    teacher_row=repair_teacher_row,
                    evaluation_row=repair_evaluation_row,
                    generation_manifest=repair_generation_manifest,
                    evaluation_manifest=repair_evaluation_manifest,
                    generation_manifest_path=repair_result["generation_manifest_path"],
                    evaluation_manifest_path=repair_result["evaluation_manifest_path"],
                    teacher_input_path=repair_result["teacher_input_path"],
                    evaluator_input_path=repair_result["evaluator_input_path"],
                    teacher_request_path=maybe_path(repair_teacher_request_dir, row_key),
                    teacher_raw_path=maybe_path(repair_teacher_raw_dir, row_key),
                    trajectory_request_path=maybe_path(repair_trajectory_request_dir, row_key),
                    trajectory_raw_path=maybe_path(repair_trajectory_raw_dir, row_key),
                    evaluation_request_path=maybe_path(repair_evaluation_request_dir, row_key),
                    evaluation_raw_path=maybe_path(repair_evaluation_raw_dir, row_key),
                    protocol_id=feedback_protocol_id,
                    taxonomy_profile_id=failure_taxonomy_id,
                    failure_defaults=failure_defaults,
                    attempt_index=next_attempt_index,
                    attempt_kind="repair",
                    auto_repaired=True,
                    repair_origin_sample_id=packet["sample_id"],
                    repair_history=repair_history,
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
                current_packet = repaired_packet

            if current_packet["attempt_metadata"]["attempt_index"] > 0:
                auto_repaired_packet_count += 1

            packet_path = config.packet_dir / f"{row_key}.json"
            write_json(packet_path, current_packet)
            handle.write(json.dumps(current_packet, ensure_ascii=False) + "\n")
            handle.flush()

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
    print(f"sample_packets={config.sample_packets_file}")
    print(f"pipeline_manifest={config.manifest_file}")


if __name__ == "__main__":
    main()