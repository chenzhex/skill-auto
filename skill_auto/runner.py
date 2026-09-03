from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request
import uuid

from .downloader import skillhub_download_url, source_type
from .semantic_evaluator import apply_semantic_result, evaluate_demo_observation

DEFAULT_DEMO_CASE_MAX_CHARS = 300
MIN_DEMO_CASE_CHARS = 20


def generate_test_cases(
    name: str,
    skill_type: str | None,
    mode: str = "demo",
    *,
    source_path: Path | None = None,
    base_url: str | None = None,
    demo_case_generator: str = "codex",
) -> list[dict[str, Any]]:
    if mode == "install":
        return []
    if mode == "smoke":
        prompt = (
            f"请使用 {name} Skill 完成最小自检任务。"
            '只返回一行 JSON：{"used_skill":true,"result":"ok|failed","reason":"20字以内"}'
        )
        return [{"id": "smoke", "prompt": prompt}]
    if demo_case_generator in {"codex", "llm"}:
        generated = generate_codex_demo_test_case(name, skill_type, source_path)
        if generated:
            return [generated]
    return [static_demo_test_case(name, skill_type)]


def generate_test_cases_batch(
    items: list[dict[str, Any]],
    *,
    demo_case_generator: str = "codex",
    batch_size: int = 5,
    base_url: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if demo_case_generator == "static":
        return {
            str(item["key"]): [static_demo_test_case(str(item["name"]), item.get("skill_type"))]
            for item in items
        }
    if demo_case_generator not in {"codex", "llm"}:
        return {}
    results: dict[str, list[dict[str, Any]]] = {}
    pending = [item for item in items if read_skill_brief(item.get("source_path"), max_chars=1)]
    for chunk in chunks(pending, max(batch_size, 1)):
        generated = generate_codex_demo_test_cases_chunk(chunk)
        results.update(generated)
    for item in items:
        key = str(item["key"])
        if key in results:
            continue
        generated = generate_codex_demo_test_case(
            str(item["name"]),
            item.get("skill_type"),
            item.get("source_path"),
        )
        if generated and valid_demo_case(str(item["name"]), generated):
            generated["source"] = "codex_single_repair"
            results[key] = [generated]
            continue
        results[key] = [static_demo_test_case(str(item["name"]), item.get("skill_type"))]
    return results


def chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def static_demo_test_case(name: str, skill_type: str | None) -> dict[str, Any]:
    prompts = {
        "data": f"请使用 {name} Skill，用一组示例经营数据生成包含 KPI、趋势、异常和建议的可视化分析报告。",
        "research": f"请使用 {name} Skill，围绕 Agentic RAG 的最新进展做一份结构化研究摘要，包含来源线索和结论边界。",
        "design": f"请使用 {name} Skill，为一个 AI 产品发布会生成一份视觉方案或海报概念，并说明设计理由。",
        "presentation": f"请使用 {name} Skill，围绕季度经营复盘生成一份 6 页以内的演示文稿大纲。",
        "chat": f"请使用 {name} Skill，把一段生硬回复改写得更自然、清晰且不失边界感。",
    }
    prompt = prompts.get(skill_type or "work", f"请使用 {name} Skill 完成一个核心示例任务，并输出可检查的结果。")
    return {"id": "core-flow", "prompt": prompt, "source": "static_type_template"}


def normalize_model_demo_case(raw: dict[str, Any]) -> dict[str, Any] | None:
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    return {"id": str(raw.get("id") or "core-flow"), "prompt": prompt.strip()}


def generate_codex_demo_test_case(
    name: str,
    skill_type: str | None,
    source_path: Path | None,
) -> dict[str, Any] | None:
    skill_brief = read_skill_brief(source_path)
    if not skill_brief:
        return None
    prompt = build_demo_case_generation_prompt(name, skill_type, skill_brief)
    text = run_codex_case_prompt(prompt)
    if not text:
        return None
    case = parse_demo_case_response(text)
    if not case or not valid_demo_case(name, case):
        return None
    case.setdefault("id", "core-flow")
    case["source"] = "codex_generated"
    case["generator_model"] = "codex"
    return case


def generate_codex_demo_test_cases_chunk(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if not items:
        return {}
    prompt = build_batch_demo_case_generation_prompt(items)
    text = run_codex_case_prompt(prompt)
    if not text:
        return {}
    cases = parse_batch_demo_case_response(text)
    results: dict[str, list[dict[str, Any]]] = {}
    by_name = {str(item["name"]): item for item in items}
    for case in cases:
        name = str(case.get("name") or "")
        item = by_name.get(name)
        if item is None:
            continue
        normalized = normalize_model_demo_case(case)
        if normalized is None:
            continue
        normalized["source"] = "codex_batch_generated"
        normalized["generator_model"] = "codex"
        if valid_demo_case(name, normalized):
            results[str(item["key"])] = [normalized]
    return results


def run_codex_case_prompt(prompt: str) -> str:
    codex_bin = os.environ.get("SKILL_AUTO_CODEX_BIN") or shutil.which("codex")
    if not codex_bin:
        return ""
    timeout = int(os.environ.get("SKILL_AUTO_CASE_TIMEOUT", "90"))
    with tempfile.TemporaryDirectory(prefix="skill-auto-case-") as tmp_dir:
        output_path = Path(tmp_dir) / "case.json"
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


def read_skill_brief(source_path: Path | None, max_chars: int = 6000) -> str:
    if source_path is None:
        return ""
    skill_md = find_skill_md_for_case_generation(source_path)
    if skill_md is None:
        return ""
    text = skill_md.read_text(encoding="utf-8", errors="ignore")
    return text[:max_chars]


def find_skill_md_for_case_generation(source_path: Path) -> Path | None:
    for candidate in ("SKILL.md", "skill.md"):
        direct = source_path / candidate
        if direct.exists():
            return direct
    matches = list(source_path.rglob("SKILL.md")) + list(source_path.rglob("skill.md"))
    return matches[0] if matches else None


def build_demo_case_generation_prompt(name: str, skill_type: str | None, skill_brief: str) -> str:
    max_chars = demo_case_max_chars()
    return f"""
你是 LazyMind Skill 自动化评测的 demo case 设计器。请根据 Skill 说明生成 1 条中文测试请求，用于验证该 Skill 在 LazyMind 中能否被触发并完成核心流程。

生成目标：
- case 必须像真实用户会输入的任务，而不是测试人员写的自检命令。
- case 必须覆盖该 Skill 最核心、最有代表性、最容易验收的能力。
- case 应有明确交付结果，评测器能根据 LazyMind 最终回复判断是否完成。
- case 应适合做 demo 展示：场景具体、结果有业务价值或传播价值，但不要刻意复杂。

Skill 类型适配：
- 文本型 Skill：提供待处理文本或明确主题，要求输出改写、总结、诊断、建议、推荐等可直接验收的结果。
- 数据/搜索型 Skill：给出明确查询目标，要求返回真实数据、来源线索、结构化结论或推荐理由。
- 代码/脚本型 Skill：给出短代码片段、明确输入或公开稳定资源，要求运行或遵循 Skill 核心流程并返回结果。
- 产物型 Skill：如果 Skill 核心能力是生成 HTML、图片、PPT、Excel、Word、PDF、报告等文件，应在 case 中明确要求生成对应产物，并要求返回文件名、保存路径或附件链接。
- 方法论/工作流型 Skill：给出具体问题场景，要求按照 Skill 工作流完成分析、规划、复盘或改进建议。

约束：
- 必须在 prompt 中明确写出“请使用 {name} Skill”。
- prompt 不超过 {max_chars} 个中文字符；在完整贴合 Skill 的前提下越精简越好。
- 用例必须自包含；如果需要文本、数据、代码片段，请在 prompt 中直接提供少量示例素材。
- 不要依赖测试环境里可能不存在的本地文件、当前项目文件、localhost/127.0.0.1 页面、私有内网地址或用户账号状态。
- 如果需要网页，优先使用公开稳定的网址；如果需要代码审查，直接提供短代码片段，不要引用本地路径。
- 不要要求用户提供 API key、token、登录账号或付费操作；测试执行阶段如有 manifest env，会自动注入到 case 前面。
- 不要生成泛泛的“完成自检”“完成核心示例任务”“随便测试一下”等低信息量请求。
- 不要让模型解释 Skill 是什么；case 应直接要求完成一个具体任务。

输出要求：
- 只返回 JSON object，不要 Markdown，不要解释。
- JSON 格式必须是：{{"id":"core-flow","prompt":"..."}}
- prompt 必须是单个字符串，不能包含未闭合引号、Markdown 表格或多余 JSON 字段。

待生成 Skill:
name: {name}
detected_skill_type: {skill_type or "unknown"}

Skill 说明:
{skill_brief}
""".strip()


def build_batch_demo_case_generation_prompt(items: list[dict[str, Any]]) -> str:
    max_chars = int(os.environ.get("SKILL_AUTO_BATCH_SKILL_BRIEF_CHARS", "2500"))
    case_max_chars = demo_case_max_chars()
    blocks = []
    for item in items:
        name = str(item["name"])
        skill_type = item.get("skill_type") or "unknown"
        source_path = item.get("source_path")
        skill_brief = read_skill_brief(source_path, max_chars=max_chars)
        blocks.append(
            f"""
Skill:
name: {name}
detected_skill_type: {skill_type}
SKILL.md 摘要:
{skill_brief}
""".strip()
        )
    joined = "\n\n---\n\n".join(blocks)
    return f"""
你是 LazyMind Skill 自动化评测的 demo case 设计器。请为下面每个 Skill 各生成 1 条中文测试请求，用于验证对应 Skill 在 LazyMind 中能否被触发并完成核心流程。

生成目标：
- 每条 case 必须像真实用户会输入的任务，而不是测试人员写的自检命令。
- 每条 case 必须覆盖对应 Skill 最核心、最有代表性、最容易验收的能力。
- 每条 case 应有明确交付结果，评测器能根据 LazyMind 最终回复判断是否完成。
- 每条 case 应适合做 demo 展示：场景具体、结果有业务价值或传播价值，但不要刻意复杂。

Skill 类型适配：
- 文本型 Skill：提供待处理文本或明确主题，要求输出改写、总结、诊断、建议、推荐等可直接验收的结果。
- 数据/搜索型 Skill：给出明确查询目标，要求返回真实数据、来源线索、结构化结论或推荐理由。
- 代码/脚本型 Skill：给出短代码片段、明确输入或公开稳定资源，要求运行或遵循 Skill 核心流程并返回结果。
- 产物型 Skill：如果 Skill 核心能力是生成 HTML、图片、PPT、Excel、Word、PDF、报告等文件，应在 case 中明确要求生成对应产物，并要求返回文件名、保存路径或附件链接。
- 方法论/工作流型 Skill：给出具体问题场景，要求按照 Skill 工作流完成分析、规划、复盘或改进建议。

约束：
- 每条 prompt 必须明确写出“请使用 <name> Skill”，其中 <name> 必须使用对应输入 Skill 的 name。
- 每条 prompt 不超过 {case_max_chars} 个中文字符；在完整贴合 Skill 的前提下越精简越好。
- 用例必须自包含；如果需要文本、数据、代码片段，请在 prompt 中直接提供少量示例素材。
- 不要依赖测试环境里可能不存在的本地文件、当前项目文件、localhost/127.0.0.1 页面、私有内网地址或用户账号状态。
- 如果需要网页，优先使用公开稳定的网址；如果需要代码审查，直接提供短代码片段，不要引用本地路径。
- 不要要求用户提供 API key、token、登录账号或付费操作；测试执行阶段如有 manifest env，会自动注入到 case 前面。
- 不要生成泛泛的“完成自检”“完成核心示例任务”“随便测试一下”等低信息量请求。
- 不要让模型解释 Skill 是什么；case 应直接要求完成一个具体任务。
- 必须为每个输入 Skill 返回且只返回 1 条 case；name 必须与输入 name 完全一致。
- 不同 Skill 的 case 应体现各自能力差异，避免批量生成相同或高度相似的任务。

输出要求：
- 只返回 JSON object，不要 Markdown，不要解释。
- JSON 格式必须是：{{"cases":[{{"name":"skill-name","id":"core-flow","prompt":"..."}}]}}
- cases 数量必须等于输入 Skill 数量。
- prompt 必须是单个字符串，不能包含未闭合引号、Markdown 表格或多余 JSON 字段。

待生成 Skills:
{joined}
""".strip()


def parse_demo_case_response(text: str) -> dict[str, Any] | None:
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
        if not isinstance(parsed, dict):
            continue
        prompt = parsed.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return {"id": str(parsed.get("id") or "core-flow"), "prompt": prompt.strip()}
    return None


def parse_batch_demo_case_response(text: str) -> list[dict[str, Any]]:
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
        if not isinstance(parsed, dict):
            continue
        cases = parsed.get("cases")
        if isinstance(cases, list):
            return [case for case in cases if isinstance(case, dict)]
    return []


def valid_demo_case(name: str, case: dict[str, Any]) -> bool:
    prompt = str(case.get("prompt") or "").strip()
    if len(prompt) < MIN_DEMO_CASE_CHARS:
        return False
    if len(prompt) > demo_case_max_chars():
        return False
    lowered = normalize_case_text(prompt)
    normalized_name = normalize_case_text(name)
    generic_markers = ("完成一个核心示例任务", "完成自检", "最小自检", "随便", "任意任务")
    if any(marker in prompt for marker in generic_markers):
        return False
    if has_local_dependency_marker(prompt):
        return False
    return normalized_name in lowered


def normalize_case_text(value: str) -> str:
    return re.sub(r"[\s_\-+]+", "", value.lower())


def has_local_dependency_marker(prompt: str) -> bool:
    lowered = prompt.lower()
    local_markers = (
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "当前项目",
        "本地项目",
        "当前仓库",
        "本地仓库",
        "本机",
        "/users/",
        "/home/",
        "/private/",
        "/tmp/",
        "c:\\",
    )
    if any(marker in lowered for marker in local_markers):
        return True
    if re.search(r"\b(?:src|app|lib|pages|components|tests?)/[A-Za-z0-9_.\-/]+", prompt):
        return True
    if re.search(r"\b[A-Za-z0-9_.-]+\.(?:ts|tsx|js|jsx|py|go|java|rs|md|yaml|yml)\b", prompt):
        file_context_markers = ("审查", "修改", "修复", "读取", "打开", "检查", "当前", "项目", "仓库", "文件")
        if any(marker in prompt for marker in file_context_markers):
            return True
    return False


def demo_case_max_chars() -> int:
    raw = os.environ.get("SKILL_AUTO_CASE_MAX_CHARS")
    if raw is None:
        return DEFAULT_DEMO_CASE_MAX_CHARS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_DEMO_CASE_MAX_CHARS
    return max(value, MIN_DEMO_CASE_CHARS)


def run_skill(
    *,
    runner: str,
    base_url: str | None,
    source_path: Path | None,
    link: str,
    name: str,
    test_cases: list[dict[str, Any]],
    log_path: Path,
    attempts: int = 2,
    retry_delay: float = 30.0,
    retry_backoff: float = 3.0,
    rate_limit_attempts: int = 3,
    rate_limit_delay: float = 120.0,
    rate_limit_backoff: float = 2.0,
    rate_limit_pass_through: bool = True,
    run_chat: bool = True,
    test_mode: str = "demo",
    max_response_chars: int = 2000,
    secret_values: list[str] | None = None,
) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if runner == "offline":
        log_path.write_text("offline runner: static preflight only\n", encoding="utf-8")
        return {
            "install_status": "skipped",
            "run_status": "skipped",
            "skill_trigger_status": "not_tested",
            "skill_execution_status": "not_tested",
        }
    if runner == "api":
        return run_api(
            base_url,
            name,
            link,
            test_cases,
            log_path,
            attempts,
            retry_delay,
            retry_backoff,
            rate_limit_attempts,
            rate_limit_delay,
            rate_limit_backoff,
            rate_limit_pass_through,
            run_chat,
            test_mode,
            max_response_chars,
            secret_values or [],
        )
    if runner == "ui":
        return run_ui_stub(base_url, name, test_cases, log_path)
    raise ValueError(f"unsupported runner: {runner}")


def run_api(
    base_url: str | None,
    name: str,
    link: str,
    test_cases: list[dict[str, Any]],
    log_path: Path,
    attempts: int,
    retry_delay: float,
    retry_backoff: float,
    rate_limit_attempts: int,
    rate_limit_delay: float,
    rate_limit_backoff: float,
    rate_limit_pass_through: bool,
    run_chat: bool,
    test_mode: str,
    max_response_chars: int,
    secret_values: list[str],
) -> dict[str, Any]:
    if not base_url:
        log_path.write_text("api runner requires --base-url\n", encoding="utf-8")
        return {
            "install_status": "skipped",
            "run_status": "skipped",
            "failure_category": "runner_not_configured",
            "failure_user_message": "API 测试需要提供 LazyMind 服务地址。",
            "failure_technical_reason": "missing --base-url",
        }
    import_url = link
    if source_type(link) == "skillhub":
        import_url = skillhub_download_url(link)
    lines = [f"api runner", f"base_url={base_url}", f"name={name}", f"link={link}", f"import_url={import_url}"]
    skills_url = base_url.rstrip("/") + "/api/core/skills"
    status, body = http_json("GET", skills_url)
    lines.append(f"GET /api/core/skills status={status} body={body[:1000]}")
    if status == 401:
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {
            "install_status": "skipped",
            "run_status": "skipped",
            "failure_category": "lazymind_unauthorized",
            "failure_user_message": "LazyMind API 返回 Unauthorized，自动化测试需要登录态、Authorization token 或测试网关放行。",
            "failure_technical_reason": "GET /api/core/skills returned HTTP 401",
            "suggested_fix": "为 skill-auto 配置可用的 LazyMind 登录态，或提供测试环境的 API token/cookie。",
        }
    if status < 200 or status >= 300:
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {
            "install_status": "fail",
            "run_status": "skipped",
            "failure_category": "lazymind_api_unavailable",
            "failure_user_message": f"LazyMind API 不可用或返回异常状态：HTTP {status}。",
            "failure_technical_reason": f"GET /api/core/skills returned HTTP {status}: {body[:500]}",
        }
    payload = {
        "category": "skill-auto",
        "name": name,
        "source": {"type": "url", "url": import_url},
        "is_enabled": True,
    }
    status, body = http_json("POST", skills_url, payload)
    lines.append(f"POST /api/core/skills status={status} body={body[:2000]}")
    if status < 200 or status >= 300:
        if status == 409 and "skill already exists" in body:
            lines.append("install_result=already_exists; continuing to chat test")
        else:
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return {
                "install_status": "fail",
                "run_status": "skipped",
                "failure_category": "install_failed",
                "failure_user_message": "Skill 导入 LazyMind 失败。",
                "failure_technical_reason": f"POST /api/core/skills returned HTTP {status}: {body[:1000]}",
            }
    else:
        lines.append("install_result=created")
    if not run_chat:
        lines.append("chat_result=skipped; run_chat=false")
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {
            "install_status": "pass",
            "run_status": "skipped",
            "skill_trigger_status": "not_tested",
            "skill_triggered": False,
            "skill_execution_status": "not_tested",
            "skill_execution_evidence": [],
            "total_attempts": 0,
            "chat_observations": [],
            "test_mode": test_mode,
            "run_chat": False,
        }
    chat_results = []
    for case in test_cases:
        case_id = str(case.get("id") or "core-flow")
        prompt = str(case.get("prompt") or f"请使用 {name} Skill 完成一个核心示例任务，并输出可检查的结果。")
        case_attempts = int(case.get("attempts") or attempts)
        if case_attempts < 1:
            case_attempts = 1
        case_rate_limit_attempts = int(case.get("rate_limit_attempts") or rate_limit_attempts)
        if case_rate_limit_attempts < 0:
            case_rate_limit_attempts = 0
        attempt = 1
        regular_failures = 0
        rate_limit_failures = 0
        while True:
            chat_result = run_chat_case(base_url, name, case_id, prompt, attempt, max_response_chars)
            if test_mode == "demo":
                semantic_result = evaluate_demo_observation(
                    base_url=base_url,
                    skill_name=name,
                    skill_link=link,
                    case=case,
                    observation=chat_result,
                )
                apply_semantic_result(chat_result, semantic_result)
            chat_result = redact_observation_secrets(chat_result, secret_values)
            chat_results.append(chat_result)
            lines.extend(format_chat_log(chat_result))
            if chat_result["status"] == "pass":
                break
            if is_rate_limited_observation(chat_result) and rate_limit_failures < case_rate_limit_attempts:
                wait_seconds = rate_limit_delay * (rate_limit_backoff ** rate_limit_failures)
                rate_limit_failures += 1
                lines.append(
                    f"rate_limit_retry_wait case={case_id} next_attempt={attempt + 1} "
                    f"seconds={wait_seconds:.1f} reason={chat_result.get('failure_category')}"
                )
                log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                attempt += 1
                continue
            regular_failures += 1
            if regular_failures < case_attempts and retry_delay > 0:
                wait_seconds = retry_delay * (retry_backoff ** (regular_failures - 1))
                lines.append(
                    f"retry_wait case={case_id} next_attempt={attempt + 1} "
                    f"seconds={wait_seconds:.1f} reason={chat_result.get('failure_category')}"
                )
                log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                attempt += 1
                continue
            break
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    failed_cases = []
    for case in test_cases:
        case_id = str(case.get("id") or "core-flow")
        case_results = [item for item in chat_results if item["case_id"] == case_id]
        if not any(item["status"] == "pass" for item in case_results):
            failed_cases.append(case_results[-1])
    total_attempts = len(chat_results)
    flaky = any(item["attempt"] > 1 and item["status"] == "pass" for item in chat_results)
    trigger_status, trigger_evidence = aggregate_trigger_status(chat_results)
    execution_status, execution_evidence = aggregate_execution_status(chat_results)
    semantic_summary = summarize_semantic_results(chat_results, test_mode)
    output_artifacts = aggregate_output_artifacts(chat_results)
    if failed_cases:
        if rate_limit_pass_through and all(is_rate_limited_observation(item) for item in failed_cases):
            return {
                "install_status": "pass",
                "run_status": "pass",
                "chat_observations": chat_results,
                "output_artifacts": output_artifacts,
                **semantic_summary,
                "test_mode": test_mode,
                "run_chat": run_chat,
                "total_attempts": total_attempts,
                "flaky": flaky,
                "skill_trigger_status": trigger_status if trigger_status == "confirmed" else "not_tested",
                "skill_triggered": trigger_status == "confirmed",
                "skill_trigger_evidence": trigger_evidence,
                "skill_execution_status": "not_tested",
                "skill_execution_evidence": ["model_rate_limited_passed"],
                "failure_category": "model_rate_limited_passed",
                "failure_user_message": "模型服务多次限流，已按限流透传策略放行，不将该 Skill 记为执行失败。",
                "failure_technical_reason": "all failed cases were model_rate_limited after rate-limit retries",
                "suggested_fix": "稍后单独重跑该 Skill，或继续增大 --rate-limit-delay / --rate-limit-attempts。",
            }
        first = failed_cases[0]
        detail = classify_execution_failure(
            execution_status,
            execution_evidence,
            str(first.get("response_excerpt") or ""),
            json.dumps(chat_results, ensure_ascii=False),
        )
        return {
            "install_status": "pass",
            "run_status": "fail",
            "chat_observations": chat_results,
            "output_artifacts": output_artifacts,
            **semantic_summary,
            "test_mode": test_mode,
            "run_chat": run_chat,
            "total_attempts": total_attempts,
            "flaky": flaky,
            "skill_trigger_status": trigger_status,
            "skill_triggered": trigger_status == "confirmed",
            "skill_trigger_evidence": trigger_evidence,
            "skill_execution_status": execution_status,
            "skill_execution_evidence": execution_evidence,
            "failure_category": first.get("failure_category") or detail["failure_category"],
            "failure_user_message": first.get("failure_user_message") or detail["failure_user_message"],
            "failure_technical_reason": first.get("failure_technical_reason") or detail["failure_technical_reason"],
            "suggested_fix": first.get("suggested_fix") or detail["suggested_fix"],
        }
    if trigger_status != "confirmed":
        return {
            "install_status": "pass",
            "run_status": "pass",
            "chat_observations": chat_results,
            "output_artifacts": output_artifacts,
            **semantic_summary,
            "test_mode": test_mode,
            "run_chat": run_chat,
            "total_attempts": total_attempts,
            "flaky": flaky,
            "skill_trigger_status": trigger_status,
            "skill_triggered": False,
            "skill_trigger_evidence": trigger_evidence,
            "skill_execution_status": execution_status,
            "skill_execution_evidence": execution_evidence,
            "failure_category": "skill_not_triggered",
            "failure_user_message": "Chat 已返回可用结果，但未观察到对应 Skill 被明确触发。",
            "failure_technical_reason": f"trigger status={trigger_status}; evidence={trigger_evidence}",
            "suggested_fix": "让测试 prompt 明确要求使用该 Skill，并检查 LazyMind 的 skill binding 是否真正进入模型上下文和工具事件。",
        }
    if execution_status != "success":
        detail = classify_execution_failure(
            execution_status,
            execution_evidence,
            "\n".join(str(item.get("response_excerpt") or "") for item in chat_results),
            json.dumps(chat_results, ensure_ascii=False),
        )
        observed_detail = preferred_observation_failure(chat_results)
        return {
            "install_status": "pass",
            "run_status": "pass",
            "chat_observations": chat_results,
            "output_artifacts": output_artifacts,
            **semantic_summary,
            "test_mode": test_mode,
            "run_chat": run_chat,
            "total_attempts": total_attempts,
            "flaky": flaky,
            "skill_trigger_status": trigger_status,
            "skill_triggered": True,
            "skill_trigger_evidence": trigger_evidence,
            "skill_execution_status": execution_status,
            "skill_execution_evidence": execution_evidence,
            "failure_category": observed_detail.get("failure_category") or detail["failure_category"],
            "failure_user_message": observed_detail.get("failure_user_message") or detail["failure_user_message"],
            "failure_technical_reason": observed_detail.get("failure_technical_reason") or detail["failure_technical_reason"],
            "suggested_fix": observed_detail.get("suggested_fix") or detail["suggested_fix"],
        }
    return {
        "install_status": "pass",
        "run_status": "pass",
        "chat_observations": chat_results,
        "output_artifacts": output_artifacts,
        **semantic_summary,
        "test_mode": test_mode,
        "run_chat": run_chat,
        "total_attempts": total_attempts,
        "flaky": flaky,
        "skill_trigger_status": trigger_status,
        "skill_triggered": trigger_status == "confirmed",
        "skill_trigger_evidence": trigger_evidence,
        "skill_execution_status": execution_status,
        "skill_execution_evidence": execution_evidence,
    }


def http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/json")
    if auth := os.environ.get("SKILL_AUTO_AUTHORIZATION"):
        request.add_header("Authorization", auth)
    if cookie := os.environ.get("SKILL_AUTO_COOKIE"):
        request.add_header("Cookie", cookie)
    if user_id := os.environ.get("SKILL_AUTO_USER_ID"):
        request.add_header("X-User-Id", user_id)
    if user_name := os.environ.get("SKILL_AUTO_USER_NAME"):
        request.add_header("X-User-Name", user_name)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - user-configured local URL.
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return 0, str(exc)


def run_chat_case(
    base_url: str,
    name: str,
    case_id: str,
    prompt: str,
    attempt: int,
    max_response_chars: int,
) -> dict[str, Any]:
    conversation_id = str(uuid.uuid4())
    payload = {
        "action": "CHAT_ACTION_NEXT",
        "conversation_id": conversation_id,
        "conversation": {"display_name": f"skill-auto {name} {case_id}", "search_config": {"dataset_list": []}},
        "models": ["LazyMind"],
        "stream": True,
        "input": [{"input_type": "text", "text": prompt}],
        "mode": "auto",
        "create_time": "2026-09-01T00:00:00.000Z",
        "explicit_resource_bindings": {"skill_names": [name]},
    }
    base = base_url.rstrip("/")
    timeout = int(os.environ.get("SKILL_AUTO_CHAT_TIMEOUT", "900"))
    status, body = http_stream_json("POST", base + "/api/core/conversations:chat", payload, timeout=timeout)
    completion = empty_conversation_completion()
    if 200 <= status < 300 or (status == 0 and is_timeout_response(body)):
        completion = wait_for_conversation_completion(base, conversation_id)
    history_status = completion["history_http_status"]
    history_body = completion["history_body"]
    answer = body
    history_answer = completion["answer"]
    if history_answer:
        answer = history_answer
    trigger_status, trigger_evidence = detect_skill_trigger(name, body, answer, history_body)
    execution_status, execution_evidence = detect_skill_execution(name, body, answer, history_body)
    output_artifacts = extract_output_artifacts(answer)
    observation = {
        "case_id": case_id,
        "attempt": attempt,
        "conversation_id": conversation_id,
        "prompt": prompt,
        "http_status": status,
        "history_http_status": history_status,
        "chat_status_http_status": completion["status_http_status"],
        "chat_is_generating": completion["is_generating"],
        "history_run_status": completion["run_status"],
        "history_run_terminal": completion["run_terminal"],
        "history_wait_timed_out": completion["wait_timed_out"],
        "status": "fail",
        "skill_trigger_status": trigger_status,
        "skill_triggered": trigger_status == "confirmed",
        "skill_trigger_evidence": trigger_evidence,
        "skill_execution_status": execution_status,
        "skill_execution_evidence": execution_evidence,
        "response_excerpt": retained_response(answer, max_response_chars),
        "output_artifacts": output_artifacts,
    }
    if status == 401:
        observation.update(
            {
                "skill_execution_status": "failed",
                "failure_category": "lazymind_unauthorized",
                "failure_user_message": "LazyMind chat API 返回 Unauthorized，自动化测试需要有效登录态。",
                "failure_technical_reason": "POST /api/core/conversations:chat returned HTTP 401",
                "suggested_fix": "重新登录并设置 SKILL_AUTO_AUTHORIZATION，或配置测试账号的 Cookie。",
            }
        )
        return observation
    if status == 0 and not answer.strip():
        if is_rate_limited_response(body):
            observation.update(
                {
                    "skill_execution_status": "blocked",
                    "skill_execution_evidence": ["model_rate_limited"],
                    "failure_category": "model_rate_limited",
                    "failure_user_message": "模型服务触发限流，本次未生成可用内容。",
                    "failure_technical_reason": body[:1000],
                    "suggested_fix": "降低批量测试频率，增加 --retry-delay 和 --between-skill-delay 后重试。",
                }
            )
            return observation
        observation.update(
            {
                "skill_execution_status": "failed",
                "failure_category": "timeout" if "timed out" in body.lower() or "timeout" in body.lower() else "chat_api_unreachable",
                "failure_user_message": (
                    "Skill 执行或模型响应超时，未在限定时间内生成可验收结果。"
                    if "timed out" in body.lower() or "timeout" in body.lower()
                    else "无法连接 LazyMind chat API，可能是服务未启动、端口不可达。"
                ),
                "failure_technical_reason": body[:1000],
                "suggested_fix": "确认 8090 服务可访问，或延长 runner 的 chat 超时时间。",
            }
        )
        return observation
    if status == 0 and answer.strip():
        status = 200
        observation["http_status"] = 200
        observation["recovered_from_stream_timeout"] = True
    if status < 200 or status >= 300:
        if is_rate_limited_response(body):
            observation.update(
                {
                    "skill_execution_status": "blocked",
                    "skill_execution_evidence": ["model_rate_limited"],
                    "failure_category": "model_rate_limited",
                    "failure_user_message": "模型服务触发限流，本次未生成可用内容。",
                    "failure_technical_reason": body[:1000],
                    "suggested_fix": "降低批量测试频率，增加 --retry-delay 和 --between-skill-delay 后重试。",
                }
            )
            return observation
        observation.update(
            {
                "skill_execution_status": "failed",
                "failure_category": "chat_api_failed",
                "failure_user_message": f"LazyMind chat API 返回异常状态：HTTP {status}。",
                "failure_technical_reason": body[:1000],
                "suggested_fix": "查看 LazyMind 后端日志，确认模型配置、工具执行权限和 Skill 绑定是否正常。",
            }
        )
        return observation
    if is_rate_limited_response(answer) or is_rate_limited_response(body) or is_rate_limited_response(history_body):
        observation.update(
            {
                "skill_execution_status": "blocked",
                "skill_execution_evidence": ["model_rate_limited"],
                "failure_category": "model_rate_limited",
                "failure_user_message": "模型服务触发限流，本次未生成可用内容。",
                "failure_technical_reason": (answer or body or history_body)[:1000],
                "suggested_fix": "降低批量测试频率，增加 --retry-delay 和 --between-skill-delay 后重试。",
            }
        )
        return observation
    if not answer.strip():
        observation.update(
            {
                "skill_execution_status": "failed",
                "failure_category": "no_structured_output",
                "failure_user_message": "LazyMind chat API 成功返回，但没有可机器验收的输出内容。",
                "failure_technical_reason": "empty SSE/body",
                "suggested_fix": "检查流式响应解析、后端是否提前结束，或为该 Skill 补充专用 smoke prompt。",
            }
        )
        return observation
    if is_unhelpful_response(answer):
        observation.update(
            {
                "skill_execution_status": "failed",
                "failure_category": "low_quality_response",
                "failure_user_message": "Skill 已触发 chat，但回答是无能力/拒答模板，未完成核心试用任务。",
                "failure_technical_reason": answer[:1000],
                "suggested_fix": "检查 Skill 是否被正确注入到模型上下文，或补充该 Skill 的核心流程测试 prompt 与工具调用权限。",
            }
        )
        return observation
    observation["status"] = "pass"
    observation["failure_category"] = None
    observation["failure_user_message"] = None
    observation["failure_technical_reason"] = None
    return observation


def is_rate_limited_observation(result: dict[str, Any]) -> bool:
    if result.get("failure_category") == "model_rate_limited":
        return True
    evidence = " ".join(str(item) for item in result.get("skill_execution_evidence") or [])
    text = "\n".join(
        [
            str(result.get("response_excerpt") or ""),
            str(result.get("failure_technical_reason") or ""),
            evidence,
        ]
    )
    return is_rate_limited_response(text)


def retained_response(answer: str, max_response_chars: int) -> str:
    if max_response_chars <= 0:
        return answer
    return answer[:max_response_chars]


def aggregate_execution_status(results: list[dict[str, Any]]) -> tuple[str, list[str]]:
    evidence: list[str] = []
    statuses = []
    for item in results:
        statuses.append(str(item.get("skill_execution_status") or "not_tested"))
        evidence.extend(str(part) for part in item.get("skill_execution_evidence") or [])
    if "success" in statuses:
        return "success", evidence[:10]
    if "degraded" in statuses:
        return "degraded", evidence[:10]
    if "blocked" in statuses:
        return "blocked", evidence[:10]
    if "failed" in statuses:
        return "failed", evidence[:10]
    return "not_tested", evidence[:10]


def summarize_semantic_results(results: list[dict[str, Any]], test_mode: str) -> dict[str, str | None]:
    if test_mode != "demo":
        return {"semantic_eval_status": "not_tested", "semantic_eval_error": None}
    semantic_items = [
        item.get("semantic_evaluation")
        for item in results
        if isinstance(item.get("semantic_evaluation"), dict)
    ]
    if not semantic_items:
        return {"semantic_eval_status": "not_tested", "semantic_eval_error": None}
    errors = [
        str(item.get("semantic_eval_error"))
        for item in semantic_items
        if item.get("semantic_eval_status") != "succeeded" and item.get("semantic_eval_error")
    ]
    if errors and len(errors) == len(semantic_items):
        return {"semantic_eval_status": "failed", "semantic_eval_error": "; ".join(errors)[:1000]}
    if errors:
        return {"semantic_eval_status": "partial", "semantic_eval_error": "; ".join(errors)[:1000]}
    return {"semantic_eval_status": "succeeded", "semantic_eval_error": None}


def preferred_observation_failure(results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in reversed(results):
        category = item.get("failure_category")
        if category and category not in {"missing_env", "blocked_by_env"}:
            return {
                "failure_category": category,
                "failure_user_message": item.get("failure_user_message"),
                "failure_technical_reason": item.get("failure_technical_reason"),
                "suggested_fix": item.get("suggested_fix"),
            }
    for item in reversed(results):
        category = item.get("failure_category")
        if category:
            return {
                "failure_category": category,
                "failure_user_message": item.get("failure_user_message"),
                "failure_technical_reason": item.get("failure_technical_reason"),
                "suggested_fix": item.get("suggested_fix"),
            }
    return {}


def classify_execution_failure(
    status: str,
    evidence: list[str],
    answer: str,
    history_body: str,
    existing_category: str | None = None,
) -> dict[str, str]:
    if existing_category == "missing_api_key":
        return failure_detail(
            "blocked_by_env",
            "Skill 需要环境变量或 API key，当前环境未配置，执行被阻塞。",
            "preflight detected missing env keys",
            "在 manifest 对应 Skill 的 env 字段中补充 API key 后重试。",
        )
    text = "\n".join([answer, history_body, "\n".join(evidence)])
    compact = text.lower()
    if is_rate_limited_response(text):
        return failure_detail(
            "model_rate_limited",
            "模型服务触发限流，本次未生成可用内容。",
            "model service returned rate-limit markers",
            "降低批量测试频率，增加 --retry-delay 和 --between-skill-delay 后重试。",
        )
    if status in {"failed", "blocked"} and present_markers(compact, ("timeout", "timed out", "stream exceeded")):
        return failure_detail(
            "timeout",
            "Skill 执行或模型响应超时，未在限定时间内生成可验收结果。",
            "chat attempt timed out",
            "延长 SKILL_AUTO_CHAT_TIMEOUT，或排查 LazyMind 后端/模型服务耗时。",
        )
    if present_markers(
        compact,
        (
            "需要授权",
            "需要 token",
            "没有 token",
            "permission denied",
            "unauthorized",
            "forbidden",
        ),
    ):
        return failure_detail(
            "permission_or_auth_required",
            "Skill 已触发，但缺少授权、Token 或权限，核心能力被阻塞。",
            "auth or permission marker detected",
            "补齐授权、Token、Cookie 或对应账号权限后重试。",
        )
    if present_markers(compact, ("missing env", "api key", "apikey", "密钥", "环境变量")):
        return failure_detail(
            "blocked_by_env",
            "Skill 需要环境变量或 API key，当前环境未配置，执行被阻塞。",
            "env/API key marker detected",
            "在 manifest 对应 Skill 的 env 字段中补充 API key 后重试。",
        )
    if present_markers(
        compact,
        (
            "未能列出文件夹",
            "未能读取",
            "no such file",
            "not found",
            "remote://",
            "path_exists",
        ),
    ):
        return failure_detail(
            "path_access_failed",
            "Skill 已触发，但执行过程中访问 Skill 文件、远程路径或参考资料失败。",
            "path/file access marker detected",
            "检查 runtime skill name、Skill 文件解包路径、远程文件访问和 LazyMind 文件工具权限。",
        )
    if present_markers(
        compact,
        (
            "预定义脚本未能运行完成",
            "未能运行完成",
            "脚本执行失败",
            "命令执行失败",
            "exit status",
            "non-zero exit",
        ),
    ):
        return failure_detail(
            "script_failed",
            "Skill 已触发，但预定义脚本或命令执行失败。",
            "script failure marker detected",
            "检查脚本路径、解释器、执行权限、系统依赖、sandbox 和超时配置。",
        )
    if status == "degraded":
        return failure_detail(
            "degraded_fallback",
            "Skill 已触发，但核心脚本/工具失败，任务由降级流程完成。",
            f"execution status=degraded; evidence={evidence}",
            "检查预定义脚本路径、运行权限、依赖安装和 LazyMind 对 Skill 脚本的执行兼容性。",
        )
    if has_task_result(answer):
        return failure_detail(
            "not_tool_like_skill",
            "Skill 已触发并产生了文字结果，但不像工具型 Skill 那样产生可机器验收的执行证据。",
            "answer exists without tool/script success marker",
            "为方法论/指导型 Skill 配置专用验收规则，或在测试用例中定义明确可检查输出。",
        )
    return failure_detail(
        "no_structured_output",
        "Skill 已触发，但没有产出可机器验收的结构化结果。",
        f"execution status={status}; evidence={evidence}",
        "补充该 Skill 的专用 smoke prompt 或增加输出格式/产物识别规则。",
    )


def failure_detail(category: str, user_message: str, technical_reason: str, suggested_fix: str) -> dict[str, str]:
    return {
        "failure_category": category,
        "failure_user_message": user_message,
        "failure_technical_reason": technical_reason,
        "suggested_fix": suggested_fix,
    }


def execution_user_message(status: str) -> str:
    messages = {
        "degraded": "Skill 已触发，但核心脚本/工具失败，任务由降级流程完成。",
        "blocked": "Skill 已触发，但因认证、依赖、权限或工具不可用导致核心能力阻塞。",
        "failed": "Skill 已触发，但核心执行失败，未完成可验收结果。",
        "not_tested": "Skill 未进入可执行测试阶段。",
    }
    return messages.get(status, f"Skill 执行状态异常：{status}。")


def execution_suggested_fix(status: str) -> str:
    fixes = {
        "degraded": "检查预定义脚本路径、运行权限、依赖安装和 LazyMind 对 Skill 脚本的执行兼容性。",
        "blocked": "补齐 API key、授权 token、MCP 工具或系统依赖后重试。",
        "failed": "查看 chat 日志和 LazyMind 后端日志，定位脚本、工具或模型调用失败原因。",
        "not_tested": "确认安装和触发链路后重新运行 API/UI 测试。",
    }
    return fixes.get(status, "查看日志并补充更精确的核心流程测试用例。")


def aggregate_trigger_status(results: list[dict[str, Any]]) -> tuple[str, list[str]]:
    evidence: list[str] = []
    for item in results:
        evidence.extend(str(part) for part in item.get("skill_trigger_evidence") or [])
    if any(item.get("skill_trigger_status") == "confirmed" for item in results):
        return "confirmed", evidence[:10]
    if any(item.get("skill_trigger_status") == "requested_only" for item in results):
        return "requested_only", evidence[:10]
    return "not_tested", evidence[:10]


def detect_skill_trigger(name: str, stream_body: str, answer: str, history_body: str) -> tuple[str, list[str]]:
    haystack = "\n".join([stream_body, answer, history_body])
    normalized = name.strip()
    candidates = {
        normalized,
        normalized.replace("_", "-"),
        normalized.replace("-", "_"),
    }
    evidence: list[str] = []
    for candidate in candidates:
        markers = (
            f"external/{candidate}",
            f"**external/{candidate}**",
            f"打开 **external/{candidate}**",
            f"加载 **external/{candidate}**",
            f"skills/external/{candidate}",
        )
        for marker in markers:
            if marker and marker in haystack:
                evidence.append(marker)
        category_pattern = re_skill_path_pattern(candidate)
        for match in category_pattern.findall(haystack):
            evidence.append(match)
    if evidence:
        return "confirmed", sorted(set(evidence))[:10]
    return "requested_only", [f"explicit_resource_bindings.skill_names={normalized}"]


def re_skill_path_pattern(candidate: str):
    import re

    escaped = re.escape(candidate)
    return re.compile(rf"(?:打开|加载|读取)?\s*\*\*[A-Za-z0-9_-]+/{escaped}\*\*")


def detect_skill_execution(name: str, stream_body: str, answer: str, history_body: str) -> tuple[str, list[str]]:
    haystack = "\n".join([stream_body, answer, history_body])
    compact = haystack.lower()
    normalized = name.strip().lower()
    evidence: list[str] = []
    blocked_markers = (
        "需要授权",
        "需要先完成授权",
        "需要 token",
        "需要提供 token",
        "没有 token",
        "permission denied",
        "没有办法直接",
        "无法直接创建",
        "无法创建",
        "工具列表里没有",
        "没有看到这个 mcp",
        "没有看到 tencent-docs 的 mcp",
        "没有直接的腾讯文档 mcp",
        "没有 tencent-docs mcp",
        "没有现成的数据源",
    )
    failed_script_markers = (
        "预定义脚本未能运行完成",
        "未能运行完成",
        "脚本执行失败",
        "命令执行失败",
        "exit status",
        "non-zero exit",
    )
    success_markers = (
        "已成功运行技能",
        "技能 **",
        "已成功加载网页内容",
        "已成功加载 **https://",
        "工具 **create_subagent** 已调用完成",
        "工具 **list_subagent_artifacts** 已调用完成",
        "已成功加载 **references/",
        "已成功创建",
        "创建成功",
        "已创建",
    )

    blocked = present_markers(compact, blocked_markers)
    failed_scripts = present_markers(compact, failed_script_markers)

    if normalized == "tencent-docs":
        created_doc_markers = (
            "docs.qq.com",
            "腾讯文档链接",
            "已成功创建腾讯文档",
            "创建了一份腾讯文档",
        )
        if present_markers(compact, created_doc_markers) and has_task_result(answer):
            return "success", present_markers(compact, created_doc_markers)[:10]
        if blocked or failed_scripts:
            return "blocked", sorted(set(blocked + failed_scripts))[:10]

    if normalized == "humanizer":
        if has_task_result(answer) and present_markers(answer, ("改写", "修改理由", "更像真人", "原意")):
            return "success", ["humanizer_rewrite_result"]

    if normalized == "self-improvement":
        learning_write_markers = (
            ".learnings",
            "已成功向",
            "已记录",
            "写入经验",
        )
        if present_markers(compact, learning_write_markers) and has_task_result(answer):
            return "success", present_markers(compact, learning_write_markers)[:10]
        if has_task_result(answer):
            return "degraded", ["experience_summary_without_persistent_learning_write"]

    if normalized in {"self-improving", "self-improving + proactive agent"}:
        memory_success_markers = (
            "memory.md",
            "corrections.md",
            "index.md",
            "heartbeat-state.md",
            "memory stats",
            "纠错学习完成",
            "初始化完成",
        )
        write_success_markers = (
            "已成功向",
            "写入内容",
            "已成功获取文件夹",
        )
        if (
            has_task_result(answer)
            and present_markers(compact, memory_success_markers)
            and present_markers(compact, write_success_markers)
        ):
            return "success", sorted(set(present_markers(compact, memory_success_markers) + present_markers(compact, write_success_markers)))[:10]

    if normalized == "find-skills":
        if failed_scripts and has_task_result(answer):
            return "degraded", sorted(set(failed_scripts + ["fallback_skill_recommendations"]))[:10]
        if has_task_result(answer) and present_markers(answer, ("推荐", "适用理由", "安装", "技能")):
            return "success", ["skill_recommendations_generated"]

    if normalized == "agent-browser":
        if failed_scripts and has_task_result(answer):
            return "degraded", sorted(set(failed_scripts + ["fallback_webpage_inspection"]))[:10]

    if normalized == "summarize":
        if failed_scripts and has_task_result(answer):
            return "degraded", sorted(set(failed_scripts + ["fallback_summary_generated"]))[:10]

    if normalized == "weather":
        weather_markers = ("天气", "温度", "降雨", "湿度", "出行建议", "open-meteo", "wttr.in")
        if has_task_result(answer) and present_markers(haystack, weather_markers):
            return "success", present_markers(haystack, weather_markers)[:10]

    if normalized == "dev-expert":
        dev_markers = (
            "根因",
            "修复方案",
            "验收标准",
            "测试用例",
            "复盘",
            "六步闭环",
        )
        if has_task_result(answer) and present_markers(answer, dev_markers):
            return "success", present_markers(answer, dev_markers)[:10]

    if blocked:
        return "blocked", sorted(set(blocked))[:10]

    if failed_scripts:
        if has_task_result(answer):
            return "degraded", sorted(set(failed_scripts))[:10]
        return "failed", sorted(set(failed_scripts))[:10]

    for marker in success_markers:
        if marker.lower() in compact:
            evidence.append(marker)
    if evidence and has_task_result(answer):
        return "success", sorted(set(evidence))[:10]
    if has_task_result(answer):
        return "success", ["task_result_without_tool_success_marker"]
    return "failed", ["no_task_result_detected"]


def present_markers(text: str, markers: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [marker for marker in markers if marker.lower() in lowered]


def has_task_result(answer: str) -> bool:
    text = answer.strip()
    if len(text) < 80:
        return False
    positive_markers = (
        "总结",
        "要点",
        "建议",
        "结果",
        "推荐",
        "风险点",
        "天气",
        "温度",
        "标题",
        "主要内容",
        "大纲",
        "经验",
        "改写",
        "修改理由",
        "根因",
        "修复方案",
        "验收标准",
        "测试用例",
        "复盘",
        "已成功",
        "完成",
    )
    return any(marker in text for marker in positive_markers)


def is_timeout_response(text: str) -> bool:
    lowered = text.lower()
    return "timeout" in lowered or "timed out" in lowered or "stream exceeded" in lowered


TERMINAL_RUN_STATUSES = {"completed", "failed", "interrupted", "cancelled", "stopped"}


def empty_conversation_completion() -> dict[str, Any]:
    return {
        "status_http_status": None,
        "status_body": "",
        "is_generating": None,
        "wait_timed_out": False,
        "history_http_status": None,
        "history_body": "",
        "history_item": {},
        "run_status": "",
        "run_terminal": None,
        "answer": "",
    }


def wait_for_conversation_completion(base_url: str, conversation_id: str) -> dict[str, Any]:
    status_timeout = float(os.environ.get("SKILL_AUTO_STATUS_POLL_TIMEOUT", os.environ.get("SKILL_AUTO_CHAT_TIMEOUT", "900")))
    status_interval = float(os.environ.get("SKILL_AUTO_STATUS_POLL_INTERVAL", "5"))
    history_timeout = float(os.environ.get("SKILL_AUTO_HISTORY_TERMINAL_TIMEOUT", "60"))
    history_interval = float(os.environ.get("SKILL_AUTO_HISTORY_POLL_INTERVAL", "2"))

    status_http_status: int | None = None
    status_body = ""
    is_generating: bool | None = None
    wait_timed_out = False
    deadline = time.monotonic() + max(status_timeout, 0)
    while True:
        status_http_status, status_body = http_json("GET", base_url + f"/api/core/conversations/{conversation_id}:status")
        parsed_is_generating = parse_is_generating(status_body)
        if parsed_is_generating is not None:
            is_generating = parsed_is_generating
        if status_http_status == 404:
            pass
        elif is_generating is False:
            break
        elif status_http_status is not None and 200 <= status_http_status < 300 and is_generating is None:
            break
        if time.monotonic() >= deadline:
            wait_timed_out = True
            break
        time.sleep(max(status_interval, 0.1))

    history_status, history_body, latest_item = poll_terminal_history_item(
        base_url,
        conversation_id,
        timeout=history_timeout,
        interval=history_interval,
    )
    run_status = latest_history_run_status(latest_item)
    run_terminal = latest_item.get("run_terminal") if isinstance(latest_item.get("run_terminal"), dict) else None
    return {
        "status_http_status": status_http_status,
        "status_body": status_body,
        "is_generating": is_generating,
        "wait_timed_out": wait_timed_out,
        "history_http_status": history_status,
        "history_body": history_body,
        "history_item": latest_item,
        "run_status": run_status,
        "run_terminal": run_terminal,
        "answer": extract_answer_from_history_item(latest_item) or extract_history_answer(history_body),
    }


def parse_is_generating(body: str) -> bool | None:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    data = parsed.get("data") if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict) else parsed
    if not isinstance(data, dict):
        return None
    value = data.get("is_generating")
    if isinstance(value, bool):
        return value
    return None


def poll_terminal_history_item(
    base_url: str,
    conversation_id: str,
    *,
    timeout: float,
    interval: float,
) -> tuple[int | None, str, dict[str, Any]]:
    deadline = time.monotonic() + max(timeout, 0)
    latest_status: int | None = None
    latest_body = ""
    latest_item: dict[str, Any] = {}
    while True:
        latest_status, latest_body = http_json(
            "GET",
            base_url + f"/api/core/conversations/{conversation_id}:history?page_size=1",
        )
        candidate = latest_history_item(latest_body)
        if candidate:
            latest_item = candidate
            if is_terminal_history_item(candidate):
                break
        if time.monotonic() >= deadline:
            break
        time.sleep(max(interval, 0.1))
    return latest_status, latest_body, latest_item


def latest_history_item(body: str) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    data = parsed.get("data") if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict) else parsed
    histories = data.get("history") if isinstance(data, dict) else None
    if not isinstance(histories, list) or not histories:
        return {}
    first = histories[0]
    return first if isinstance(first, dict) else {}


def latest_history_run_status(item: dict[str, Any]) -> str:
    run_terminal = item.get("run_terminal")
    if isinstance(run_terminal, dict):
        terminal_status = run_terminal.get("status")
        if isinstance(terminal_status, str) and terminal_status:
            return terminal_status
    run_status = item.get("run_status")
    return run_status if isinstance(run_status, str) else ""


def is_terminal_history_item(item: dict[str, Any]) -> bool:
    return latest_history_run_status(item) in TERMINAL_RUN_STATUSES


def is_history_answer_complete(answer: str) -> bool:
    complete_markers = (
        "file_id:",
        "save_chat_artifact",
        "任务完成",
        "报告已生成",
        "已生成并保存",
        "HTML报告已生成",
        "HTML 报告文件",
    )
    if present_markers(answer, complete_markers):
        return True
    if ".html" in answer and "已成功向" in answer and "写入内容" in answer:
        return True
    return False


def extract_history_answer(body: str) -> str:
    item = latest_history_item(body)
    if item:
        return extract_answer_from_history_item(item)
    return ""


def extract_answer_from_history_item(item: dict[str, Any]) -> str:
    for key in ("result", "content", "answer"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def extract_output_artifacts(text: str) -> list[str]:
    if not text:
        return []
    artifacts: list[str] = []
    patterns = (
        r"file_id:[0-9a-fA-F-]+",
        r"/static-files/[^\s)\"'<>]+",
        r"[\w\u4e00-\u9fff./-]+\.html\b",
        r"[\w\u4e00-\u9fff./-]+\.pdf\b",
        r"[\w\u4e00-\u9fff./-]+\.png\b",
        r"[\w\u4e00-\u9fff./-]+\.jpg\b",
        r"[\w\u4e00-\u9fff./-]+\.jpeg\b",
    )
    for pattern in patterns:
        for match in re.findall(pattern, text):
            cleaned = match.strip().rstrip("，。；;、")
            if cleaned.startswith("references/") or cleaned.startswith("./references/"):
                continue
            if cleaned and cleaned not in artifacts:
                artifacts.append(cleaned)
    return artifacts


def aggregate_output_artifacts(chat_results: list[dict[str, Any]]) -> list[str]:
    artifacts: list[str] = []
    for result in chat_results:
        for artifact in result.get("output_artifacts") or []:
            if artifact not in artifacts:
                artifacts.append(artifact)
    return artifacts


def is_unhelpful_response(answer: str) -> bool:
    lowered = answer.lower()
    markers = (
        "sorry, i have not learned how to answer this question yet",
        "i have not learned how to answer",
        "我还没有学会",
        "无法回答这个问题",
        "不能回答这个问题",
    )
    return any(marker in lowered for marker in markers)


def is_rate_limited_response(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "请求过于频繁",
        "触发限流",
        "模型调用失败",
        "本次未生成可用内容",
        "rate limit",
        "rate_limited",
        "too many requests",
        "429",
    )
    return any(marker in lowered for marker in markers)


def http_stream_json(method: str, url: str, payload: dict[str, Any], timeout: int) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "text/event-stream")
    request.add_header("Content-Type", "application/json")
    if auth := os.environ.get("SKILL_AUTO_AUTHORIZATION"):
        request.add_header("Authorization", auth)
    if cookie := os.environ.get("SKILL_AUTO_COOKIE"):
        request.add_header("Cookie", cookie)
    if user_id := os.environ.get("SKILL_AUTO_USER_ID"):
        request.add_header("X-User-Id", user_id)
    if user_name := os.environ.get("SKILL_AUTO_USER_NAME"):
        request.add_header("X-User-Name", user_name)
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.setitimer(signal.ITIMER_REAL, timeout)

    def raise_stream_timeout(signum: int, frame: Any) -> None:
        raise TimeoutError(f"stream exceeded {timeout}s")

    try:
        signal.signal(signal.SIGALRM, raise_stream_timeout)
        with urllib.request.urlopen(request, timeout=min(timeout, 30)) as response:  # noqa: S310 - user-configured local URL.
            return response.status, collect_stream(response)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except (TimeoutError, socket.timeout) as exc:
        return 0, f"timeout: {exc}"
    except urllib.error.URLError as exc:
        return 0, str(exc)
    finally:
        signal.setitimer(signal.ITIMER_REAL, old_timer[0], old_timer[1])
        signal.signal(signal.SIGALRM, old_handler)


def collect_stream(response: Any) -> str:
    excerpts: list[str] = []
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            excerpts.append(data)
            continue
        text = extract_text(parsed)
        if text:
            excerpts.append(text)
    return "\n".join(excerpts)


def extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := extract_text(item)))
    if not isinstance(value, dict):
        return ""
    interesting_keys = (
        "content",
        "text",
        "reasoning_content",
        "message",
        "error",
        "finish_reason",
    )
    parts = [str(value[key]) for key in interesting_keys if isinstance(value.get(key), str) and value.get(key)]
    for key in ("result", "delta", "answer", "answers", "data"):
        nested = extract_text(value.get(key))
        if nested:
            parts.append(nested)
    return "\n".join(parts)


