from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import time

from .downloader import prepare_source, source_type, stable_id
from .evaluator import finalize_recommendation
from .builtin_writer import update_builtin_sources
from .featured_writer import write_featured
from .manifest import load_yaml, require_skill_list
from .models import SkillSource, TrialRecord
from .onboarding_resolver import resolve_onboarding_manifest
from .preflight import inspect_source
from .reporter import write_reports
from .runner import generate_test_cases, generate_test_cases_batch, run_skill
from .scheduler import create_schedule


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="skill-auto")
    subparsers = parser.add_subparsers(dest="command", required=True)

    test_parser = subparsers.add_parser("test", help="test Skills and write trial reports")
    test_parser.add_argument("--manifest", required=True, type=Path)
    test_parser.add_argument("--out", type=Path)
    test_parser.add_argument("--runner", choices=["offline", "api", "ui"], default="offline")
    test_parser.add_argument("--base-url")
    test_parser.add_argument("--mode", choices=["install", "smoke", "demo"], default="demo", help="test depth")
    test_parser.add_argument(
        "--demo-case-generator",
        choices=["codex", "llm", "static"],
        default="codex",
        help="demo case source: codex uses the local Codex model; static uses built-in templates",
    )
    test_parser.add_argument("--demo-case-batch-size", type=int, default=5, help="number of Skills per Codex demo-case generation batch")
    test_parser.add_argument("--run-chat", type=parse_bool, default=True, help="false means install/static check only")
    test_parser.add_argument(
        "--max-response-chars",
        type=int,
        default=2000,
        help="max chat answer chars retained per attempt; 0 means full response",
    )
    test_parser.add_argument("--attempts", type=int, default=2, help="max attempts per test case")
    test_parser.add_argument("--retry-delay", type=float, default=30.0, help="seconds to wait before retrying a failed chat attempt")
    test_parser.add_argument("--retry-backoff", type=float, default=3.0, help="multiplier applied to retry delay after each failed attempt")
    test_parser.add_argument("--rate-limit-attempts", type=int, default=3, help="extra attempts used only after model rate-limit responses")
    test_parser.add_argument("--rate-limit-delay", type=float, default=120.0, help="initial seconds to wait after a model rate-limit response")
    test_parser.add_argument("--rate-limit-backoff", type=float, default=2.0, help="multiplier for model rate-limit retry waits")
    test_parser.add_argument(
        "--rate-limit-pass-through",
        type=parse_bool,
        default=True,
        help="true means repeated model rate limits do not mark the Skill as failed",
    )
    test_parser.add_argument("--between-skill-delay", type=float, default=30.0, help="seconds to wait between skills in one batch")
    test_parser.set_defaults(func=cmd_test)

    onboard_parser = subparsers.add_parser("onboard", help="resolve or apply LazyMind onboarding")
    onboard_parser.add_argument("--manifest", required=True, type=Path)
    onboard_parser.add_argument("--lazymind-root", type=Path, default=Path.cwd())
    onboard_parser.add_argument("--resolve-only", action="store_true")
    onboard_parser.add_argument("--dry-run", action="store_true")
    onboard_parser.add_argument("--apply", action="store_true")
    onboard_parser.set_defaults(func=cmd_onboard)

    run_parser = subparsers.add_parser("run", help="test then resolve onboarding")
    run_parser.add_argument("--test-manifest", required=True, type=Path)
    run_parser.add_argument("--onboard-manifest", type=Path)
    run_parser.add_argument("--out", type=Path)
    run_parser.add_argument("--runner", choices=["offline", "api", "ui"], default="offline")
    run_parser.add_argument("--base-url")
    run_parser.add_argument("--mode", choices=["install", "smoke", "demo"], default="demo", help="test depth")
    run_parser.add_argument(
        "--demo-case-generator",
        choices=["codex", "llm", "static"],
        default="codex",
        help="demo case source: codex uses the local Codex model; static uses built-in templates",
    )
    run_parser.add_argument("--demo-case-batch-size", type=int, default=5, help="number of Skills per Codex demo-case generation batch")
    run_parser.add_argument("--run-chat", type=parse_bool, default=True, help="false means install/static check only")
    run_parser.add_argument(
        "--max-response-chars",
        type=int,
        default=2000,
        help="max chat answer chars retained per attempt; 0 means full response",
    )
    run_parser.add_argument("--attempts", type=int, default=2, help="max attempts per test case")
    run_parser.add_argument("--retry-delay", type=float, default=30.0, help="seconds to wait before retrying a failed chat attempt")
    run_parser.add_argument("--retry-backoff", type=float, default=3.0, help="multiplier applied to retry delay after each failed attempt")
    run_parser.add_argument("--rate-limit-attempts", type=int, default=3, help="extra attempts used only after model rate-limit responses")
    run_parser.add_argument("--rate-limit-delay", type=float, default=120.0, help="initial seconds to wait after a model rate-limit response")
    run_parser.add_argument("--rate-limit-backoff", type=float, default=2.0, help="multiplier for model rate-limit retry waits")
    run_parser.add_argument(
        "--rate-limit-pass-through",
        type=parse_bool,
        default=True,
        help="true means repeated model rate limits do not mark the Skill as failed",
    )
    run_parser.add_argument("--between-skill-delay", type=float, default=30.0, help="seconds to wait between skills in one batch")
    run_parser.set_defaults(func=cmd_run)

    pipeline_parser = subparsers.add_parser("pipeline", help="run install, smoke, and demo stages")
    pipeline_parser.add_argument("--manifest", required=True, type=Path)
    pipeline_parser.add_argument("--out", type=Path)
    pipeline_parser.add_argument("--runner", choices=["api"], default="api")
    pipeline_parser.add_argument("--base-url", required=True)
    pipeline_parser.add_argument("--smoke-attempts", type=int, default=1)
    pipeline_parser.add_argument("--demo-attempts", type=int, default=2)
    pipeline_parser.add_argument("--retry-delay", type=float, default=30.0)
    pipeline_parser.add_argument("--retry-backoff", type=float, default=3.0)
    pipeline_parser.add_argument("--rate-limit-attempts", type=int, default=3)
    pipeline_parser.add_argument("--rate-limit-delay", type=float, default=120.0)
    pipeline_parser.add_argument("--rate-limit-backoff", type=float, default=2.0)
    pipeline_parser.add_argument("--rate-limit-pass-through", type=parse_bool, default=True)
    pipeline_parser.add_argument(
        "--demo-case-generator",
        choices=["codex", "llm", "static"],
        default="codex",
        help="demo case source for stage 3",
    )
    pipeline_parser.add_argument("--demo-case-batch-size", type=int, default=5)
    pipeline_parser.add_argument("--smoke-between-skill-delay", type=float, default=10.0)
    pipeline_parser.add_argument("--demo-between-skill-delay", type=float, default=30.0)
    pipeline_parser.add_argument("--smoke-max-response-chars", type=int, default=500)
    pipeline_parser.add_argument("--demo-max-response-chars", type=int, default=0)
    pipeline_parser.set_defaults(func=cmd_pipeline)

    schedule_parser = subparsers.add_parser("schedule", help="schedule a Skill test run with launchd")
    schedule_parser.add_argument("--at", required=True, help="run time, e.g. '14:19' or '2026-09-01 14:19'")
    schedule_parser.add_argument("--manifest", type=Path)
    schedule_parser.add_argument("--skill", action="append", default=[], help="inline skill as name=url; can be repeated")
    schedule_parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    schedule_parser.add_argument("--username", default="admin")
    schedule_parser.add_argument("--password", default="admin")
    schedule_parser.add_argument("--mode", choices=["install", "smoke", "demo"], default="demo")
    schedule_parser.add_argument(
        "--demo-case-generator",
        choices=["codex", "llm", "static"],
        default="codex",
        help="demo case source when scheduled mode is demo",
    )
    schedule_parser.add_argument("--demo-case-batch-size", type=int, default=5)
    schedule_parser.add_argument("--run-chat", type=parse_bool, default=True)
    schedule_parser.add_argument("--max-response-chars", type=int, default=2000)
    schedule_parser.add_argument("--attempts", type=int, default=2)
    schedule_parser.add_argument("--retry-delay", type=float, default=30.0)
    schedule_parser.add_argument("--retry-backoff", type=float, default=3.0)
    schedule_parser.add_argument("--rate-limit-attempts", type=int, default=3)
    schedule_parser.add_argument("--rate-limit-delay", type=float, default=120.0)
    schedule_parser.add_argument("--rate-limit-backoff", type=float, default=2.0)
    schedule_parser.add_argument("--rate-limit-pass-through", type=parse_bool, default=True)
    schedule_parser.add_argument("--between-skill-delay", type=float, default=30.0)
    schedule_parser.add_argument("--out", help="output report dir; defaults to reports/scheduled-<timestamp>")
    schedule_parser.add_argument("--install-launchd", action="store_true", help="copy plist into ~/Library/LaunchAgents and load it")
    schedule_parser.set_defaults(func=cmd_schedule)

    args = parser.parse_args(argv)
    args.func(args)


