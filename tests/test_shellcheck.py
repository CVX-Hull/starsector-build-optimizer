"""Shellcheck gates: rendered EC2 userdata templates and repo shell scripts.

Scope note: shellcheck catches quoting/globbing/syntax defects. It does NOT
model ERR-trap/`wait` reaping semantics — it passes the exact pre-fix pattern
behind the 2026-07-11 attempt-1 worker loss clean (measured false negative;
see docs/reports/2026-07-12-phase7-batch-v2-incidents.md). The executable
bash-semantics tests in test_phase7_learned_batch.py remain the guard for
that bug class; this module is the complementary static layer.

`shellcheck` resolves via the `shellcheck-py` dev dependency, so the skipifs
below never fire in the project venv; they exist for bare interpreters only.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_cloud_userdata import _make_worker_config
from tests.test_phase7_learned_batch import make_config

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELLCHECK = shutil.which("shellcheck")


def _shellcheck(name: str, script: str, tmp_path: Path) -> None:
    path = tmp_path / f"{name}.sh"
    path.write_text(script)
    proc = subprocess.run(
        [SHELLCHECK, "--shell=bash", str(path)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, f"{name}:\n{proc.stdout}"


def _rendered_templates(tmp_path: Path):
    """Yield (name, script) for every userdata renderer, both with every
    optional branch populated and with all optionals empty, so conditional
    template segments are inside the checked text."""
    from starsector_optimizer.cloud_userdata import (
        render_probe_user_data,
        render_user_data,
    )
    from starsector_optimizer.phase7_learned_batch import (
        render_phase7_learned_batch_user_data,
    )

    yield "worker_all_optionals", render_user_data(
        _make_worker_config(),
        tailscale_authkey="tskey-test",
        debug_ssh_pubkey="ssh-ed25519 AAAA test",
        mod_jar_override_url="http://100.64.0.1:8000/mod.jar",
        mod_jar_override_sha256="a" * 64,
    )
    yield "worker_no_optionals", render_user_data(
        _make_worker_config(),
        tailscale_authkey="tskey-test",
    )

    batch_cfg = make_config(tmp_path)
    yield "phase7_batch_all_optionals", render_phase7_learned_batch_user_data(
        dataclasses.replace(
            batch_cfg,
            noise_floor_override=0.5,
            fresh_honest_eval_ledger_id="ledger-1",
        ),
        control_plane_url="http://100.64.0.1:9131",
        bearer_token="secret-token",
        bundle_sha256="a" * 64,
    )
    yield "phase7_batch_no_optionals", render_phase7_learned_batch_user_data(
        batch_cfg,
        control_plane_url="http://100.64.0.1:9131",
        bearer_token="secret-token",
        bundle_sha256="a" * 64,
    )

    yield "probe", render_probe_user_data("probe-test-campaign")


@pytest.mark.skipif(SHELLCHECK is None, reason="shellcheck not installed")
def test_rendered_user_data_passes_shellcheck(tmp_path):
    for name, script in _rendered_templates(tmp_path):
        _shellcheck(name, script, tmp_path)


def _repo_shell_scripts() -> list[Path]:
    scripts = sorted(REPO_ROOT.glob("scripts/**/*.sh"))
    scripts.append(REPO_ROOT / ".githooks" / "pre-commit")
    return scripts


@pytest.mark.skipif(SHELLCHECK is None, reason="shellcheck not installed")
def test_repo_shell_scripts_pass_shellcheck():
    scripts = _repo_shell_scripts()
    assert scripts, "shell-script glob found nothing — check REPO_ROOT"
    proc = subprocess.run(
        [SHELLCHECK, "--shell=bash", *map(str, scripts)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout
