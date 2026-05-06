#!/usr/bin/env python3

import argparse
import collections
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
DEFAULT_TEACHER_INPUT_SCHEMA_PATH = "schemas/teacher_agent_input_v1.json"
DEFAULT_EVALUATOR_INPUT_SCHEMA_PATH = "schemas/evaluator_agent_input_v1.json"
DEFAULT_EVALUATION_PROFILE_PATH = "profiles/evaluation_profile_constitutional_v1.json"
DEFAULT_EVALUATION_SCHEMA_PATH = "schemas/trajectory_evaluation_v1.json"
DEFAULT_TRAJECTORY_PROFILE = "analysis_teacher_compress_v1"
DEFAULT_TRAJECTORY_PIPELINE = "teacher_compress"
DEFAULT_SAMPLES = 3


@dataclass(frozen=True)
class Config:
    root_dir: Path
    scenario_dir: Path
    results_dir: Path
    run_id: str
    run_dir: Path
    packet_dir: Path
    teacher_input_dir: Path
    evaluator_input_dir: Path
    sample_packets_file: Path
    summary_file: Path
    manifest_file: Path
    sample_packet_schema_path: Path
    teacher_input_schema_path: Path
    evaluator_input_schema_path: Path
    generator_script: Path
    evaluator_script: Path
    generator_run_id: str
    evaluator_run_id: str
    samples: int
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
        packet_dir=run_dir / "sample_packets",
        teacher_input_dir=run_dir / "interfaces" / "teacher_inputs",
        evaluator_input_dir=run_dir / "interfaces" / "evaluator_inputs",
        sample_packets_file=run_dir / "sample_packets.jsonl",
        summary_file=run_dir / "pipeline_summary.txt",
        manifest_file=run_dir / "pipeline_manifest.json",
        sample_packet_schema_path=root_dir
        / os.environ.get("SAMPLE_PACKET_SCHEMA_FILE", DEFAULT_SAMPLE_PACKET_SCHEMA_PATH),
        teacher_input_schema_path=root_dir
        / os.environ.get("TEACHER_INPUT_SCHEMA_FILE", DEFAULT_TEACHER_INPUT_SCHEMA_PATH),
        evaluator_input_schema_path=root_dir
        / os.environ.get("EVALUATOR_INPUT_SCHEMA_FILE", DEFAULT_EVALUATOR_INPUT_SCHEMA_PATH),
        generator_script=root_dir / "scripts" / "generate_state_trajectories.py",
        evaluator_script=root_dir / "scripts" / "evaluate_trajectories.py",
        generator_run_id="generate",
        evaluator_run_id="evaluate",
        samples=int(os.environ.get("SAMPLES", str(DEFAULT_SAMPLES))),
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
        config.teacher_input_schema_path,
        config.evaluator_input_schema_path,
        config.generator_script,
        config.evaluator_script,
        config.root_dir / config.evaluation_profile_file,
        config.root_dir / config.evaluation_schema_file,
    ]:
        if not path.is_file():
            raise SystemExit(f"Required file not found: {path}")
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


def build_generator_env(config: Config) -> dict[str, str]:
    env = stage_env(os.environ, "GENERATOR")
    env["RESULTS_DIR"] = str(config.run_dir)
    env["RUN_ID"] = config.generator_run_id
    env["SAMPLES"] = str(config.samples)
    env["TRAJECTORY_PROFILE"] = config.trajectory_profile
    env["TRAJECTORY_PIPELINE"] = config.trajectory_pipeline
    return env


def build_evaluator_env(config: Config) -> dict[str, str]:
    env = stage_env(os.environ, "EVALUATOR")
    env["RESULTS_DIR"] = str(config.run_dir)
    env["RUN_ID"] = config.evaluator_run_id
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


