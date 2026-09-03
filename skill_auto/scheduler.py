from __future__ import annotations

import os
import plistlib
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .manifest import write_yaml


@dataclass
class ScheduleResult:
    label: str
    manifest_path: Path
    script_path: Path
    plist_path: Path
    installed_plist_path: Path | None = None
    launchctl_output: str | None = None


def create_schedule(
    *,
    at: str,
    manifest: Path | None,
    skills: list[str],
    base_url: str,
    username: str,
    password: str,
    mode: str,
    run_chat: bool,
    max_response_chars: int,
    attempts: int,
    retry_delay: float,
    retry_backoff: float,
    rate_limit_attempts: int,
    rate_limit_delay: float,
    rate_limit_backoff: float,
    rate_limit_pass_through: bool,
    between_skill_delay: float,
    demo_case_generator: str,
    demo_case_batch_size: int,
    out: str | None,
    project_root: Path,
    install_launchd: bool,
) -> ScheduleResult:
    if attempts < 1:
        raise ValueError("--attempts must be >= 1")
    run_at = parse_at(at)
    schedules_dir = project_root / "schedules"
    schedules_dir.mkdir(parents=True, exist_ok=True)
    label = "com.lazymind.skill-auto." + run_at.strftime("%Y%m%d%H%M")

    manifest_path = manifest
    if manifest_path is None:
        if not skills:
            raise ValueError("provide --manifest or at least one --skill name=url")
        manifest_path = schedules_dir / f"skills-{run_at.strftime('%Y%m%d-%H%M')}.yaml"
        write_yaml(manifest_path, manifest_from_skills(skills, run_at))
    else:
        manifest_path = manifest_path.expanduser()
        if not manifest_path.is_absolute():
            manifest_path = (project_root / manifest_path).resolve()

    script_path = schedules_dir / f"{label}.sh"
    plist_path = schedules_dir / f"{label}.plist"
    write_runner_script(
        script_path=script_path,
        project_root=project_root,
        manifest_path=manifest_path,
        base_url=base_url,
        username=username,
        password=password,
        mode=mode,
        run_chat=run_chat,
        max_response_chars=max_response_chars,
        attempts=attempts,
        retry_delay=retry_delay,
        retry_backoff=retry_backoff,
        rate_limit_attempts=rate_limit_attempts,
        rate_limit_delay=rate_limit_delay,
        rate_limit_backoff=rate_limit_backoff,
        rate_limit_pass_through=rate_limit_pass_through,
        between_skill_delay=between_skill_delay,
        demo_case_generator=demo_case_generator,
        demo_case_batch_size=demo_case_batch_size,
        out=out,
    )
    write_launchd_plist(plist_path, label, script_path, run_at)

    result = ScheduleResult(label=label, manifest_path=manifest_path, script_path=script_path, plist_path=plist_path)
    if install_launchd:
        installed = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_bytes(plist_path.read_bytes())
        proc = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(installed)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0 and "already bootstrapped" in (proc.stderr + proc.stdout).lower():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(installed)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            proc = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(installed)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "launchctl bootstrap failed").strip())
        result.installed_plist_path = installed
        result.launchctl_output = (proc.stdout + proc.stderr).strip()
    return result


def parse_at(value: str) -> datetime:
    text = value.strip()
    now = datetime.now()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    raise ValueError("--at must be 'YYYY-MM-DD HH:MM' or 'HH:MM'")


def manifest_from_skills(skills: list[str], run_at: datetime) -> dict[str, Any]:
    items = []
    for raw in skills:
        if "=" not in raw:
            raise ValueError("--skill must use name=url")
        name, link = raw.split("=", 1)
        name = name.strip()
        link = clean_skill_link(link.strip())
        if not name or not link:
            raise ValueError("--skill must use non-empty name=url")
        items.append({"name": name, "link": link})
    return {"schema_version": 1, "batch_id": f"scheduled-{run_at.strftime('%Y%m%d-%H%M')}", "skills": items}


def clean_skill_link(link: str) -> str:
    return link.strip().rstrip("”\"'`").replace("\\_", "_")


def write_runner_script(
    *,
    script_path: Path,
    project_root: Path,
    manifest_path: Path,
    base_url: str,
    username: str,
    password: str,
    mode: str,
    run_chat: bool,
    max_response_chars: int,
    attempts: int,
    retry_delay: float,
    retry_backoff: float,
    rate_limit_attempts: int,
    rate_limit_delay: float,
    rate_limit_backoff: float,
    rate_limit_pass_through: bool,
    between_skill_delay: float,
    demo_case_generator: str,
    demo_case_batch_size: int,
    out: str | None,
) -> None:
    out_expr = shlex.quote(out) if out else '"reports/scheduled-$(date +%Y%m%d-%H%M%S)"'
    content = f"""#!/bin/zsh
set -euo pipefail
cd {shlex.quote(str(project_root))}
mkdir -p logs
TOKEN=$(curl -sS -X POST {shlex.quote(base_url.rstrip('/') + '/api/authservice/auth/login')} \\
  -H 'Content-Type: application/json' \\
  -d {shlex.quote('{"username":"' + username + '","password":"' + password + '"}')} \\
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["access_token"])')
SKILL_AUTO_AUTHORIZATION="Bearer $TOKEN" python3 -m skill_auto test \\
  --manifest {shlex.quote(str(manifest_path))} \\
  --runner api \\
  --base-url {shlex.quote(base_url)} \\
  --mode {shlex.quote(mode)} \\
  --run-chat {str(run_chat).lower()} \\
  --max-response-chars {max_response_chars} \\
  --attempts {attempts} \\
  --retry-delay {retry_delay} \\
  --retry-backoff {retry_backoff} \\
  --rate-limit-attempts {rate_limit_attempts} \\
  --rate-limit-delay {rate_limit_delay} \\
  --rate-limit-backoff {rate_limit_backoff} \\
  --rate-limit-pass-through {str(rate_limit_pass_through).lower()} \\
  --between-skill-delay {between_skill_delay} \\
  --demo-case-generator {shlex.quote(demo_case_generator)} \\
  --demo-case-batch-size {demo_case_batch_size} \\
  --out {out_expr}
"""
    script_path.write_text(content, encoding="utf-8")
    script_path.chmod(0o755)


def write_launchd_plist(plist_path: Path, label: str, script_path: Path, run_at: datetime) -> None:
    payload: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": [str(script_path)],
        "StartCalendarInterval": {
            "Year": run_at.year,
            "Month": run_at.month,
            "Day": run_at.day,
            "Hour": run_at.hour,
            "Minute": run_at.minute,
        },
        "StandardOutPath": str(script_path.with_suffix(".out.log")),
        "StandardErrorPath": str(script_path.with_suffix(".err.log")),
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle)
