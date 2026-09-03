from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SEMANTIC_STATUSES = {"success", "degraded", "blocked", "failed", "not_tested"}
TRIGGER_STATUSES = {"confirmed", "requested_only", "not_triggered", "unclear", "not_tested"}


def semantic_evaluation_enabled() -> bool:
    return os.environ.get("SKILL_AUTO_SEMANTIC_EVAL", "true").strip().lower() not in {"0", "false", "no", "off"}


def evaluate_demo_observation(
    *,
    base_url: str,
    skill_name: str,
    skill_link: str,
    case: dict[str, Any],
    observation: dict[str, Any],
) -> dict[str, Any] | None:
    if not semantic_evaluation_enabled():
        return None
    if observation.get("failure_category") == "model_rate_limited":
        return None
    response = str(observation.get("response_excerpt") or "")
    if not response.strip():
        return None
    prompt = build_codex_semantic_eval_prompt(
        {
            "skill_name": skill_name,
            "skill_link": skill_link,
            "case_id": str(case.get("id") or observation.get("case_id") or "core-flow"),
            "case_prompt": str(case.get("prompt") or observation.get("prompt") or ""),
            "rule_trigger_status": observation.get("skill_trigger_status"),
            "rule_trigger_evidence": observation.get("skill_trigger_evidence") or [],
            "rule_execution_status": observation.get("skill_execution_status"),
            "rule_execution_evidence": observation.get("skill_execution_evidence") or [],
            "rule_failure_category": observation.get("failure_category"),
            "lazy_mind_response": response,
        }
    )
    text = run_codex_semantic_prompt(prompt)
    if not text:
        return {"semantic_eval_status": "failed", "semantic_eval_error": "codex semantic evaluation returned empty output"}
    output = parse_semantic_eval_response(text)
    if output is None:
        return {"semantic_eval_status": "failed", "semantic_eval_error": "codex semantic evaluation returned invalid JSON"}
    normalized = normalize_semantic_output(output)
    normalized["semantic_eval_status"] = "succeeded"
    normalized["semantic_eval_model"] = "codex"
    return normalized


def apply_semantic_result(observation: dict[str, Any], semantic: dict[str, Any] | None) -> None:
    if not semantic:
        return
    observation["semantic_evaluation"] = semantic
    if semantic.get("semantic_eval_status") != "succeeded":
        return
    observation["rule_skill_trigger_status"] = observation.get("skill_trigger_status")
    observation["rule_skill_execution_status"] = observation.get("skill_execution_status")
    observation["rule_failure_category"] = observation.get("failure_category")
    observation["skill_trigger_status"] = semantic["trigger_status"]
    observation["skill_triggered"] = semantic["trigger_status"] == "confirmed"
    observation["skill_trigger_evidence"] = semantic["evidence"]
    observation["skill_execution_status"] = semantic["skill_execution_status"]
    observation["skill_execution_evidence"] = semantic["evidence"]
    failure_category = semantic.get("failure_category")
    if failure_category and failure_category != "none":
        observation["failure_category"] = failure_category
        observation["failure_user_message"] = semantic.get("reason") or "语义评测认为该 Skill 未完成预期任务。"
        observation["failure_technical_reason"] = semantic.get("reason") or failure_category
        observation["suggested_fix"] = semantic.get("suggested_fix") or observation.get("suggested_fix")
    else:
        observation["failure_category"] = None
        observation["failure_user_message"] = None
        observation["failure_technical_reason"] = None
    observation["status"] = "pass" if semantic["skill_execution_status"] in {"success", "degraded"} else "fail"


