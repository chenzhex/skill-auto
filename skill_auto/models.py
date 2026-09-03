from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class SkillSource:
    name: str
    link: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrialRecord:
    trial_id: str
    name: str
    link: str
    runtime_skill_name: str | None = None
    source_type: str = "unknown"
    source_commit: str | None = None
    test_mode: str = "demo"
    run_chat: bool = True
    stars: int | None = None
    license: str | None = None
    package_size_mb: float | None = None
    detected_skill_type: str | None = None
    detected_industry: str | None = None
    detected_scenario: str | None = None
    requires_api_key: bool = False
    required_env_keys: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    security_status: str = "pass"
    preflight_status: str = "unknown"
    install_status: str = "skipped"
    run_status: str = "skipped"
    skill_trigger_status: str = "not_tested"
    skill_triggered: bool = False
    skill_trigger_evidence: list[str] = field(default_factory=list)
    skill_execution_status: str = "not_tested"
    skill_execution_evidence: list[str] = field(default_factory=list)
    semantic_eval_status: str = "not_tested"
    semantic_eval_error: str | None = None
    quality_score: float = 0.0
    stability_score: float = 0.0
    visual_demo_score: float = 0.0
    lazymind_compat_score: int = 0
    total_attempts: int = 0
    flaky: bool = False
    recommendation: str = "hold"
    recommended_surface: str = "none"
    demo_candidate: bool = False
    generated_test_cases: list[dict[str, Any]] = field(default_factory=list)
    chat_observations: list[dict[str, Any]] = field(default_factory=list)
    output_artifacts: list[str] = field(default_factory=list)
    run_log_path: str | None = None
    failure_category: str | None = None
    failure_user_message: str | None = None
    failure_technical_reason: str | None = None
    missing_dependencies: list[str] = field(default_factory=list)
    suggested_fix: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