def format_chat_log(result: dict[str, Any]) -> list[str]:
    return [
        f"CHAT {result['case_id']} attempt={result.get('attempt')} conversation_id={result['conversation_id']} "
        f"http_status={result['http_status']} history_http_status={result.get('history_http_status')} "
        f"status={result['status']} trigger={result.get('skill_trigger_status')} execution={result.get('skill_execution_status')}",
        f"prompt={result['prompt']}",
        f"response_excerpt={result['response_excerpt'][:4000]}",
        f"skill_trigger_evidence={result.get('skill_trigger_evidence')}",
        f"skill_execution_evidence={result.get('skill_execution_evidence')}",
        f"failure_category={result.get('failure_category')}",
        f"failure_technical_reason={result.get('failure_technical_reason')}",
    ]


def redact_observation_secrets(observation: dict[str, Any], secret_values: list[str]) -> dict[str, Any]:
    if not secret_values:
        return observation
    return redact_value(observation, secret_values)


def redact_value(value: Any, secret_values: list[str]) -> Any:
    if isinstance(value, str):
        return redact_text(value, secret_values)
    if isinstance(value, list):
        return [redact_value(item, secret_values) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item, secret_values) for key, item in value.items()}
    return value


def redact_text(text: str, secret_values: list[str]) -> str:
    redacted = text
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "***REDACTED***")
    return redacted


def run_ui_stub(base_url: str | None, name: str, test_cases: list[dict[str, Any]], log_path: Path) -> dict[str, Any]:
    if not base_url:
        log_path.write_text("ui runner requires --base-url\n", encoding="utf-8")
        return {
            "install_status": "skipped",
            "run_status": "skipped",
            "failure_category": "runner_not_configured",
            "failure_user_message": "UI 测试需要提供 LazyMind 前端地址。",
            "failure_technical_reason": "missing --base-url",
        }
    log_path.write_text(
        "ui runner extension point\n"
        f"base_url={base_url}\n"
        f"name={name}\n"
        f"test_cases={test_cases}\n",
        encoding="utf-8",
    )
    return {
        "install_status": "skipped",
        "run_status": "skipped",
        "failure_category": "runner_not_implemented",
        "failure_user_message": "Playwright UI runner 已预留，但还需要补充登录、上传、chat 试用脚本。",
        "failure_technical_reason": "ui runner is not implemented yet",
    }
