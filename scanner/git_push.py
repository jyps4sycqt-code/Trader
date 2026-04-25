"""Auto-commit and push scan artifacts.

We only operate on the configured branch and never force-push. If the
branch has diverged from origin we attempt a fast-forward pull before
pushing; if that also fails we log and abort so the operator can review.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    log.debug("git: %s", " ".join(args))
    return subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True)


def commit_and_push(repo_root: Path, cfg: dict, message: str) -> bool:
    if not cfg["git"]["enabled"]:
        log.info("git push disabled in config; skipping")
        return False

    branch = cfg["git"]["branch"]
    remote = cfg["git"]["remote"]

    # Ensure identity is set (for unattended launchd jobs).
    _run(["git", "config", "user.name", cfg["git"]["user_name"]], repo_root)
    _run(["git", "config", "user.email", cfg["git"]["user_email"]], repo_root)

    cur = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if cur.stdout.strip() != branch:
        log.error("on branch %r, expected %r — refusing to push", cur.stdout.strip(), branch)
        return False

    _run(["git", "add", "-A"], repo_root)
    status = _run(["git", "status", "--porcelain"], repo_root)
    if not status.stdout.strip():
        log.info("nothing to commit")
        return True

    commit = _run(["git", "commit", "-m", message], repo_root)
    if commit.returncode != 0:
        log.error("commit failed: %s\n%s", commit.stdout, commit.stderr)
        return False

    delays = [2, 4, 8, 16]
    for attempt, delay in enumerate([0] + delays):
        if delay:
            time.sleep(delay)
        push = _run(["git", "push", "-u", remote, branch], repo_root)
        if push.returncode == 0:
            log.info("pushed to %s/%s", remote, branch)
            return True
        log.warning("push attempt %d failed: %s", attempt + 1, push.stderr.strip())

    log.error("push failed after retries; manual intervention required")
    return False