def normalize_semantic_output(output: dict[str, Any]) -> dict[str, Any]:
    trigger_status = str(output.get("trigger_status") or "unclear")
    if trigger_status not in TRIGGER_STATUSES:
        trigger_status = "unclear"
    execution_status = str(output.get("skill_execution_status") or "failed")
    if execution_status not in SEMANTIC_STATUSES:
        execution_status = "failed"
    confidence = output.get("confidence", 0)
    try:
        confidence_float = float(confidence)
    except (TypeError, ValueError):
        confidence_float = 0.0
    evidence = output.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    evidence = [str(item)[:300] for item in evidence if str(item).strip()][:10]
    if not evidence:
        evidence = [str(output.get("reason") or "semantic evaluation")]
    return {
        "trigger_status": trigger_status,
        "skill_execution_status": execution_status,
        "task_completed": bool(output.get("task_completed")),
        "used_skill": bool(output.get("used_skill")),
        "core_requirement_met": bool(output.get("core_requirement_met")),
        "confidence": max(0.0, min(confidence_float, 1.0)),
        "failure_category": str(output.get("failure_category") or "none"),
        "evidence": evidence,
        "reason": str(output.get("reason") or ""),
        "suggested_fix": str(output.get("suggested_fix") or ""),
    }


def build_semantic_eval_instruction() -> str:
    return """
你是 LazyMind Skill 自动化测试的语义评测器。你的任务是基于“测试请求”和“LazyMind 的最终回复”，判断指定 Skill 是否被使用，以及该 Skill 是否完成了本次测试请求的核心任务。

总原则：
- 只做评测，不要执行测试请求，不要联网，不要调用工具。
- 以最终回复中的可观察证据为准，不要因为单个关键词、单个失败日志或单个工具事件直接下结论。
- 先判断测试请求的核心目标是什么，再判断最终回复是否满足核心目标。
- 区分“Skill 主流程完成但过程中有非关键降级”和“没有依靠 Skill 完成任务”。前者通常是 success，后者才可能是 degraded、blocked 或 failed。

trigger_status 判定：
- confirmed：回复显示模型读取、打开、遵循、运行或引用了指定 Skill，或出现了该 Skill 的说明、脚本、参考资料、工作流、产物路径等明确证据。
- requested_only：测试框架绑定或请求中要求使用该 Skill，但回复没有显示任何实际读取、打开、运行或遵循 Skill 的证据。
- not_triggered：回复明确使用了其他 Skill、其他工具链，或完全没有处理该 Skill。
- unclear：证据不足，无法可靠判断是否实际触发。

skill_execution_status 判定：
- success：指定 Skill 被确认触发，并且最终回复完成了测试请求的核心目标。即使过程中出现非关键步骤失败、重试、外部图片解析失败、部分数据源失败、轻微格式瑕疵，只要回复最终按照该 Skill 的能力或该 Skill 定义的降级方案产出了可验收结果，也应判 success。例如：wechat-cover 使用 Skill 脚本拿到真实文章数据，微信图片因防盗链无法解析，但最终按 Skill 要求基于真实数据生成 HTML 报告，这属于 success；图片解析失败可写入 evidence/reason，不应仅因此判 degraded。
- degraded：指定 Skill 被确认触发，但核心目标只被部分完成，或主要依靠 Skill 之外的合理 fallback 才得到可用但不完整的结果。适用于：Skill 脚本/核心工具失败后，模型改用通用能力完成了简化版任务；产物缺少关键部分但仍有部分价值；外部数据不完整导致结果明显缩水。
- blocked：指定 Skill 被确认触发，但因环境、权限、凭证、网络、依赖、沙箱、浏览器、模型限流等外部或运行时阻塞，无法完成核心目标。适用于：缺少 API key/token、无权限访问、依赖缺失、脚本无法启动、外部服务不可用、模型请求被限流且无可用结果。
- failed：没有完成核心目标，且不是明确的外部阻塞；或回复虽然结束但内容无效、答非所问、只给空泛说明、没有真实执行证据、使用了错误 Skill/错误工具。
- not_tested：本次没有运行 chat 或没有可评测回复。

不同类型任务的验收口径：
- 文本型 Skill：如写作、改写、总结、诊断、推荐、方法论执行，只要回复明显遵循 Skill 工作流并完成用户任务，可以判 success，不要求附件。
- 数据/搜索型 Skill：需要看到真实数据、明确来源线索、查询结果或可验证的结构化结论；如果最终只说无法获取数据，应判 blocked 或 failed。
- 代码/脚本型 Skill：如果 Skill 的脚本或参考流程成功运行，并基于结果完成任务，可以判 success；如果脚本失败但模型绕开 Skill 产出简化答案，通常判 degraded。
- 产物型 Skill：当测试请求明确要求生成 HTML、图片、PPT、Excel、Word、PDF、报告等文件，或 Skill 的核心能力天然要求文件产物时，最终回复应体现文件名、file_id、附件链接、保存路径或“已生成并保存”等交付证据。

附件和文件产物规则：
- 不要把“没有附件/文件产物”作为通用失败标准。
- 只有当测试请求明确要求文件产物，或该 Skill 核心能力天然要求文件产物时，缺少文件产物证据才影响执行状态。
- 如果回复中已经明确给出文件名、file_id、附件链接、保存路径或“已生成并保存”等证据，可以认为存在文件交付物。
- 附件正则提取结果只是辅助证据，不是唯一判定依据。

failure_category 选择：
- none：执行成功，无需归因。
- model_rate_limited：模型服务限流导致没有可用结果。
- missing_env：缺少环境变量、API key、token 或凭证。
- script_failed：Skill 的关键脚本、命令或核心工具运行失败，并影响核心目标。
- network_failed：外部网页、API、图片或数据源访问失败，并影响核心目标。
- permission_or_auth_required：需要登录、授权、权限或付费访问。
- no_real_result：没有真实结果，只有空泛说明、占位内容或无法验收的描述。
- wrong_tool：使用了错误 Skill、错误工具或没有按指定 Skill 执行。
- low_quality_response：回复质量明显不足、答非所问或没有满足基本要求。
- unknown：失败原因无法从回复中判断。

输出要求：
- 只返回一个 JSON object，不要 Markdown，不要解释。
- evidence 最多 5 条，应引用或概括最终回复中的关键证据。
- reason 用一句话说明判断原因；如果 success 但存在非关键问题，也可以在 reason 中说明。
- suggested_fix 仅在 degraded、blocked、failed 时填写；success 时为空字符串。

JSON 字段必须是：
{
  "trigger_status": "confirmed|requested_only|not_triggered|unclear",
  "skill_execution_status": "success|degraded|blocked|failed|not_tested",
  "task_completed": true,
  "used_skill": true,
  "core_requirement_met": true,
  "confidence": 0.0,
  "failure_category": "none|model_rate_limited|missing_env|script_failed|network_failed|permission_or_auth_required|no_real_result|wrong_tool|low_quality_response|unknown",
  "evidence": ["引用或概括关键证据，最多5条"],
  "reason": "一句话说明判断原因",
  "suggested_fix": "如果失败或降级，给出一句修复建议；成功则为空字符串"
}
""".strip()


def build_codex_semantic_eval_prompt(data: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            build_semantic_eval_instruction(),
            "待评测数据 JSON:",
            json.dumps(data, ensure_ascii=False),
        ]
    )


def parse_semantic_eval_response(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    candidates = [stripped]
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def run_codex_semantic_prompt(prompt: str) -> str:
    codex_bin = os.environ.get("SKILL_AUTO_CODEX_BIN") or shutil.which("codex")
    if not codex_bin:
        return ""
    timeout = int(os.environ.get("SKILL_AUTO_SEMANTIC_EVAL_TIMEOUT", "180"))
    with tempfile.TemporaryDirectory(prefix="skill-auto-semantic-") as tmp_dir:
        output_path = Path(tmp_dir) / "semantic.json"
        try:
            proc = subprocess.run(
                [
                    codex_bin,
                    "-a",
                    "never",
                    "exec",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--sandbox",
                    "read-only",
                    "--output-last-message",
                    str(output_path),
                    "-",
                ],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        text = ""
        if output_path.exists():
            text = output_path.read_text(encoding="utf-8", errors="ignore")
        if not text:
            text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0 and not text.strip():
        return ""
    return text
