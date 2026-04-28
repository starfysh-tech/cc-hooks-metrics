"""Shared command-pattern detection for guardrails.

Tokenizes bash commands via shlex, then runs each enabled predicate against
the staged tokens. Rules are predicates over `list[list[str]]` (pipeline stages)
returning `(blocked: bool, reason: str)`. New dangerous flag forms become a
new branch in a predicate, not a new regex.

Limits: shlex does not parse process substitution (`<(...)`) and treats
backticks as plain text. Goal is catching accidental destruction, not
adversarial evasion. A determined attacker who reads this file can
paraphrase around it.
"""
from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable

# .env path regex — kept as regex because it's a path-substring check, not
# a command-structure check. Allows .env.{sample,example,template,test}.
ENV_PATTERN = re.compile(r"\.env\b(?!\.(sample|example|template|test))")

# Connectors that split a bash line into independently-executed stages.
# `|` is included so pipe-to-shell rules can inspect adjacent stages.
_CONNECTOR_RE = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")

# AWS subcommands whose `delete-*` form is common in dev/test workflows.
# Members of this set are NOT blocked even though they match the broader
# "aws … delete-*" pattern.
_AWS_DELETE_SAFELIST = {
    ("s3api", "delete-object"),
    ("s3api", "delete-objects"),
    ("s3", "rm"),
    ("logs", "delete-log-stream"),
}


def tokenize(command: str) -> list[list[str]]:
    """Split a bash command on connectors, then shlex-tokenize each stage.

    Stages where shlex fails (unbalanced quotes, etc.) become a single-token
    list with the raw segment — predicates see something rather than nothing.
    """
    stages: list[list[str]] = []
    for segment in _CONNECTOR_RE.split(command):
        seg = segment.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            tokens = [seg]
        if tokens:
            stages.append(tokens)
    return stages


# ── Predicates ────────────────────────────────────────────────────────────────
# Each predicate inspects all stages and returns (blocked, reason). Reasons
# are user-facing — keep them short and actionable.


_SUDO_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# sudo flags that consume the following token as their value. Per sudo(8):
# -A/--askpass and -B/--bell are boolean toggles, NOT valued — including them
# here would consume the actual command. -R/--chroot was missing and lets
# `sudo -R /tmp bash` evade detection.
_SUDO_VALUED_FLAGS = {
    "-u", "--user", "-g", "--group", "-D", "--chdir", "-C", "--close-from",
    "-r", "--role", "-t", "--type", "-T", "--command-timeout",
    "-p", "--prompt", "-h", "--host", "-U", "--other-user",
    "-R", "--chroot",
}


def _effective_command(stage: list[str]) -> str:
    """Return the effective command name, skipping a leading `sudo` along with
    any sudo flags (`-E`, `-u user`, `--user=root`, etc.) AND env assignments
    (`FOO=1 cmd`).

    `sudo curl …`, `sudo -E bash`, `sudo -u root bash`, `sudo --user=root bash`,
    `sudo FOO=1 bash` all return the underlying tool name.
    """
    if not stage:
        return ""
    if stage[0] != "sudo":
        return stage[0]
    skip_next = False
    for tok in stage[1:]:
        if skip_next:
            skip_next = False
            continue
        if tok in _SUDO_VALUED_FLAGS:
            skip_next = True
            continue
        if tok.startswith("-"):
            # `--user=root` keeps its value in the same token; bare `--`/`-h`/
            # `-E` etc. don't consume the next argument.
            continue
        if _SUDO_ENV_RE.match(tok):
            continue
        return tok
    return ""


def blocks_pipe_to_shell(stages: list[list[str]]) -> tuple[bool, str]:
    """curl/wget piped into bash/sh — the canonical install-script attack.

    Recognizes the fetcher and shell even when prefixed with `sudo` (flags or
    env assignments). Also detects `sh -c "curl … | bash"`-style invocations
    where the inner pipeline collapses curl into a `sh -c …` stage — but only
    when the stage IS a shell, so benign pipelines like `echo curl | bash`
    are not flagged.
    """
    fetchers = {"curl", "wget"}
    shells = {"bash", "sh", "zsh", "ksh"}
    saw_fetcher = False
    for stage in stages:
        first = _effective_command(stage)
        if first in shells and saw_fetcher:
            return True, f"pipe-to-shell: fetched script piped to {first}"
        if first in fetchers or (
            first in shells and any(t in fetchers for t in stage[1:])
        ):
            saw_fetcher = True
    return False, ""


