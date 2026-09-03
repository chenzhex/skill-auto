from __future__ import annotations

import json
from pathlib import Path

from .manifest import write_yaml
from .models import TrialRecord


def write_reports(out_dir: Path, records: list[TrialRecord]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    trials_path = out_dir / "skill_trials.jsonl"
    with trials_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    candidates = [
        {
            "name": record.name,
            "link": record.link,
            "onboard_as": {"builtin": True, "featured": record.demo_candidate},
            "trial_id": record.trial_id,
            "score": record.lazymind_compat_score,
        }
        for record in records
        if record.recommendation == "onboard"
    ]
    write_yaml(out_dir / "onboarding_candidates.yaml", {"schema_version": 1, "skills": candidates})

    install_passed_manifest = [
        manifest_item(record)
        for record in records
        if record.install_status == "pass" and record.preflight_status in {"pass", "partial"}
    ]
    write_yaml(out_dir / "install_passed.yaml", {"schema_version": 1, "skills": install_passed_manifest})

    smoke_passed = [
        manifest_item(record, include_smoke_risk=True)
        for record in records
        if should_enter_demo(record)
    ]
    write_yaml(out_dir / "smoke_passed.yaml", {"schema_version": 1, "skills": smoke_passed})

    bad_cases = [
        {
            "name": record.name,
            "link": record.link,
            "test_mode": record.test_mode,
            "run_chat": record.run_chat,
            "skill_trigger_status": record.skill_trigger_status,
            "skill_execution_status": record.skill_execution_status,
            "failure_category": record.failure_category,
            "failure_user_message": record.failure_user_message,
            "failure_technical_reason": record.failure_technical_reason,
            "suggested_fix": record.suggested_fix,
        }
        for record in records
        if record.failure_category and record.failure_category != "model_rate_limited_passed"
    ]
    write_yaml(out_dir / "bad_cases.yaml", {"schema_version": 1, "skills": bad_cases})

    passed = sum(1 for record in records if record.run_status == "pass")
    install_passed_count = sum(1 for record in records if record.install_status == "pass")
    execution_counts = {
        status: sum(1 for record in records if record.skill_execution_status == status)
        for status in ("success", "degraded", "blocked", "failed", "not_tested")
    }
    failure_counts: dict[str, int] = {}
    for record in records:
        if record.failure_category:
            failure_counts[record.failure_category] = failure_counts.get(record.failure_category, 0) + 1
    summary = [
        f"# Skill Trial Summary",
        "",
        f"- Total: {len(records)}",
        f"- Install passed: {install_passed_count}",
        f"- Run passed: {passed}",
        f"- Execution success: {execution_counts['success']}",
        f"- Execution degraded: {execution_counts['degraded']}",
        f"- Execution blocked: {execution_counts['blocked']}",
        f"- Execution failed: {execution_counts['failed']}",
        f"- Onboarding candidates: {len(candidates)}",
        f"- Bad cases: {len(bad_cases)}",
    ]
    if failure_counts:
        summary.append("- Failure categories:")
        for category, count in sorted(failure_counts.items()):
            summary.append(f"  - {category}: {count}")
    summary.append("")
    for record in records:
        summary.append(
            f"- {record.name}: preflight={record.preflight_status}, "
            f"mode={record.test_mode}, run_chat={record.run_chat}, "
            f"install={record.install_status}, run={record.run_status}, "
            f"trigger={record.skill_trigger_status}, "
            f"execution={record.skill_execution_status}, "
            f"semantic={record.semantic_eval_status}, "
            f"attempts={record.total_attempts}, flaky={record.flaky}, "
            f"score={record.lazymind_compat_score}, recommendation={record.recommendation}"
        )
    (out_dir / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")

    bugs = ["# Skill Bad Case Bugs", ""]
    for item in bad_cases:
        bugs.extend(
            [
                f"## {item['name']}",
                "",
                f"- Category: {item['failure_category']}",
                f"- User message: {item['failure_user_message']}",
                f"- Technical reason: {item['failure_technical_reason']}",
                f"- Suggested fix: {item['suggested_fix']}",
                "",
            ]
        )
    (out_dir / "bugs.md").write_text("\n".join(bugs), encoding="utf-8")


def should_enter_demo(record: TrialRecord) -> bool:
    hard_blockers = {
        "blocked_by_env",
        "permission_or_auth_required",
        "model_rate_limited",
        "timeout",
        "chat_api_unreachable",
        "lazymind_unauthorized",
        "lazymind_api_unavailable",
        "source_unavailable",
        "install_failed",
        "invalid_package",
    }
    if record.test_mode != "smoke":
        return record.run_status == "pass" and record.skill_trigger_status == "confirmed"
    if record.install_status != "pass" or record.run_status != "pass":
        return False
    if record.failure_category == "model_rate_limited_passed":
        return True
    if record.skill_trigger_status != "confirmed":
        return False
    return record.failure_category not in hard_blockers


def smoke_risks(record: TrialRecord) -> list[str]:
    risks: list[str] = []
    if record.skill_execution_status not in {"success", "not_tested"}:
        risks.append(f"execution_{record.skill_execution_status}")
    if record.failure_category:
        risks.append(record.failure_category)
    return sorted(set(risks))


def manifest_item(record: TrialRecord, include_smoke_risk: bool = False) -> dict[str, object]:
    item: dict[str, object] = {"name": record.name, "link": record.link}
    if record.runtime_skill_name and record.runtime_skill_name != record.name:
        item["runtime_skill_name"] = record.runtime_skill_name
    if include_smoke_risk:
        risks = smoke_risks(record)
        if risks:
            item["smoke_risk"] = risks
    return item
