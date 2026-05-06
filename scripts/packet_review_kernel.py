#!/usr/bin/env python3

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class PacketKernelConfig(Protocol):
    root_dir: Path
    run_id: str
    generator_run_id: str
    evaluator_run_id: str


@dataclass(frozen=True)
class AttemptContext:
    attempt_index: int = 0
    attempt_kind: str = "initial"
    auto_repaired: bool = False
    repair_origin_sample_id: str | None = None

    def as_metadata(self) -> dict[str, Any]:
        return {
            "attempt_index": self.attempt_index,
            "attempt_kind": self.attempt_kind,
            "auto_repaired": self.auto_repaired,
            "repair_origin_sample_id": self.repair_origin_sample_id,
        }


def rel_to_root(config: PacketKernelConfig, path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return str(path.relative_to(config.root_dir))


@dataclass(frozen=True)
class ProvenancePaths:
    generation_manifest_path: Path
    evaluation_manifest_path: Path
    teacher_input_path: Path | None = None
    evaluator_input_path: Path | None = None
    teacher_request_path: Path | None = None
    teacher_raw_path: Path | None = None
    trajectory_request_path: Path | None = None
    trajectory_raw_path: Path | None = None
    evaluation_request_path: Path | None = None
    evaluation_raw_path: Path | None = None

    def source_refs(self, config: PacketKernelConfig) -> dict[str, str | None]:
        return {
            "generation_manifest_ref": rel_to_root(config, self.generation_manifest_path),
            "evaluation_manifest_ref": rel_to_root(config, self.evaluation_manifest_path),
            "teacher_request_ref": rel_to_root(config, self.teacher_request_path),
            "teacher_raw_ref": rel_to_root(config, self.teacher_raw_path),
            "trajectory_request_ref": rel_to_root(config, self.trajectory_request_path),
            "trajectory_raw_ref": rel_to_root(config, self.trajectory_raw_path),
            "evaluation_request_ref": rel_to_root(config, self.evaluation_request_path),
            "evaluation_raw_ref": rel_to_root(config, self.evaluation_raw_path),
        }


def scenario_snapshot(scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "profile": scenario["profile"],
        "situation": scenario["situation"],
        "task": scenario["task"],
        "action_options": list(scenario.get("action_options", [])),
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
        parse_status = (
            evaluation_row.get("parse_status", "missing_evaluation")
            if evaluation_row
            else "missing_evaluation"
        )
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
    if (
        packet["contracts"]["teacher_agent"]["status"] == "completed"
        and packet["artifacts"]["teacher_analysis"] is None
    ):
        raise ValueError("teacher contract marked completed without teacher artifact")
    if packet["artifacts"]["trajectory"] is None and packet["summary"]["generation_parse_status"] == "ok":
        raise ValueError("trajectory parse status ok but trajectory artifact missing")
    if (
        packet["contracts"]["evaluator_agent"]["status"] == "completed"
        and packet["artifacts"]["evaluation"] is None
    ):
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


def resolve_provenance_paths(
    config: PacketKernelConfig,
    *,
    generation_manifest_path: Path,
    generation_manifest: dict[str, Any],
    evaluation_manifest_path: Path,
    evaluation_manifest: dict[str, Any],
    row_key: str,
    teacher_input_path: Path | None = None,
    evaluator_input_path: Path | None = None,
) -> ProvenancePaths:
    teacher_request_dir = None
    teacher_raw_dir = None
    if generation_manifest["pipeline_mode"] == "teacher_compress":
        teacher_request_dir = config.root_dir / generation_manifest["output_files"]["teacher_requests"]
        teacher_raw_dir = config.root_dir / generation_manifest["output_files"]["teacher_raw"]

    trajectory_request_dir = config.root_dir / generation_manifest["output_files"]["requests"]
    trajectory_raw_dir = config.root_dir / generation_manifest["output_files"]["raw"]
    evaluation_request_dir = config.root_dir / evaluation_manifest["output_files"]["requests"]
    evaluation_raw_dir = config.root_dir / evaluation_manifest["output_files"]["raw"]

    return ProvenancePaths(
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


def build_sample_packet(
    config: PacketKernelConfig,
    scenario: dict[str, Any],
    trajectory_row: dict[str, Any],
    teacher_row: dict[str, Any] | None,
    evaluation_row: dict[str, Any] | None,
    generation_manifest: dict[str, Any],
    evaluation_manifest: dict[str, Any],
    provenance_paths: ProvenancePaths,
    protocol_id: str | None,
    taxonomy_profile_id: str | None,
    failure_defaults: dict[str, dict[str, Any]],
    attempt_context: AttemptContext | None = None,
    repair_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    attempt_context = attempt_context or AttemptContext()
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
        "attempt_metadata": attempt_context.as_metadata(),
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
                "input_ref": rel_to_root(config, provenance_paths.teacher_input_path),
                "status": teacher_status,
            },
            "evaluator_agent": {
                "input_schema_version": "evaluator_agent_input_v1"
                if provenance_paths.evaluator_input_path
                else None,
                "output_schema_version": evaluation_manifest["evaluation_schema_version"],
                "input_ref": rel_to_root(config, provenance_paths.evaluator_input_path),
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
            "source_refs": provenance_paths.source_refs(config),
        },
    }
    validate_packet(packet)
    return packet