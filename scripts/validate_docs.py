#!/usr/bin/env python3
"""Mechanical documentation-system validator, run by .githooks/pre-commit.

Checks the invariants that keep the doc system navigable (the judgment-based
half of grooming lives in .claude/skills/doc-grooming.md):

1. Index completeness — every report / reference / spec / skill file is
   linked from its owning index.
2. Frontmatter sanity — reports, reference docs, and specs carry ``type:``
   and ``status:``; ``status: superseded`` requires ``superseded-by:``.
3. Canonical roadmap — docs/roadmap.md exists, is typed ``index``, and
   carries a parseable ``last-validated`` date.
4. Internal links — every relative markdown link in docs/ (including the
   index files, roadmap, and project overview), .claude/skills/, and the
   root/combat-harness CLAUDE.md resolves to an existing file. Links inside
   fenced code blocks or inline code spans are examples, not navigation,
   and are ignored.

Exit 0 = clean; exit 1 = violations listed on stderr.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INDEX_RULES = [
    # (member glob dir, exclude names, owning index)
    (ROOT / "docs/reports", {"INDEX.md"}, ROOT / "docs/reports/INDEX.md"),
    (ROOT / "docs/reference", {"README.md"}, ROOT / "docs/reference/README.md"),
    (ROOT / "docs/specs", {"README.md"}, ROOT / "docs/project-overview.md"),
    (ROOT / ".claude/skills", {"README.md"}, ROOT / ".claude/skills/README.md"),
]

FRONTMATTER_DIRS = [ROOT / "docs/reports", ROOT / "docs/reference", ROOT / "docs/specs"]
FRONTMATTER_EXCLUDE = {"INDEX.md", "README.md"}


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        m = re.match(r"^([A-Za-z-]+):\s*(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


# Fenced blocks first (their body may contain backticks), then inline code
# spans (`...` or ``...``) — both are example text, not navigation.
_CODE_FENCE_RE = re.compile(r"^(?:```|~~~).*?^(?:```|~~~)\s*$", re.S | re.M)
_CODE_SPAN_RE = re.compile(r"``[^`]*``|`[^`]*`")
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _link_check_files(root: Path) -> list[Path]:
    files = sorted(root.glob("docs/**/*.md"))
    files += sorted(root.glob(".claude/skills/*.md"))
    for extra in (root / "CLAUDE.md", root / "combat-harness" / "CLAUDE.md"):
        if extra.exists():
            files.append(extra)
    return files


def check_links(root: Path) -> list[str]:
    """Return one error string per relative link that resolves to nothing."""
    errors: list[str] = []
    for path in _link_check_files(root):
        text = _CODE_SPAN_RE.sub("", _CODE_FENCE_RE.sub("", path.read_text(encoding="utf-8")))
        for match in _LINK_RE.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if "<" in target:  # template placeholder, e.g. charts/<campaign>/…
                continue
            target_path = (path.parent / target.split("#", 1)[0]).resolve()
            if not target_path.exists():
                errors.append(
                    f"{path.relative_to(root)}: broken link -> {target}"
                )
    return errors


def main() -> int:
    errors: list[str] = []

    errors.extend(check_links(ROOT))

    for member_dir, exclude, index_path in INDEX_RULES:
        index_text = index_path.read_text(encoding="utf-8")
        for member in sorted(member_dir.glob("*.md")):
            if member.name in exclude:
                continue
            if member.name not in index_text:
                errors.append(
                    f"{member.relative_to(ROOT)} is not linked from "
                    f"{index_path.relative_to(ROOT)}"
                )

    for fm_dir in FRONTMATTER_DIRS:
        for member in sorted(fm_dir.glob("*.md")):
            if member.name in FRONTMATTER_EXCLUDE:
                continue
            fields = frontmatter(member)
            rel = member.relative_to(ROOT)
            if "type" not in fields or "status" not in fields:
                errors.append(f"{rel}: frontmatter missing 'type:' or 'status:'")
                continue
            if fields["status"] == "superseded" and "superseded-by" not in fields:
                errors.append(f"{rel}: status superseded without 'superseded-by:'")

    roadmap = ROOT / "docs/roadmap.md"
    if not roadmap.exists():
        errors.append("docs/roadmap.md missing (canonical forward roadmap)")
    else:
        fields = frontmatter(roadmap)
        if fields.get("type") != "index":
            errors.append("docs/roadmap.md: frontmatter 'type:' must be 'index'")
        try:
            date.fromisoformat(fields.get("last-validated", ""))
        except ValueError:
            errors.append("docs/roadmap.md: 'last-validated:' is not an ISO date")

    if errors:
        print("validate_docs: documentation-system violations:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "Fix per .claude/skills/doc-grooming.md (index the file, correct "
            "frontmatter, or re-groom the roadmap).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
