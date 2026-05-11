#!/usr/bin/env python3
"""Validate repo-local active implementation plans.

The check is intentionally small and dependency-free so it can run from the
pre-commit hook. It enforces the lifecycle invariant that approved, active, or
implemented plans must carry explicit passed plan-review and fresh-eye gates.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ACTIVE_DIR = Path(".claude/plans/active")
REQUIRES_PASSED_REVIEW = {"approved", "active", "implemented"}


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip()
    return out


def _section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^## {re.escape(heading)}\n(?P<body>.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group("body") if match else ""


def validate_plan(path: Path) -> list[str]:
    text = path.read_text()
    frontmatter = _frontmatter(text)
    status = frontmatter.get("status", "")
    approved = frontmatter.get("approved", "")
    gate = _section(text, "Plan Review Gate")
    fresh_eye = _section(text, "Fresh-Eye Review Gate")
    errors: list[str] = []

    if status in REQUIRES_PASSED_REVIEW:
        if not gate:
            errors.append("missing ## Plan Review Gate")
        if "- Status: passed" not in gate:
            errors.append("Plan Review Gate must include '- Status: passed'")
        if "plan-review" not in gate:
            errors.append("Plan Review Gate must reference plan-review")
        if not fresh_eye:
            errors.append("missing ## Fresh-Eye Review Gate")
        if "- Status: passed" not in fresh_eye:
            errors.append("Fresh-Eye Review Gate must include '- Status: passed'")
        if "sub-agent" not in fresh_eye:
            errors.append("Fresh-Eye Review Gate must reference sub-agents")
        if approved in {"", "null"}:
            errors.append("approved frontmatter must be non-null")

    if status == "draft" and gate and "- Status: passed" in gate:
        errors.append("draft plan cannot have a passed Plan Review Gate")
    if status == "draft" and fresh_eye and "- Status: passed" in fresh_eye:
        errors.append("draft plan cannot have a passed Fresh-Eye Review Gate")

    return errors


def main() -> int:
    if not ACTIVE_DIR.exists():
        return 0
    failed = False
    for path in sorted(ACTIVE_DIR.glob("*.md")):
        errors = validate_plan(path)
        if not errors:
            continue
        failed = True
        for error in errors:
            print(f"{path}: {error}", file=sys.stderr)
    if failed:
        print(
            "Active plan validation failed. Run plan-review with fresh-eye "
            "sub-agents and record passed Plan Review and Fresh-Eye Review "
            "gates before approving or implementing the plan.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
