# Contributing

Use Python 3.12. Do not hand-edit the generated tool reference or copy operation schemas into
source code; `paperclip-openapi.json` is the interface source of truth.

```bash
scripts/bootstrap.sh
scripts/validate.sh
```

Changes to the OpenAPI document must regenerate `docs/generated/mcp-tools.md` and preserve a
one-to-one mapping between every method/path pair and a direct `pc_*` MCP tool. Keep the
inbound MCP credential separate from the optional upstream Paperclip credential, and never
add arbitrary URL, header, cookie, or authorization overrides to tool input.

Commits should have an imperative subject, a blank line, and a body recording the design
impact, exact validation, and any unavailable live Paperclip or container evidence.