def cmd_test(args: argparse.Namespace) -> None:
    out_dir = default_out_dir(args.out, args.manifest, args.mode)
    records = run_tests(
        args.manifest,
        out_dir,
        args.runner,
        args.base_url,
        args.mode,
        args.run_chat,
        args.max_response_chars,
        args.attempts,
        args.retry_delay,
        args.retry_backoff,
        args.rate_limit_attempts,
        args.rate_limit_delay,
        args.rate_limit_backoff,
        args.rate_limit_pass_through,
        args.between_skill_delay,
        args.demo_case_generator,
        args.demo_case_batch_size,
    )
    print(f"Wrote {len(records)} trial records to {out_dir}")


def cmd_run(args: argparse.Namespace) -> None:
    out_dir = default_out_dir(args.out, args.test_manifest, args.mode)
    records = run_tests(
        args.test_manifest,
        out_dir,
        args.runner,
        args.base_url,
        args.mode,
        args.run_chat,
        args.max_response_chars,
        args.attempts,
        args.retry_delay,
        args.retry_backoff,
        args.rate_limit_attempts,
        args.rate_limit_delay,
        args.rate_limit_backoff,
        args.rate_limit_pass_through,
        args.between_skill_delay,
        args.demo_case_generator,
        args.demo_case_batch_size,
    )
    print(f"Wrote {len(records)} trial records to {out_dir}")
    if args.onboard_manifest:
        resolved_path = out_dir / "onboarding.resolved.yaml"
        resolve_onboarding_manifest(args.onboard_manifest, resolved_path)
        print(f"Wrote resolved onboarding manifest to {resolved_path}")