def blocks_rm_dangerous_target(stages: list[list[str]]) -> tuple[bool, str]:
    """rm with -r/-f forces against /, ~, $HOME, *, or the home expansion."""
    targets = {"/", "~", "$HOME", "*", os.path.expanduser("~")}
    for stage in stages:
        if not stage or stage[0] != "rm":
            continue
        flags = [t for t in stage[1:] if t.startswith("-")]
        forces = any("r" in f or "f" in f for f in flags)
        if not forces:
            continue
        for arg in stage[1:]:
            if arg.startswith("-"):
                continue
            if arg in targets:
                return True, f"rm -rf on dangerous target: {arg}"
    return False, ""


def blocks_rm_non_tmp(stages: list[list[str]]) -> tuple[bool, str]:
    """Soft-block: rm against any path not under /tmp — requires confirmation.

    Hard catastrophic targets are already caught by `blocks_rm_dangerous_target`.
    This catches the long tail of specific-path `rm` commands (e.g.
    ``rm -f docs/foo.md``, ``rm -f ~/.claude/plans/old.md``) that previously
    fell through silently when ``skipDangerousModePermissionPrompt: true`` is
    set in settings.json (auto mode).

    Pairs with `evaluate_command_soft` and the soft-block branch in
    ``guard-security.py``, which surfaces Claude Code's confirmation prompt by
    emitting ``permissionDecision: "ask"`` in JSON ``hookSpecificOutput``
    rather than refusing outright. Plain ``exit 1`` is treated as a
    non-blocking warning by the harness, so the JSON contract is required.
    """
    for stage in stages:
        if not stage or stage[0] != "rm":
            continue
        for arg in stage[1:]:
            if arg.startswith("-"):
                continue
            norm = os.path.normpath(arg) if arg.startswith("/") else arg
            if norm == "/tmp" or norm.startswith("/tmp/"):
                continue
            return True, f"rm on non-/tmp path: {arg}"
    return False, ""


def blocks_sudo_rm(stages: list[list[str]]) -> tuple[bool, str]:
    """`sudo rm` in any stage — even on a single file."""
    for stage in stages:
        if len(stage) >= 2 and stage[0] == "sudo" and stage[1] == "rm":
            return True, "sudo rm"
    return False, ""


def blocks_mkfs(stages: list[list[str]]) -> tuple[bool, str]:
    """mkfs.* — filesystem format."""
    for stage in stages:
        if stage and stage[0].startswith("mkfs."):
            return True, f"mkfs format command: {stage[0]}"
    return False, ""


def blocks_dd_to_device(stages: list[list[str]]) -> tuple[bool, str]:
    """dd writing to a /dev/* device."""
    for stage in stages:
        if not stage or stage[0] != "dd":
            continue
        for arg in stage[1:]:
            if arg.startswith("of=/dev/"):
                return True, f"dd raw device write: {arg}"
    return False, ""


def blocks_chmod_777_root(stages: list[list[str]]) -> tuple[bool, str]:
    """chmod 777 / — every-mode-on-root. Also catches `chmod -R 777 /` etc."""
    for stage in stages:
        if not stage or stage[0] != "chmod":
            continue
        args = stage[1:]
        if "777" in args and "/" in args:
            return True, "chmod 777 /"
    return False, ""


def blocks_redirect_to_etc(stages: list[list[str]]) -> tuple[bool, str]:
    """`>`/`>>` redirection into /etc/, including quoted paths."""
    for stage in stages:
        joined = " ".join(stage)
        if re.search(r">+\s*['\"]?/etc/", joined):
            return True, "redirect into /etc/"
    return False, ""


def blocks_terraform_destroy(stages: list[list[str]]) -> tuple[bool, str]:
    """`terraform destroy` without `-target=` — the DataTalks.Club case."""
    for stage in stages:
        if stage[:2] != ["terraform", "destroy"]:
            continue
        targeted = any(
            t == "-target" or t.startswith("-target=") for t in stage[2:]
        )
        if not targeted:
            return True, "terraform destroy without -target"
    return False, ""


def blocks_aws_delete_dangerous(stages: list[list[str]]) -> tuple[bool, str]:
    """`aws SVC delete-*` excluding common dev-safe subcommands.

    Scans all adjacent (token, next) pairs after the `aws` argv0 so global
    options like `aws --region us-east-1 rds delete-db-cluster` and
    `aws --profile prod ec2 delete-snapshot` are still detected.
    """
    for stage in stages:
        if len(stage) < 3 or stage[0] != "aws":
            continue
        for service, sub in zip(stage[1:], stage[2:]):
            if service.startswith("-"):
                continue
            if not (sub.startswith("delete-") or sub == "rm"):
                continue
            if (service, sub) in _AWS_DELETE_SAFELIST:
                continue
            return True, f"aws {service} {sub}"
    return False, ""


