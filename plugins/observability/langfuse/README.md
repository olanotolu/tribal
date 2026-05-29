# Langfuse Observability Plugin

This plugin ships bundled with Tribal but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

```bash
pip install langfuse
tribal plugins enable observability/langfuse
```

Or check the box in the interactive `tribal plugins` UI.

## Required credentials

Set these in `~/.tribal/.env`:

```bash
TRIBAL_LANGFUSE_PUBLIC_KEY=pk-lf-...
TRIBAL_LANGFUSE_SECRET_KEY=sk-lf-...
TRIBAL_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
tribal plugins list                 # observability/langfuse should show "enabled"
tribal chat -q "hello"              # then check Langfuse for a "Tribal turn" trace
```

## Optional tuning

```bash
TRIBAL_LANGFUSE_ENV=production       # environment tag
TRIBAL_LANGFUSE_RELEASE=v1.0.0       # release tag
TRIBAL_LANGFUSE_SAMPLE_RATE=0.5      # sample 50% of traces
TRIBAL_LANGFUSE_MAX_CHARS=12000      # max chars per field (default: 12000)
TRIBAL_LANGFUSE_DEBUG=true           # verbose plugin logging
```

## Disable

```bash
tribal plugins disable observability/langfuse
```
