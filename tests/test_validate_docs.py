"""Tests for the internal-link check in scripts/validate_docs.py.

The link checker validates that every relative markdown link inside the doc
system resolves to an existing file. Links inside fenced code blocks AND
inline code spans are documentation examples, not navigation — they must be
ignored (docs/CONVENTIONS.md §Cross-references shows link syntax in inline
backticks).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_docs", REPO_ROOT / "scripts" / "validate_docs.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_docs"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules["validate_docs"]
        raise
    return module


def _make_tree(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / "combat-harness").mkdir()
    (tmp_path / "docs" / "real.md").write_text("# Real target\n")
    return tmp_path


class TestCheckLinks:
    def test_broken_relative_link_detected(self, tmp_path):
        v = _load_validator()
        root = _make_tree(tmp_path)
        (root / "docs" / "a.md").write_text("see [gone](missing.md)\n")
        errors = v.check_links(root)
        assert len(errors) == 1
        assert "missing.md" in errors[0]
        assert "docs/a.md" in errors[0]

    def test_valid_relative_link_passes(self, tmp_path):
        v = _load_validator()
        root = _make_tree(tmp_path)
        (root / "docs" / "a.md").write_text("see [real](real.md)\n")
        assert v.check_links(root) == []

    def test_link_resolves_relative_to_containing_file_not_cwd(self, tmp_path):
        v = _load_validator()
        root = _make_tree(tmp_path)
        # ../docs/real.md from .claude/skills/ — valid only if resolved
        # against the containing file's directory.
        (root / ".claude" / "skills" / "s.md").write_text("see [real](../../docs/real.md)\n")
        assert v.check_links(root) == []

    def test_link_in_fenced_code_block_ignored(self, tmp_path):
        v = _load_validator()
        root = _make_tree(tmp_path)
        (root / "docs" / "a.md").write_text("```markdown\n[example](does/not/exist.md)\n```\n")
        assert v.check_links(root) == []

    def test_link_in_inline_code_span_ignored(self, tmp_path):
        v = _load_validator()
        root = _make_tree(tmp_path)
        # The CONVENTIONS.md pattern: link syntax shown in inline backticks.
        (root / "docs" / "a.md").write_text("- Use links: `[name](relative/path.md)`.\n")
        assert v.check_links(root) == []

    def test_external_and_special_targets_skipped(self, tmp_path):
        v = _load_validator()
        root = _make_tree(tmp_path)
        (root / "docs" / "a.md").write_text(
            "[web](https://example.com/x.md)\n"
            "[plain](http://example.com)\n"
            "[mail](mailto:x@y.z)\n"
            "[frag](#local-anchor)\n"
            "[tmpl](charts/<campaign>/plot.png)\n"
        )
        assert v.check_links(root) == []

    def test_fragment_suffix_stripped_before_resolution(self, tmp_path):
        v = _load_validator()
        root = _make_tree(tmp_path)
        (root / "docs" / "a.md").write_text("[real](real.md#some-section)\n")
        assert v.check_links(root) == []

    def test_index_and_root_workflow_files_are_walked(self, tmp_path):
        v = _load_validator()
        root = _make_tree(tmp_path)
        (root / "docs" / "INDEX.md").write_text("[gone](nope.md)\n")
        (root / "CLAUDE.md").write_text("[gone](docs/nope.md)\n")
        (root / "combat-harness" / "CLAUDE.md").write_text("[gone](nope.md)\n")
        errors = v.check_links(root)
        assert len(errors) == 3


class TestRealTree:
    def test_repo_docs_have_no_broken_links(self):
        v = _load_validator()
        assert v.check_links(REPO_ROOT) == []