def cmd_pipeline(args: argparse.Namespace) -> None:
    out_dir = default_out_dir(args.out, args.manifest, "pipeline")
    install_dir = out_dir / "01-install"
    smoke_dir = out_dir / "02-smoke"
    demo_dir = out_dir / "03-demo"

    install_records = run_tests(
        args.manifest,
        install_dir,
        args.runner,
        args.base_url,
        "install",
        False,
        0,
        1,
        args.retry_delay,
        args.retry_backoff,
        args.rate_limit_attempts,
        args.rate_limit_delay,
        args.rate_limit_backoff,
        args.rate_limit_pass_through,
        0,
        args.demo_case_generator,
        args.demo_case_batch_size,
    )
    print(f"Install stage wrote {len(install_records)} trial records to {install_dir}")

    smoke_manifest = install_dir / "install_passed.yaml"
    if not manifest_has_skills(smoke_manifest):
        print("Smoke stage skipped: install_passed.yaml is empty")
        return
    smoke_records = run_tests(
        smoke_manifest,
        smoke_dir,
        args.runner,
        args.base_url,
        "smoke",
        True,
        args.smoke_max_response_chars,
        args.smoke_attempts,
        args.retry_delay,
        args.retry_backoff,
        args.rate_limit_attempts,
        args.rate_limit_delay,
        args.rate_limit_backoff,
        args.rate_limit_pass_through,
        args.smoke_between_skill_delay,
        args.demo_case_generator,
        args.demo_case_batch_size,
    )
    print(f"Smoke stage wrote {len(smoke_records)} trial records to {smoke_dir}")

    demo_manifest = smoke_dir / "smoke_passed.yaml"
    if not manifest_has_skills(demo_manifest):
        print("Demo stage skipped: smoke_passed.yaml is empty")
        return
    demo_records = run_tests(
        demo_manifest,
        demo_dir,
        args.runner,
        args.base_url,
        "demo",
        True,
        args.demo_max_response_chars,
        args.demo_attempts,
        args.retry_delay,
        args.retry_backoff,
        args.rate_limit_attempts,
        args.rate_limit_delay,
        args.rate_limit_backoff,
        args.rate_limit_pass_through,
        args.demo_between_skill_delay,
        args.demo_case_generator,
        args.demo_case_batch_size,
    )
    print(f"Demo stage wrote {len(demo_records)} trial records to {demo_dir}")