def review_state(
    trajectory_row: dict[str, Any],
    evaluation_row: dict[str, Any] | None,
    teacher_used: bool,
) -> dict[str, str]:
    if trajectory_row.get("parse_status") != "ok":
        if teacher_used and trajectory_row.get("teacher_parse_status") == "ok":
            return {
                "status": "needs_revision",
                "next_action": "regenerate_from_teacher",
                "reason": "trajectory_parse_failed_after_teacher",
            }
        return {
            "status": "manual_review",
            "next_action": "rerun_generation",
            "reason": "trajectory_parse_failed",
        }

    if evaluation_row is None or evaluation_row.get("parse_status") != "ok":
        return {
            "status": "manual_review",
            "next_action": "manual_review",
            "reason": "evaluation_parse_failed",
        }

    verdict = evaluation_row.get("overall_verdict", "")
    if verdict == "keep":
        return {
            "status": "approved",
            "next_action": "approve",
            "reason": "evaluation_keep",
        }
    if verdict == "revise":
        return {
            "status": "needs_revision",
            "next_action": "regenerate_from_teacher" if teacher_used else "revise_prompt_local",
            "reason": "evaluation_revise",
        }
    if verdict == "manual_review":
        return {
            "status": "manual_review",
            "next_action": "manual_review",
            "reason": "evaluation_manual_review",
        }
    if verdict == "reject":
        return {
            "status": "rejected",
            "next_action": "reject",
            "reason": "evaluation_reject",
        }
    return {
        "status": "manual_review",
        "next_action": "manual_review",
        "reason": "evaluation_unknown_verdict",
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

    state = review_state(trajectory_row, evaluation_row, teacher_used)
    packet = {
        "schema_version": "sample_packet_v1",
        "sample_id": f"{config.run_id}::{trajectory_row['scenario_id']}::{trajectory_row['sample_index']:02d}",
        "scenario_id": trajectory_row["scenario_id"],
        "sample_index": trajectory_row["sample_index"],
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
) -> None:
    manifest = {
        "run_id": config.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_ids": scenario_ids,
        "samples": config.samples,
        "sample_packet_schema": str(config.sample_packet_schema_path.relative_to(config.root_dir)),
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

    status_counts: collections.Counter[str] = collections.Counter()
    next_action_counts: collections.Counter[str] = collections.Counter()
    verdict_counts: collections.Counter[str] = collections.Counter()
    axis_scores: dict[str, list[int]] = collections.defaultdict(list)
    by_scenario: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)

    for row in rows:
        status_counts[row["review_state"]["status"]] += 1
        next_action_counts[row["review_state"]["next_action"]] += 1
        verdict_counts[row["summary"].get("overall_verdict") or "<empty>"] += 1
        by_scenario[row["scenario_id"]].append(row)
        for axis_id, score in row["summary"].get("axis_scores", {}).items():
            if isinstance(score, int):
                axis_scores[axis_id].append(score)

    lines.append(f"review_state={dict(sorted(status_counts.items()))}")
    lines.append(f"next_action={dict(sorted(next_action_counts.items()))}")
    lines.append(f"overall_verdict={dict(sorted(verdict_counts.items()))}")
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

    generation_manifest_path = config.run_dir / config.generator_run_id / "manifest.json"
    generation_manifest = load_json(generation_manifest_path)
    trajectories_file = config.root_dir / generation_manifest["output_files"]["trajectories"]
    trajectory_rows = load_jsonl(trajectories_file)
    trajectory_index = {
        (row["scenario_id"], row["sample_index"]): row for row in trajectory_rows
    }

    teacher_rows: list[dict[str, Any]] = []
    teacher_index: dict[tuple[str, int], dict[str, Any]] = {}
    if generation_manifest["pipeline_mode"] == "teacher_compress":
        teacher_file = config.root_dir / generation_manifest["output_files"]["teacher_analyses"]
        teacher_rows = load_jsonl(teacher_file)
        teacher_index = {
            (row["scenario_id"], row["sample_index"]): row for row in teacher_rows
        }

    evaluator_command = [sys.executable, str(config.evaluator_script), str(trajectories_file)]
    evaluator_env = build_evaluator_env(config)
    print(f"evaluator_stage={config.evaluator_run_id} trajectory_file={trajectories_file}")
    run_stage(evaluator_command, evaluator_env, config.root_dir)

    evaluation_manifest_path = config.run_dir / config.evaluator_run_id / "manifest.json"
    evaluation_manifest = load_json(evaluation_manifest_path)
    evaluations_file = config.root_dir / evaluation_manifest["output_files"]["evaluations"]
    evaluation_rows = load_jsonl(evaluations_file)
    evaluation_index = {
        (row["scenario_id"], row["sample_index"]): row for row in evaluation_rows
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
            )

            packet_path = config.packet_dir / f"{row_key}.json"
            write_json(packet_path, packet)
            handle.write(json.dumps(packet, ensure_ascii=False) + "\n")
            handle.flush()

    write_pipeline_manifest(
        config,
        scenario_ids,
        generation_manifest,
        evaluation_manifest,
        len(trajectory_rows),
    )
    summary_text = write_summary(config.sample_packets_file, config.summary_file)
    print(summary_text, end="")
    print(f"sample_packets={config.sample_packets_file}")
    print(f"pipeline_manifest={config.manifest_file}")


if __name__ == "__main__":
    main()