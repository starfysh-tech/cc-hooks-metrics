# Security

`cc-hooks-metrics` is a telemetry + guardrails toolkit for Claude Code. It runs
inside an authenticated developer's session and inserts rows into a local SQLite
DB. This document maps the controls it ships against the **OWASP Top 10 for
Agentic Applications (2026)** — see the OWASP GenAI Security Project at
[genai.owasp.org](https://genai.owasp.org/) for the canonical risk catalog.

## References

- **OWASP GenAI Security Project** — [genai.owasp.org](https://genai.owasp.org/) — the parent project that publishes the Agentic Top 10 and the LLM Applications Top 10.
- **Claude Code CVEs addressed by the floor in `version-requirements`:**
  - [CVE-2025-59536](https://nvd.nist.gov/vuln/detail/CVE-2025-59536) (CVSS 8.7) — `.claude/settings.json` / `.mcp.json` code injection (fixed in Claude Code 1.0.111).
  - [CVE-2026-21852](https://nvd.nist.gov/vuln/detail/CVE-2026-21852) (CVSS 5.3) — `apiUrl` override exfiltrates the auth header (fixed in Claude Code 2.0.65).

## Reporting a vulnerability

Open a GitHub issue tagged `security`, or email the maintainer directly. Do
**not** include credentials, full audit logs, or tokens in the report.

## OWASP Agentic Top 10 (2026) — coverage

| ASI | Risk | Coverage | Where |
|-----|------|----------|-------|
| **ASI01** Goal Hijack | Prompt-injection in retrieved content | Out of scope (model-side) — `guard-security.py` catches the obvious destructive commands the agent might be talked into running, but cannot defend against semantic injection. | `guardrails/guard-security.py` |
| **ASI02** Tool Misuse | Over-permissioned destructive ops | Predicate-based denylist: `terraform destroy` (un-targeted), `aws … delete-*` (with safe-list), `kubectl delete (ns|pv) *prod*`, `dropdb`, `git push --force … main/master`, `curl|bash`, `rm -rf /`, `mkfs.*`, `dd of=/dev/*`, `chmod 777 /`. Each rule is a small named function in `guardrails/_patterns.py` — adding a new flag form is one branch, not a regex. Escape hatch: `GUARD_SECURITY_ALLOW=rule1,rule2`. | `guardrails/guard-security.py`, `guardrails/_patterns.py` |
| **ASI03** Identity / Privilege Abuse | Credential overreach, env hijack | OTLP endpoint allow-list via `HOOKS_METRICS_OTLP_ALLOWED_HOSTS` (env unset = off; set = enforce; all-empty = deny). CRLF guard on `HOOKS_METRICS_OTLP_HEADERS` rejects header smuggling. **Pin `ANTHROPIC_BASE_URL` in `/etc/claude-code/managed-settings.json`** — defends against [CVE-2026-21852](https://nvd.nist.gov/vuln/detail/CVE-2026-21852) redirecting your auth header. | `hooks_report/otlp.py` |
| **ASI04** Supply Chain | Malicious project config / .mcp.json | `guard-incoming-config.py` (SessionStart, opt-in) walks JSON for `apiUrl`/`ANTHROPIC_BASE_URL` overrides, `enableAllProjectMcpServers: true`, and pipe-to-shell in hook commands. Reuses the same `blocks_pipe_to_shell` predicate as ASI02 — single source of truth. **Min Claude Code version 2.1.50** ([CVE-2025-59536](https://nvd.nist.gov/vuln/detail/CVE-2025-59536) fixed in 1.0.111, [CVE-2026-21852](https://nvd.nist.gov/vuln/detail/CVE-2026-21852) in 2.0.65 — both already exceeded). | `guardrails/guard-incoming-config.py`, `version-requirements`, `install.sh` |
| **ASI05** Unexpected Code Execution | Agent-generated code runs without review | Out of scope (this is the developer's review discipline). The guardrails reduce blast radius via ASI02 deny rules. | — |
| **ASI06** Memory / Context Poisoning | Persistent CLAUDE.md / shared-context drift | Out of scope today. Future work: hash-pinned shared-context verification. | — |
| **ASI07** Excessive Permissions | Auto-approve fatigue | The shipped `settings-example.json` does NOT enable `--dangerously-skip-permissions`. `guard-auto-allow.py` only allows known read-only operations. | `guardrails/guard-auto-allow.py` |
| **ASI08** Insufficient Observability | No record of what the agent did | All tool calls flow through `audit-logger.sh` → `audit_events` table. Reports surface destructive blocks, force-pushes, and step-level performance via `hooks-report.sh`. | `audit-logger.sh`, `hooks_report/` |
| **ASI09** Human-Agent Trust Exploit | Rubber-stamping confident wrong actions | `guard-security.py` blocks irreversible operations outright (exit 2), so there is no "approve all" prompt to fatigue through. `guard-incoming-config.py` blocks at SessionStart before any tool fires. | `guardrails/guard-security.py`, `guardrails/guard-incoming-config.py` |
| **ASI10** Rogue Agents | Drift over long sessions, can't kill | Tamper-evident audit chain (opt-in via `HOOKS_AUDIT_CHAIN=1`): each `audit_events` row stores `prev_hash` and `row_hash = sha256(prev || ts || session || tool || input)`. Atomic insert via `BEGIN IMMEDIATE` so concurrent writers serialize. Verifier: `hooks-report.sh --verify-audit-chain` — exit 1 + first divergence on tamper. Kill-switch documented in [RUNBOOK.md](RUNBOOK.md). | `hooks_report/audit_chain.py`, `audit-logger.sh`, `db-init.sh` |

Legend: ✅ shipped · ⚠️ partial / opt-in · ❌ out of scope

## What these guardrails do **not** close

Honest limits — this list matters more than the table above.

- **Determined adversary.** Predicates are pattern-matchers over `shlex` tokens. An attacker who reads `_patterns.py` can paraphrase around any rule (process substitution, `bash -c`, base64-decoded payloads, etc.). Goal is catching accidental destruction and unsophisticated supply-chain hits, not adversarial evasion.
- **Compromised allowed endpoint.** The OTLP host allow-list defends against env-hijack of `HOOKS_METRICS_OTLP_ENDPOINT`. It does **not** defend against the maintainer of an allowlisted endpoint being compromised.
- **Audit chain is detection, not prevention.** Anyone with write access to `~/.claude/hooks.db` can re-chain from a forged anchor. `--verify-audit-chain` only proves the chain is internally consistent. For unforgeable proof, ship `row_hash` to a separate write-once log.
- **Pruning vs chain.** When `HOOKS_AUDIT_CHAIN=1`, `audit_events` pruning is skipped — the table grows append-only. Plan an out-of-band archive policy (see [RUNBOOK.md](RUNBOOK.md)).
- **Model-level prompt injection (ASI01).** Hooks see commands and file paths, not the model's reasoning. Hidden instructions in retrieved content can still hijack the agent's intent.
- **CLAUDE.md / shared-context poisoning (ASI06).** Not addressed in this release.

## Recommended deployment posture

For high-stakes environments, layer these on top of the shipped defaults:

1. **Pin `ANTHROPIC_BASE_URL` and `HOOKS_METRICS_OTLP_ENDPOINT` in `/etc/claude-code/managed-settings.json`** — managed settings cannot be overridden by a project's `.claude/settings.json`.
2. **Set `HOOKS_METRICS_OTLP_ALLOWED_HOSTS` to your OTel collector hostname** — turns the env-hijack class into "POST refused" rather than "auth header exfiltrated".
3. **Wire `guard-incoming-config` into SessionStart** (`settings-guardrails-example.json`) before cloning third-party repos. Opt-in by design — small startup cost per session.
4. **Set `HOOKS_AUDIT_CHAIN=1`** in your shell rc and run `hooks-report.sh --verify-audit-chain` on a schedule. Investigate every break.
5. **Document the kill switch** — see [RUNBOOK.md](RUNBOOK.md) and run the drill at least quarterly.

## Further reading

- [OWASP GenAI Security Project](https://genai.owasp.org/) — Agentic Top 10 and LLM Applications Top 10 risk catalogs.
- [OWASP Top 10](https://owasp.org/www-project-top-ten/) — the original web-application risk list, still relevant for the OTLP exporter and any HTTP surface this tool touches.
- [Claude Code documentation](https://docs.claude.com/en/docs/claude-code/overview) — settings, hooks, managed settings, MCP servers.