def blocks_kubectl_delete_prod(stages: list[list[str]]) -> tuple[bool, str]:
    """`kubectl delete (ns|namespace|pv|pvc) <name-with-prod>`."""
    resources = {"ns", "namespace", "pv", "pvc"}
    for stage in stages:
        if (
            len(stage) >= 4
            and stage[0] == "kubectl"
            and stage[1] == "delete"
            and stage[2] in resources
        ):
            target = stage[3].lower()
            if "prod" in target:
                return True, f"kubectl delete {stage[2]} {stage[3]}"
    return False, ""


def blocks_dropdb(stages: list[list[str]]) -> tuple[bool, str]:
    """`dropdb` — Postgres database deletion."""
    for stage in stages:
        if stage and stage[0] == "dropdb":
            return True, "dropdb"
    return False, ""


def blocks_force_push_main(stages: list[list[str]]) -> tuple[bool, str]:
    """`git push` force-targeting main/master via `--force`, `-f`,
    `--force-with-lease`, or a `+`-prefixed refspec.

    The `+ref` form is itself a force mechanism — detected even when no
    explicit force flag is present (e.g., `git push origin +main`).
    """
    protected = {"main", "master"}
    for stage in stages:
        if stage[:2] != ["git", "push"]:
            continue
        force = any(
            t in {"--force", "-f"} or t.startswith("--force-with-lease")
            for t in stage[2:]
        )
        for arg in stage[2:]:
            plus = arg.startswith("+")
            ref = arg[1:] if plus else arg
            on_protected = (
                ref in protected
                or ref.endswith(":main") or ref.endswith(":master")
            )
            if plus and on_protected:
                return True, "git force-push to main/master (+ref)"
            if force and on_protected:
                return True, "git force-push to main/master"
    return False, ""


# ── Registry ──────────────────────────────────────────────────────────────────

RuleFn = Callable[[list[list[str]]], tuple[bool, str]]

# Insertion order is the contract: first matching rule wins. Order rules by
# specificity (most specific first) so the reported reason is the most useful.
RULES: dict[str, RuleFn] = {
    "blocks_pipe_to_shell": blocks_pipe_to_shell,
    "blocks_rm_dangerous_target": blocks_rm_dangerous_target,
    "blocks_sudo_rm": blocks_sudo_rm,
    "blocks_mkfs": blocks_mkfs,
    "blocks_dd_to_device": blocks_dd_to_device,
    "blocks_chmod_777_root": blocks_chmod_777_root,
    "blocks_redirect_to_etc": blocks_redirect_to_etc,
    "blocks_terraform_destroy": blocks_terraform_destroy,
    "blocks_aws_delete_dangerous": blocks_aws_delete_dangerous,
    "blocks_kubectl_delete_prod": blocks_kubectl_delete_prod,
    "blocks_dropdb": blocks_dropdb,
    "blocks_force_push_main": blocks_force_push_main,
}


def _allowed_rules() -> set[str]:
    """Names listed in $GUARD_SECURITY_ALLOW are skipped at evaluation time."""
    raw = os.environ.get("GUARD_SECURITY_ALLOW", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def evaluate_command(command: str) -> tuple[bool, str]:
    """Run all enabled rules against a command. Returns (blocked, reason).

    `.env` access via Bash is checked separately by callers using
    ENV_PATTERN — it's a path check, not a structural rule.
    """
    stages = tokenize(command)
    skip = _allowed_rules()
    for name, rule in RULES.items():
        if name in skip:
            continue
        blocked, reason = rule(stages)
        if blocked:
            return True, reason
    return False, ""


# Soft-block rules: not catastrophic, but consequential enough that auto-mode
# should never run them silently. The caller emits JSON
# ``permissionDecision: "ask"`` on match (see guard-security.py) to surface
# Claude Code's confirmation prompt — not a hard refusal like RULES above.
SOFT_RULES: dict[str, RuleFn] = {
    "blocks_rm_non_tmp": blocks_rm_non_tmp,
}


def evaluate_command_soft(command: str) -> tuple[bool, str]:
    """Run soft rules. Returns (match, reason). Same skip-list semantics as
    `evaluate_command` — names in ``$GUARD_SECURITY_ALLOW`` are bypassed."""
    stages = tokenize(command)
    skip = _allowed_rules()
    for name, rule in SOFT_RULES.items():
        if name in skip:
            continue
        match, reason = rule(stages)
        if match:
            return True, reason
    return False, ""