def default_out_dir(out: Path | None, manifest_path: Path, mode: str) -> Path:
    if out is not None:
        return out
    stem = safe_path_stem(manifest_path.stem)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("reports") / f"{stem}-{mode}-{timestamp}"


def safe_path_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
    return cleaned or "skills"


def manifest_has_skills(path: Path) -> bool:
    if not path.exists():
        return False
    data = load_yaml(path)
    skills = data.get("skills")
    return isinstance(skills, list) and bool(skills)


def run_tests(
    manifest_path: Path,
    out_dir: Path,
    runner: str,
    base_url: str | None,
    mode: str,
    run_chat: bool,
    max_response_chars: int,
    attempts: int,
    retry_delay: float = 30.0,
    retry_backoff: float = 3.0,
    rate_limit_attempts: int = 3,
    rate_limit_delay: float = 120.0,
    rate_limit_backoff: float = 2.0,
    rate_limit_pass_through: bool = True,
    between_skill_delay: float = 30.0,
    demo_case_generator: str = "codex",
    demo_case_batch_size: int = 5,
) -> list[TrialRecord]:
    if attempts < 1:
        raise SystemExit("--attempts must be >= 1")
    if max_response_chars < 0:
        raise SystemExit("--max-response-chars must be >= 0")
    if retry_delay < 0:
        raise SystemExit("--retry-delay must be >= 0")
    if retry_backoff < 1:
        raise SystemExit("--retry-backoff must be >= 1")
    if rate_limit_attempts < 0:
        raise SystemExit("--rate-limit-attempts must be >= 0")
    if rate_limit_delay < 0:
        raise SystemExit("--rate-limit-delay must be >= 0")
    if rate_limit_backoff < 1:
        raise SystemExit("--rate-limit-backoff must be >= 1")
    if between_skill_delay < 0:
        raise SystemExit("--between-skill-delay must be >= 0")
    if demo_case_batch_size < 1:
        raise SystemExit("--demo-case-batch-size must be >= 1")
    data = load_yaml(manifest_path)
    skills = require_skill_list(data, manifest_path)
    workspace = out_dir / ".sources"
    records: list[TrialRecord] = []
    prepared: list[dict] = []
    for index, item in enumerate(skills, start=1):
        source = SkillSource(name=item["name"], link=item["link"], raw=item)
        trial_id = f"trial_{stable_id(str(index), source.name, source.link)}"
        item_run_chat = parse_bool(str(item.get("run_chat"))) if item.get("run_chat") is not None else run_chat
        item_mode = str(item.get("mode") or mode)
        if item_mode == "install":
            item_run_chat = False
        effective_mode = item_mode if item_run_chat else "install"
        record = TrialRecord(
            trial_id=trial_id,
            name=source.name,
            link=source.link,
            source_type=source_type(source.link),
            test_mode=effective_mode,
            run_chat=item_run_chat,
        )
        source_path, fetch_info = prepare_source(source, workspace)
        if source_path is not None and fetch_info and not fetch_info.startswith("fetch_failed"):
            record.source_commit = fetch_info
        item_env_values = normalize_env_values(item.get("env"))
        preflight = inspect_source(source_path, item_env_values)
        apply_preflight(record, preflight)
        if fetch_info and fetch_info.startswith("fetch_failed"):
            record.failure_category = "source_unavailable"
            record.failure_user_message = "无法拉取 Skill 源码，请检查链接、网络或仓库权限。"
            record.failure_technical_reason = fetch_info
            record.preflight_status = "fail"
        runtime_skill_name = item.get("runtime_skill_name") or record.runtime_skill_name or source.name
        record.runtime_skill_name = runtime_skill_name
        prepared.append(
            {
                "item": item,
                "source": source,
                "record": record,
                "source_path": source_path,
                "env_values": item_env_values,
            }
        )

    assign_test_cases(prepared, base_url, demo_case_generator, demo_case_batch_size)

    for index, entry in enumerate(prepared, start=1):
        item = entry["item"]
        source = entry["source"]
        record = entry["record"]
        source_path = entry["source_path"]
        item_env_values = entry["env_values"]
        log_path = out_dir / "logs" / f"{source.name}.log"
        if record.preflight_status in {"pass", "partial"}:
            run_result = run_skill(
                runner=runner,
                base_url=base_url,
                source_path=source_path,
                link=source.link,
                name=record.runtime_skill_name or record.name,
                test_cases=cases_with_env_context(record.generated_test_cases, item_env_values),
                log_path=log_path,
                attempts=int(item.get("attempts") or attempts),
                retry_delay=float(item.get("retry_delay") if item.get("retry_delay") is not None else retry_delay),
                retry_backoff=float(
                    item.get("retry_backoff") if item.get("retry_backoff") is not None else retry_backoff
                ),
                rate_limit_attempts=int(
                    item.get("rate_limit_attempts")
                    if item.get("rate_limit_attempts") is not None
                    else rate_limit_attempts
                ),
                rate_limit_delay=float(
                    item.get("rate_limit_delay") if item.get("rate_limit_delay") is not None else rate_limit_delay
                ),
                rate_limit_backoff=float(
                    item.get("rate_limit_backoff")
                    if item.get("rate_limit_backoff") is not None
                    else rate_limit_backoff
                ),
                rate_limit_pass_through=parse_bool(str(item.get("rate_limit_pass_through")))
                if item.get("rate_limit_pass_through") is not None
                else rate_limit_pass_through,
                run_chat=record.run_chat,
                test_mode=record.test_mode,
                max_response_chars=int(
                    item.get("max_response_chars")
                    if item.get("max_response_chars") is not None
                    else max_response_chars
                ),
                secret_values=secret_values_for_env(item_env_values),
            )
            apply_run_result(record, run_result)
        record.run_log_path = str(log_path)
        finalize_recommendation(record)
        records.append(record)
        item_delay = float(
            item.get("between_skill_delay") if item.get("between_skill_delay") is not None else between_skill_delay
        )
        if item_delay > 0 and index < len(skills):
            time.sleep(item_delay)
    write_reports(out_dir, records)
    return records


