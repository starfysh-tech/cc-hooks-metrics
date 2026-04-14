---
description: Process and address PR review comments from the current pull request.
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(mise run:*), mcp__github__pull_request_read, mcp__github__add_issue_comment, mcp__github__add_reply_to_pull_request_comment, mcp__github__update_pull_request
---

# PR Review

Process and address PR review comments from the current pull request.

---

## Phase 1: PR Context

### 1.1 Fetch PR Status

Use `mcp__github__pull_request_read` with method `get` to retrieve PR details (number, title, state, url, author, reviewDecision).

### 1.2 Fetch All Comments

Use three parallel calls:
- `mcp__github__pull_request_read` method `get_review_comments` — inline code review threads
- `mcp__github__pull_request_read` method `get_reviews` — formal review submissions (body text, approve/request-changes/comment state)
- `mcp__github__pull_request_read` method `get_comments` — general PR comments

### 1.3 Display Summary

Present PR overview:

| Field | Value |
|-------|-------|
| PR | #[number] |
| Title | [title] |
| State | [state] |
| Author | [author] |
| Review Status | [reviewDecision] |
| Reviews | [N] reviews ([X] with actionable feedback) |

---

## Phase 2: Comment Analysis

### 2.1 Parse Comments

For each inline review thread (`get_review_comments`), extract:
- File and line number
- Reviewer name
- Comment content
- Whether it's part of a thread

For each formal review (`get_reviews`), extract:
- Reviewer name and review state (APPROVED / CHANGES_REQUESTED / COMMENTED)
- Review body text
- **Skip** reviews with empty bodies (approval-only)
- **Skip** bot reviews that are purely auto-generated summaries (e.g. Gemini changelog); include bot reviews that contain explicit actionable feedback, issues, or questions

### 2.2 Categorize Comments

| Symbol | Category | Action Required |
|--------|----------|-----------------|
| ✓ | Actionable | Must implement - explicit code change requested |
| 🔍 | Issue/Bug | Must fix - reviewer found a problem |
| ? | Question | Need to answer or clarify intent |
| 💭 | Suggestion | Consider - may skip with justification |

### 2.3 Group by File/Topic

Organize comments by:
1. File path (group related changes)
2. Topic (if comments span multiple files)

### 2.4 Handle Ambiguity

If a comment is unclear, use `AskUserQuestion`:

> **Clarification needed for comment by @[reviewer]:**
>
> "[comment text]"
>
> How should this be interpreted?
> 1. [Interpretation A]
> 2. [Interpretation B]
> 3. Skip this comment
> 4. Ask reviewer for clarification

---

## Phase 3: Task List

### 3.1 Create Task List

Create a consolidated task list:

```
- [ ] [File: path/to/file.ts] - [Change description] (✓ actionable)
- [ ] [File: path/to/file.ts] - [Fix description] (🔍 issue)
- [ ] [Answer] - [Question summary] (? question)
- [ ] [Consider] - [Suggestion summary] (💭 suggestion)
```

Group tasks by:
1. Must fix (✓ and 🔍)
2. Questions to answer (?)
3. Suggestions to consider (💭)

### 3.2 Get Approval

Use `AskUserQuestion`:

> **Task list created with [N] items:**
>
> - [X] must fix
> - [Y] questions
> - [Z] suggestions
>
> What would you like to do?
> 1. Approve all - proceed with implementation
> 2. Review list - show full task list first
> 3. Modify scope - exclude some items
> 4. Cancel - exit without changes

If "Modify scope": allow user to specify which items to skip.

---

## Phase 4: Implementation

### 4.1 Work Through Tasks

For each task:
1. Announce which task you're working on
2. Make the required change
3. Confirm completion, then move to next task

### 4.2 Run Tests

After major changes, run: `mise run check`

If tests fail: STOP, show failures, ask how to proceed.

### 4.3 Review Before Push

Show summary of all changes made:

```
Files modified:
- path/to/file1.ts (3 changes)
- path/to/file2.ts (1 change)

Tasks completed: [X]/[Y]
Tests: [passed/failed/skipped]
```

---

## Phase 5: Finalization

### 5.1 Push Approval

Use `AskUserQuestion`:

> **Ready to push and respond to reviewers?**
>
> Changes: [N] files modified
> Tests: [status]
>
> 1. Push and respond - push changes, reply to all comments
> 2. Push only - push without responding
> 3. Review changes - show diff before pushing
> 4. Cancel - keep changes local

### 5.2 Push Changes

Use `/commitcraft push` to push changes.

### 5.3 Respond to Comments

For each addressed review thread, use `mcp__github__add_reply_to_pull_request_comment` to reply inline.

For general PR-level comments, use `mcp__github__add_issue_comment`.

Format replies as:

> **Re: [original comment summary]**
>
> [Explanation of change made]
>
> - Changed [what] to [what]
> - [Additional context if needed]

### 5.4 Request Re-Review

Use `AskUserQuestion`:

> **Request re-review?**
>
> 1. Yes - request from original reviewers
> 2. Yes, specific reviewer - specify who
> 3. No - skip re-review request

If yes, use `mcp__github__update_pull_request` to add reviewers.

### 5.5 Final Report

```
✓ PR #[number] updated

Changes pushed:
- [file1] - [summary]
- [file2] - [summary]

Comments addressed: [X]/[Y]
Re-review requested: [yes/no] ([reviewers])

URL: [pr-url]
```

---

## Approval Gates

| Gate | When | Purpose |
|------|------|---------|
| Task list approval | After Phase 3 | Confirm scope before implementation |
| Push approval | After Phase 4 | Review changes before pushing |
| Re-review prompt | After Phase 5.3 | Decide on re-review request |
