# Runbook

Operational procedures for `cc-hooks-metrics` in production. Companion doc to
[SECURITY.md](SECURITY.md).

## Kill switch — stopping a runaway Claude Code session

If a session is taking destructive actions, slow, or hung, the fastest way to
stop it from another shell:

```bash
pkill -f claude-code
```

The hook pipeline preserves all events written before kill — guardrail blocks
and audit rows for the killed session remain in `hooks.db`.

### Drill cadence

Run this at least **once per quarter**. The first time someone runs the drill
under pressure should not be the first time they run it.

1. From shell A, start any long-running Claude Code session.
2. From shell B, find the right PID and kill it within **30 seconds**.
3. Verify in shell A that the session terminated cleanly and the prompt returned.
4. `hooks-report.sh --sessions` should show the session in recent history.
5. If `HOOKS_AUDIT_CHAIN=1` is set, run `hooks-report.sh --verify-audit-chain`
   and confirm the chain is intact across the kill boundary.

If the drill takes more than 30 seconds, that's information you need before an
incident, not after. Common causes: shell B is in the wrong terminal multiplexer
window, `pgrep -f claude-code` returns more than one PID and the operator
hesitates, or `pkill` is shell-aliased to something interactive.

## Audit-chain performance cost

When `HOOKS_AUDIT_CHAIN=1`, every `audit-logger.sh` invocation forks a Python
process to do the atomic chained insert (`hooks_report.audit_chain --insert`).
Cold-start cost is roughly **30–80ms per audit event** vs ~5ms for the legacy
in-bash sqlite3 heredoc. For a busy session firing dozens of tool calls per
minute this is observable in `hooks-report.sh` overhead.

Tradeoff: the chain feature requires atomic SELECT-then-INSERT, which is hard
to do correctly from bash without a Python (or other) helper. If the cost is
prohibitive in your environment, leave `HOOKS_AUDIT_CHAIN` unset and accept
that audit rows are not tamper-evident.

## Audit-chain verification

When `HOOKS_AUDIT_CHAIN=1` is set in the environment that runs `audit-logger.sh`,
each row of `audit_events` is hash-chained:

```
row_hash = sha256(prev_hash || ts || session || tool || input)
```

### Periodic verification

```bash
hooks-report.sh --verify-audit-chain
echo "exit=$?"
```

- **exit 0** + `audit-chain: OK` — chain is internally consistent.
- **exit 0** + `pre-migration schema` note — `audit_events` lacks chain columns
  (you have not enabled chaining, or the DB was created before this feature).
- **exit 1** — at least one chain break. Output identifies the row id and shows
  expected vs observed hash.

### Investigating a chain break

A break means one of:

1. **Tampering** — `prev_hash`, `row_hash`, or any input field was modified
   after insert.
2. **Pre-migration legacy row** — an `audit_events` row exists with empty
   `row_hash`. The next chained insert writes a `CHAIN_BREAK_<ts>` sentinel as
   its `prev_hash` so the boundary is visible.
3. **Direct INSERT bypassing the helper** — anything that wrote to
   `audit_events` without going through `hooks_report.audit_chain.insert_chained`
   (e.g. an old `audit-logger.sh` deployment, a test fixture) skips the hash.

Walk the table to find the divergence:

```bash
sqlite3 ~/.claude/hooks.db "
SELECT id, ts, tool, length(prev_hash), length(row_hash)
FROM audit_events ORDER BY id;
"
```

If you trust everything before the break, you can reset the chain by inserting
a fresh genesis row through the helper (next chained insert will compute against
the new tail) — but **document the boundary** in this file with date + reason.

## Audit-events archive policy

When `HOOKS_AUDIT_CHAIN=1`, `_maybe_prune_hooks_db` skips pruning of
`audit_events` (pruning would create unverifiable hash discontinuities). The
table grows append-only. `hook_metrics` is still pruned at 30 days as before.

### When to archive

- Table grows past ~1M rows or ~500MB on disk — roughly 6–12 months of normal
  use depending on hook activity.
- `sqlite3 ~/.claude/hooks.db "SELECT COUNT(*), MIN(ts), MAX(ts) FROM audit_events"`
  shows a date range you no longer need online.

### Archive procedure

1. Verify the chain is clean: `hooks-report.sh --verify-audit-chain`. **Do not
   archive a broken chain** — investigate first.
2. Pick a cutoff `id`. Note its `row_hash` — this becomes the anchor of the
   archive.
3. Export the rows to be archived:
   ```bash
   sqlite3 ~/.claude/hooks.db ".dump audit_events" \
     | sqlite3 audit_archive_$(date +%Y%m%d).db
   ```
4. Keep the archive in write-once storage (S3 Object Lock, append-only EBS,
   etc.).
5. Delete the archived rows from the live DB. The next chained insert will
   detect the empty tail and write a `CHAIN_BREAK_<ts>` sentinel — this is
   expected and visible to the verifier.

## Incident: OTLP allow-list rejecting valid endpoints

Symptom: `warn: otlp: endpoint host '<host>' not in HOOKS_METRICS_OTLP_ALLOWED_HOSTS`.

1. Confirm the endpoint is the one you intended:
   `echo "$HOOKS_METRICS_OTLP_ENDPOINT"`.
2. Confirm the allow-list:
   `echo "$HOOKS_METRICS_OTLP_ALLOWED_HOSTS"`.
3. Allow-list entries must be **bare hostnames**, lowercase, no port, no scheme.
   `otel.local:4318` is wrong. `otel.local` is right.
4. Trailing comma → empty entry → **deny all** with stderr warning. Strip
   trailing/leading commas.

## Updating Claude Code minimum version

`version-requirements` enforces `MIN_CC_VERSION=2.1.50` to keep users above the
known CVE fix versions:

- **CVE-2025-59536** (CVSS 8.7) — fixed in 1.0.111
- **CVE-2026-21852** (CVSS 5.3) — fixed in 2.0.65

Do not relax `MIN_CC_VERSION` below `2.0.65` without re-evaluating both CVEs.
When a new Claude Code CVE lands, bump the floor and add a comment in
`version-requirements` referencing the CVE id.