def assign_test_cases(
    prepared: list[dict],
    base_url: str | None,
    demo_case_generator: str,
    demo_case_batch_size: int,
) -> None:
    batch_items = []
    for index, entry in enumerate(prepared):
        item = entry["item"]
        record = entry["record"]
        manifest_cases = manifest_test_cases(item)
        if manifest_cases:
            record.generated_test_cases = manifest_cases
            continue
        item_generator = str(item.get("demo_case_generator") or demo_case_generator)
        if record.test_mode == "demo" and item_generator in {"codex", "llm", "static"}:
            batch_items.append(
                {
                    "key": str(index),
                    "name": record.runtime_skill_name or record.name,
                    "skill_type": record.detected_skill_type,
                    "source_path": entry["source_path"],
                    "demo_case_generator": item_generator,
                }
            )
            continue
        record.generated_test_cases = generate_test_cases(
            record.runtime_skill_name or record.name,
            record.detected_skill_type,
            record.test_mode,
            source_path=entry["source_path"],
            base_url=base_url,
            demo_case_generator=item_generator,
        )
    if not batch_items:
        return
    static_items = [item for item in batch_items if item["demo_case_generator"] == "static"]
    llm_items = [item for item in batch_items if item["demo_case_generator"] == "llm"]
    codex_items = [item for item in batch_items if item["demo_case_generator"] == "codex"]
    generated = {}
    if llm_items:
        generated.update(
            generate_test_cases_batch(
                llm_items,
                demo_case_generator="codex",
                batch_size=demo_case_batch_size,
                base_url=base_url,
            )
        )
    if codex_items:
        generated.update(
            generate_test_cases_batch(
                codex_items,
                demo_case_generator="codex",
                batch_size=demo_case_batch_size,
                base_url=base_url,
            )
        )
    if static_items:
        generated.update(generate_test_cases_batch(static_items, demo_case_generator="static", batch_size=demo_case_batch_size))
    for item in batch_items:
        prepared[int(item["key"])]["record"].generated_test_cases = generated[str(item["key"])]


