# shellcheck shell=bash
# Auto-source .env so AWS_PROFILE (+ TAILSCALE_AUTHKEY for downstream scripts)
# is set without operators having to remember `set -a; source .env; set +a`.
# Per `.claude/skills/cloud-worker-ops.md` § AWS profile, the principled auth
# flow is the dedicated `starsector` IAM user surfaced via AWS_PROFILE —
# without it, boto3 falls back to whatever default-profile session the CLI
# happens to have (e.g. an Amazon-Q `login_session` against root, which
# boto3's SDK can't resolve). `set -a; source .env; set +a` exports every
# assignment in .env into the script env. Skipped if AWS_PROFILE is already
# set so an explicit operator override is honored.
#
# Source from cloud entry-point scripts that touch AWS (boto3 or `aws ec2 ...`).
# Resolves the project root via git so it works regardless of caller cwd.
if [[ -z "${AWS_PROFILE:-}" ]]; then
  _starsector_env_root="$(git rev-parse --show-toplevel 2>/dev/null)"
  if [[ -n "$_starsector_env_root" && -f "$_starsector_env_root/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$_starsector_env_root/.env"
    set +a
  fi
  unset _starsector_env_root
fi
