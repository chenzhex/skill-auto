from __future__ import annotations

from .models import TrialRecord


def finalize_recommendation(record: TrialRecord) -> None:
    if record.security_status == "block":
        record.recommendation = "reject"
        record.recommended_surface = "none"
        record.lazymind_compat_score = 0
        return
    if record.preflight_status == "fail":
        record.recommendation = "reject"
        record.recommended_surface = "none"
        record.lazymind_compat_score = 0
        return

    score = 0
    if record.preflight_status == "pass":
        score += 30
    elif record.preflight_status == "partial":
        score += 18
    if record.install_status == "pass":
        score += 25
    elif record.install_status == "skipped":
        score += 8
    if record.run_status == "pass" and not record.flaky:
        score += 30
    elif record.run_status == "pass":
        score += 24
    elif record.run_status == "skipped":
        score += 8
    if not record.requires_api_key or not record.required_env_keys:
        score += 5
    if record.security_status == "pass":
        score += 10
    if record.run_status == "pass" and record.skill_trigger_status != "confirmed":
        score -= 20
    if record.skill_execution_status == "degraded":
        score -= 15
    elif record.skill_execution_status in {"blocked", "failed"}:
        score -= 30

    record.lazymind_compat_score = max(min(score, 100), 0)
    if record.run_status == "pass" and record.skill_trigger_status == "confirmed" and record.skill_execution_status == "success":
        record.quality_score = 4.0
    elif record.run_status == "pass" and record.skill_trigger_status == "confirmed" and record.skill_execution_status == "degraded":
        record.quality_score = 2.5
    else:
        record.quality_score = 0.0
    if record.run_status == "pass" and not record.flaky:
        record.stability_score = 4.0
    elif record.run_status == "pass":
        record.stability_score = 2.5
    else:
        record.stability_score = 0.0
    record.visual_demo_score = 3.0 if record.detected_skill_type in {"data", "design", "presentation"} else 1.5
    record.demo_candidate = record.visual_demo_score >= 3 and record.lazymind_compat_score >= 70

    if (
        record.run_status == "pass"
        and record.install_status == "pass"
        and record.skill_trigger_status == "confirmed"
        and record.skill_execution_status == "success"
        and record.lazymind_compat_score >= 85
    ):
        record.recommendation = "onboard"
        record.recommended_surface = "both" if record.demo_candidate else "builtin"
    elif record.preflight_status in {"pass", "partial"} and record.security_status != "block":
        record.recommendation = "hold" if record.failure_category else "manual_review"
        record.recommended_surface = "none"
    else:
        record.recommendation = "reject"
        record.recommended_surface = "none"