def manifest_test_cases(item: dict) -> list[dict] | None:
    if item.get("test_cases"):
        return normalize_test_cases(item["test_cases"])
    if item.get("case"):
        return normalize_test_cases(item["case"])
    return None


def cases_with_env_context(cases: list[dict], env_values: dict[str, str]) -> list[dict]:
    if not env_values:
        return cases
    prefix = "本次测试可使用以下 API key / 环境变量，请在需要时直接使用，不要要求用户再次提供：\n"
    prefix += "\n".join(f"{key}={value}" for key, value in env_values.items())
    prefix += "\n\n"
    updated = []
    for case in cases:
        prompt = str(case.get("prompt") or "")
        updated.append({**case, "prompt": prefix + prompt, "env_context_keys": list(env_values)})
    return updated


def normalize_env_values(raw: object) -> dict[str, str]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        values = {}
        for key, value in raw.items():
            key_text = str(key).strip()
            if key_text and value is not None and str(value) != "":
                values[key_text] = str(value)
        return values
    if isinstance(raw, str):
        return parse_env_assignments(raw.splitlines())
    if isinstance(raw, list):
        values: dict[str, str] = {}
        for item in raw:
            if isinstance(item, dict):
                values.update(normalize_env_values(item))
                continue
            values.update(parse_env_assignments(str(item).splitlines()))
        return values
    raise SystemExit("manifest env must be a mapping, a KEY=value string, or a list of KEY=value entries")


