# Ritual Canon Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Ritual v1 so Tribal can list lore, attach real-world outcomes, falsify folklore, and review whether folklore should stay draft, become canon, become stale, or die.

**Architecture:** Add a focused `tribal_cli/ritual.py` shared engine with no live-agent dependency. Wire it into CLI subcommands, slash commands, and tests. Keep persistence local and file-backed under `~/.tribal`.

**Tech Stack:** Python 3.12, argparse, JSONL files, YAML law config, pytest, existing Tribal CLI patterns.

---

## File Structure

- Create `tribal_cli/ritual.py`: shared lore/ritual engine, JSON/plain rendering, CLI command handlers, slash handlers.
- Create `tests/tribal_cli/test_ritual.py`: unit tests for engine behavior and renderers.
- Create `tests/cli/test_ritual_slash.py`: slash command dispatch tests.
- Modify `tribal_cli/main.py`: add `lore` and `ritual` top-level CLI commands.
- Modify `tribal_cli/commands.py`: register `/lore`, `/outcome`, `/falsify`, and `/ritual`.
- Modify `cli.py`: dispatch slash commands to `tribal_cli.ritual`.
- Modify `README.md`: add Ritual commands to the Tribal command list.

## Task 1: Shared Ritual Engine

**Files:**
- Create: `tribal_cli/ritual.py`
- Test: `tests/tribal_cli/test_ritual.py`

- [ ] **Step 1: Write failing engine tests**

Add tests that birth a temp tribe, seed `lore/lemmas.jsonl`, and verify:

```python
def test_lore_list_filters_by_status(tmp_path): ...
def test_confirm_appends_outcome_without_auto_canon(tmp_path): ...
def test_falsify_marks_lemma_falsified(tmp_path): ...
def test_ritual_review_recommends_keep_promote_falsify_and_stale(tmp_path): ...
def test_missing_genesis_refuses(tmp_path): ...
def test_malformed_lemma_rows_are_preserved_on_rewrite(tmp_path): ...
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/Users/term_/.local/bin/uv run pytest tests/tribal_cli/test_ritual.py
```

Expected: fails because `tribal_cli.ritual` does not exist.

- [ ] **Step 3: Implement `tribal_cli/ritual.py`**

Add:

```python
class RitualError(RuntimeError): ...
class RitualNotBornError(RitualError): ...
@dataclass class RitualResult: ...
def run_lore_list(...): ...
def run_lore_show(...): ...
def run_lore_confirm(...): ...
def run_lore_falsify(...): ...
def run_ritual_review(...): ...
def render_ritual_result(...): ...
def cmd_lore(args): ...
def cmd_ritual(args): ...
def handle_lore_slash_command(command): ...
def handle_outcome_slash_command(command): ...
def handle_falsify_slash_command(command): ...
def handle_ritual_slash_command(command): ...
```

Use `genesis.DEFAULT_LAW` defaults. Preserve malformed JSONL raw lines when rewriting valid lemmas.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
/Users/term_/.local/bin/uv run pytest tests/tribal_cli/test_ritual.py
```

Expected: all tests pass.

## Task 2: CLI Commands

**Files:**
- Modify: `tribal_cli/main.py`
- Test: `tests/tribal_cli/test_ritual.py`

- [ ] **Step 1: Add failing CLI tests**

Add tests that call `cmd_lore` and `cmd_ritual` with `argparse.Namespace` and verify:

```python
def test_cmd_lore_list_json(...): ...
def test_cmd_ritual_review_json(...): ...
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/Users/term_/.local/bin/uv run pytest tests/tribal_cli/test_ritual.py -k "cmd_"
```

Expected: fails before command handlers or parser wiring are present.

- [ ] **Step 3: Wire `lore` and `ritual` in `tribal_cli/main.py`**

Add top-level command functions:

```python
def cmd_lore(args): ...
def cmd_ritual(args): ...
```

Add parsers:

```text
tribal lore list [--status ...] [--json]
tribal lore show <lemma_id> [--json]
tribal lore confirm <lemma_id> --evidence "..." [--json]
tribal lore falsify <lemma_id> --evidence "..." [--json]
tribal ritual review [--json]
```

- [ ] **Step 4: Run CLI smoke checks**

Run:

```bash
/Users/term_/.local/bin/uv run tribal lore --help
/Users/term_/.local/bin/uv run tribal ritual --help
```

Expected: both commands show help.

## Task 3: Slash Commands

**Files:**
- Modify: `tribal_cli/commands.py`
- Modify: `cli.py`
- Test: `tests/cli/test_ritual_slash.py`

- [ ] **Step 1: Write failing slash tests**

Add tests mirroring `tests/cli/test_tribe_slash.py`:

```python
def test_slash_lore_calls_shared_engine(...): ...
def test_slash_outcome_calls_shared_engine(...): ...
def test_slash_falsify_calls_shared_engine(...): ...
def test_slash_ritual_calls_shared_engine(...): ...
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/Users/term_/.local/bin/uv run pytest tests/cli/test_ritual_slash.py
```

Expected: fails because slash commands are not registered/dispatched.

- [ ] **Step 3: Register and dispatch slash commands**

Add `CommandDef` entries under the `Tribal` category for `lore`, `outcome`, `falsify`, and `ritual`.

In `TribalCLI.process_command`, dispatch canonical commands to `tribal_cli.ritual` handlers and print returned text.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
/Users/term_/.local/bin/uv run pytest tests/cli/test_ritual_slash.py
```

Expected: all slash tests pass.

## Task 4: Documentation and Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README command list**

Add:

```text
tribal lore list
tribal lore show <id>
tribal lore confirm <id> --evidence "..."
tribal lore falsify <id> --evidence "..."
tribal ritual review
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
scripts/run_tests.sh tests/tribal_cli/test_ritual.py tests/cli/test_ritual_slash.py tests/tribal_cli/test_tribe.py tests/cli/test_tribe_slash.py tests/tribal_cli/test_commands.py
```

Expected: all pass.

- [ ] **Step 3: Run lint**

Run:

```bash
/Users/term_/.local/bin/uv tool run ruff check tribal_cli/ritual.py tribal_cli/main.py tribal_cli/commands.py cli.py tests/tribal_cli/test_ritual.py tests/cli/test_ritual_slash.py
```

Expected: all checks pass.

- [ ] **Step 4: Run smoke commands**

Run:

```bash
/Users/term_/.local/bin/uv run tribal lore --help
/Users/term_/.local/bin/uv run tribal ritual --help
/Users/term_/.local/bin/uv run tribal lore list
/Users/term_/.local/bin/uv run tribal ritual review
```

Expected: help commands succeed; list/review work against current `~/.tribal`.

- [ ] **Step 5: Commit and push**

Run:

```bash
git add README.md cli.py tribal_cli/commands.py tribal_cli/main.py tribal_cli/ritual.py tests/cli/test_ritual_slash.py tests/tribal_cli/test_ritual.py docs/superpowers/plans/2026-05-31-ritual-canon-engine.md
git commit -m "feat: add ritual canon engine"
git push origin codex/ritual-canon-engine
```
