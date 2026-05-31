# Ritual Canon Engine Design

## Summary

Ritual is the next Tribal layer after Genesis and Council.

Genesis births the tribe's capacity for knowledge. Council lets the tribe deliberate and write draft folklore. Ritual decides what happens to that folklore after lived evidence arrives.

Hermes remembers sessions, skills, and useful context. Tribal should go one layer higher: it should govern which beliefs are allowed to become trusted lore, which beliefs stay provisional, and which beliefs die because the world falsified them.

The first release is intentionally small: a local, file-backed canon engine for reviewing folklore, attaching outcomes, preserving falsifiers, and promoting or demoting lemmas without auto-canon.

## Product Thesis

The killer Tribal loop is:

```text
ask -> council -> draft folklore -> live outcome -> ritual review -> canon / folklore / falsified / stale
```

This makes Tribal feel less like a chat memory product and more like an operating system for earned judgment. The tribe does not merely remember what it said. It remembers what survived contact with reality.

## User-Facing Commands

### `tribal lore list`

Lists lemmas in the current tribe.

Options:

- `--status folklore|canon|falsified|stale|all`
- `--json`

Default behavior lists non-archived folklore and canon.

### `tribal lore show <lemma-id>`

Shows one lemma with claim, status, evidence, falsifiers, promotion state, and source council.

### `tribal lore confirm <lemma-id> --evidence "..."`

Adds confirming evidence from lived outcome. Confirmation does not automatically promote to canon.

### `tribal lore falsify <lemma-id> --evidence "..."`

Adds falsifying evidence and marks the lemma as `falsified`.

### `tribal ritual review`

Reviews current folklore and recommends one of:

- keep as folklore
- promote to canon
- falsify
- mark stale
- split into narrower lemma

V1 can run without child agents. It should be deterministic, fast, and conservative. The Keeper applies law rules and prints a status panel.

### Slash Commands

The chat runtime exposes:

```text
/lore
/lore show <lemma-id>
/outcome <lemma-id> <evidence>
/falsify <lemma-id> <evidence>
/ritual review
```

The slash commands call the same shared engine as the CLI.

## Data Model

Existing draft lemmas live in:

```text
~/.tribal/lore/lemmas.jsonl
```

Ritual appends outcome events to:

```text
~/.tribal/lore/outcomes.jsonl
```

Ritual appends review events to:

```text
~/.tribal/ritual/reviews.jsonl
```

Lineage receives:

```text
ritual.reviewed
lore.confirmed
lore.falsified
lore.promoted
lore.marked_stale
```

## Lemma Schema Extension

Council already writes folklore lemmas. Ritual extends their lifecycle without breaking existing rows.

Required fields after Ritual touches a lemma:

```json
{
  "id": "tk_12345678",
  "status": "folklore",
  "tribe_id": "personal.life",
  "claim": "Ship the demo.",
  "created_at": "2026-05-31T18:30:32Z",
  "source": {
    "type": "council",
    "council_id": "council_...",
    "role": "Keeper"
  },
  "evidence": [],
  "falsifiers": [],
  "outcomes": [],
  "confidence": "draft",
  "promotion": {
    "status": "unvalidated",
    "confirmed_count": 0,
    "falsified_count": 0,
    "last_reviewed_at": null
  }
}
```

Status values:

- `folklore`: useful but unproven.
- `canon`: promoted by ritual, never automatic.
- `falsified`: contradicted by outcome evidence.
- `stale`: too old or too context-sensitive to trust.

## Law Rules

Ritual reads `~/.tribal/law.yaml`.

V1 law stays conservative:

- `canon.auto_promote: false`
- Canon requires explicit ritual review.
- Falsifying evidence can demote immediately.
- One council can create folklore, but cannot create canon.
- A newborn tribe cannot have elder authority without accumulated confirmed outcomes.

Recommended law additions:

```yaml
ritual:
  canon_min_confirmations: 2
  canon_requires_no_open_falsifiers: true
  stale_after_days: 45
  max_promotions_per_review: 3
```

If the fields are missing, Ritual uses these defaults in memory and repairs `law.yaml` only through a safe schema repair pass.

## Architecture

### Shared Engine

Create `tribal_cli/ritual.py`.

Responsibilities:

- load birth, tribe, law, lemmas, outcomes, and lineage
- list and show lore
- append outcome evidence
- apply falsification
- compute review recommendations
- rewrite `lemmas.jsonl` atomically when statuses change
- append ritual and lineage events
- render plain text and JSON

The module should not depend on the interactive agent. This keeps tests fast and makes CLI and slash commands share behavior.

### CLI Wiring

Add subcommands in `tribal_cli/main.py`:

```text
tribal lore list
tribal lore show <id>
tribal lore confirm <id> --evidence "..."
tribal lore falsify <id> --evidence "..."
tribal ritual review
```

### Slash Wiring

Register commands in `tribal_cli/commands.py` under the `Tribal` category:

```text
/lore
/outcome
/falsify
/ritual review
```

Dispatch through the main slash command handler and call the shared engine.

## Review Algorithm

For each folklore lemma:

1. Load outcome counts.
2. If falsifying outcome exists, recommend `falsify`.
3. If stale by law age, recommend `mark_stale`.
4. If confirmation count meets law threshold and no open falsifiers have been triggered, recommend `promote_to_canon`.
5. Otherwise recommend `keep_folklore`.

The review output should explain why in plain language.

Example:

```text
Lemma: tk_ab12cd34
Claim: Ship the demo.
Recommendation: keep_folklore
Reason: 1 confirmation, 0 falsifications. Canon requires 2 confirmations.
Open falsifier: If the council is solving the wrong problem, weaken or revise this council decision.
```

## Error Handling

- If Genesis is missing: print `Run tribal genesis first.`
- If lemma ID is unknown: return a clear not-found message.
- If evidence is empty: reject the command.
- If `lemmas.jsonl` contains malformed lines: ignore malformed rows for list/review and preserve the raw malformed lines unchanged when rewriting valid lemmas.
- If a write fails: do not partially update lineage or outcomes. Write the core lore change first through a temp file, then append events.
- If `law.yaml` is missing: recreate defaults like Council already does.

## Testing

Unit tests:

- lore list filters by status
- lore show returns one lemma
- confirm appends outcome and increments promotion counters
- falsify appends outcome and marks lemma `falsified`
- ritual review recommends keep/promote/falsify/stale correctly
- auto-canon never happens on confirm alone
- missing Genesis refuses
- malformed JSONL does not crash list/review

CLI tests:

- `uv run tribal lore list --json`
- `uv run tribal lore show <id> --json`
- `uv run tribal lore confirm <id> --evidence "..."`
- `uv run tribal lore falsify <id> --evidence "..."`
- `uv run tribal ritual review --json`

Slash tests:

- `/lore` lists lemmas
- `/outcome <id> <evidence>` confirms a lemma
- `/falsify <id> <evidence>` falsifies a lemma
- `/ritual review` calls the shared review engine

Regression checks:

```text
scripts/run_tests.sh tests/tribal_cli tests/cli
/Users/term_/.local/bin/uv run tribal lore list
/Users/term_/.local/bin/uv run tribal ritual review
```

## Non-Goals For V1

- No MiroFish simulation execution.
- No automatic canon promotion.
- No cloud sync.
- No dashboard UI.
- No multi-human elder reputation system.
- No irreversible deletion of lore.

## Success Criteria

The feature is successful when a user can:

1. Ask the tribe a question and get a draft folklore lemma.
2. Later record what happened in real life.
3. Run Ritual.
4. See the lemma remain folklore, become canon, become stale, or get falsified.

The key user-facing sentence should become:

```text
Tribal does not just remember the council's answer. It runs rituals to decide whether the answer deserves to become lore.
```
