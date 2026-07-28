# MCP clients

## Streamable HTTP

Run `paperclip-mcp http` and connect to:

```text
http://127.0.0.1:8000/mcp
```

When `PAPERCLIP_MCP_TOKEN` or `PAPERCLIP_MCP_TOKEN_FILE` is configured, every MCP request
must have `Authorization: Bearer <gateway token>`. Health and readiness probes remain
unauthenticated and never reveal credentials.

Example client configuration:

```json
{
  "mcpServers": {
    "paperclip": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer ${PAPERCLIP_MCP_TOKEN}"
      }
    }
  }
}
```

Use the variable-substitution syntax supported by your client; do not commit a literal
token. A malformed scheme, extra authorization fields, or wrong token receives HTTP 401
with `WWW-Authenticate: Bearer`.

## stdio

For a client that owns the process:

```json
{
  "mcpServers": {
    "paperclip": {
      "command": "/absolute/path/to/.venv/bin/paperclip-mcp",
      "args": ["stdio"],
      "env": {
        "PAPERCLIP_BASE_URL": "http://127.0.0.1:3100"
      }
    }
  }
}
```

Add `PAPERCLIP_API_TOKEN_FILE` for authenticated Paperclip. Omit both upstream token
settings for Paperclip `local_mode`. Stdio does not use the inbound gateway token because
there is no listening network endpoint.

## Choosing tools

Use `paperclip_list_operations` with `tag` or `search` to discover an exact operation. Call
the returned direct `pc_*` tool for the strongest input schema, or pass the stable operation
key to `paperclip_call_operation`. Direct tools and key-based dispatch reach the same
allowlisted implementation.

Review a tool's `readOnlyHint` and `destructiveHint`, but do not treat annotations as an
approval system. Paperclip authorization and workflow controls remain authoritative.