def parse_env_assignments(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise SystemExit("manifest env string/list entries must use KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value:
            values[key] = value
    return values


def secret_values_for_env(env_values: dict[str, str]) -> list[str]:
    return list(env_values.values())


def normalize_test_cases(raw: object) -> list[dict]:
    if isinstance(raw, str):
        return [{"id": "core-flow", "prompt": raw, "source": "manifest_case"}]
    if isinstance(raw, dict):
        prompt = raw.get("prompt") or raw.get("case")
        if not isinstance(prompt, str) or not prompt.strip():
            raise SystemExit("manifest case must include a non-empty prompt")
        normalized = dict(raw)
        normalized["id"] = str(normalized.get("id") or "core-flow")
        normalized["prompt"] = prompt.strip()
        normalized.setdefault("source", "manifest_case")
        return [normalized]
    if isinstance(raw, list):
        cases = []
        for index, item in enumerate(raw, start=1):
            if isinstance(item, str):
                cases.append({"id": f"case-{index}", "prompt": item, "source": "manifest_test_cases"})
                continue
            if not isinstance(item, dict):
                raise SystemExit("manifest test_cases items must be strings or objects")
            prompt = item.get("prompt") or item.get("case")
            if not isinstance(prompt, str) or not prompt.strip():
                raise SystemExit("manifest test_cases items must include a non-empty prompt")
            normalized = dict(item)
            normalized["id"] = str(normalized.get("id") or f"case-{index}")
            normalized["prompt"] = prompt.strip()
            normalized.setdefault("source", "manifest_test_cases")
            cases.append(normalized)
        return cases
    raise SystemExit("manifest case/test_cases must be a string, object, or list")


def apply_preflight(record: TrialRecord, result: dict) -> None:
    for key, value in result.items():
        if hasattr(record, key):
            setattr(record, key, value)
    record.required_env_keys = result.get("required_env_keys", record.required_env_keys)
    record.requires_api_key = bool(result.get("requires_api_key", record.requires_api_key))
    record.dependencies = result.get("dependencies", record.dependencies)
    record.package_size_mb = result.get("package_size_mb", record.package_size_mb)
    missing_env = result.get("missing_env_keys") or []
    if missing_env:
        record.failure_category = "missing_api_key"
        record.failure_user_message = f"该 Skill 需要环境变量：{', '.join(missing_env)}。"
        record.failure_technical_reason = f"missing env keys: {', '.join(missing_env)}"
        record.suggested_fix = "在 manifest 对应 Skill 的 env 字段中补充 API key 后重试。"


def apply_run_result(record: TrialRecord, result: dict) -> None:
    preflight_failure = record.failure_category
    preflight_user_message = record.failure_user_message
    preflight_technical_reason = record.failure_technical_reason
    preflight_suggested_fix = record.suggested_fix
    for key, value in result.items():
        if hasattr(record, key):
            setattr(record, key, value)
    if preflight_failure == "missing_api_key" and record.skill_execution_status != "success":
        record.failure_category = "blocked_by_env"
        record.failure_user_message = preflight_user_message
        record.failure_technical_reason = preflight_technical_reason
        record.suggested_fix = preflight_suggested_fix


def cmd_onboard(args: argparse.Namespace) -> None:
    if args.resolve_only and (args.dry_run or args.apply):
        raise SystemExit("--resolve-only cannot be combined with --dry-run or --apply")
    if not args.resolve_only and not args.dry_run and not args.apply:
        raise SystemExit("choose one of --resolve-only, --dry-run, or --apply")
    resolved_path = resolved_manifest_path(args.manifest)
    resolved = resolve_onboarding_manifest(args.manifest, resolved_path)
    print(f"Wrote resolved manifest to {resolved_path}")
    if args.resolve_only:
        return
    dry_run = not args.apply
    builtin_result = update_builtin_sources(args.lazymind_root, resolved["skills"], dry_run=dry_run)
    featured_result = write_featured(args.lazymind_root, resolved["skills"], dry_run=dry_run)
    print(f"Builtin changes: {builtin_result['changes']}")
    print(f"Featured changes: {[item['path'] for item in featured_result]}")


def resolved_manifest_path(path: Path) -> Path:
    if path.name.endswith(".resolved.yaml"):
        return path
    return path.with_name(path.stem + ".resolved.yaml")


def cmd_schedule(args: argparse.Namespace) -> None:
    result = create_schedule(
        at=args.at,
        manifest=args.manifest,
        skills=args.skill,
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        mode=args.mode,
        run_chat=args.run_chat,
        max_response_chars=args.max_response_chars,
        attempts=args.attempts,
        retry_delay=args.retry_delay,
        retry_backoff=args.retry_backoff,
        rate_limit_attempts=args.rate_limit_attempts,
        rate_limit_delay=args.rate_limit_delay,
        rate_limit_backoff=args.rate_limit_backoff,
        rate_limit_pass_through=args.rate_limit_pass_through,
        between_skill_delay=args.between_skill_delay,
        demo_case_generator=args.demo_case_generator,
        demo_case_batch_size=args.demo_case_batch_size,
        out=args.out,
        project_root=Path.cwd(),
        install_launchd=args.install_launchd,
    )
    print(f"Schedule label: {result.label}")
    print(f"Manifest: {result.manifest_path}")
    print(f"Runner script: {result.script_path}")
    print(f"Launchd plist: {result.plist_path}")
    if result.installed_plist_path:
        print(f"Installed launchd plist: {result.installed_plist_path}")
