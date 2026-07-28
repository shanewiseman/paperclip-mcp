# Operations

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `PAPERCLIP_BASE_URL` | Absolute upstream origin or path prefix | `http://127.0.0.1:3100` |
| `PAPERCLIP_API_TOKEN[_FILE]` | Optional upstream bearer; omit for `local_mode` | unset |
| `PAPERCLIP_MCP_TOKEN[_FILE]` | Inbound HTTP bearer | unset on loopback |
| `PAPERCLIP_MCP_BIND_HOST` | HTTP bind | `127.0.0.1` |
| `PAPERCLIP_MCP_BIND_PORT` | HTTP port | `8000` |
| `PAPERCLIP_MCP_ENABLED_TAGS` | Optional comma-separated tool reduction | all tags |
| `PAPERCLIP_MCP_MAX_REQUEST_BYTES` | MCP/multipart request bound | 8 MiB |
| `PAPERCLIP_MCP_MAX_RESPONSE_BYTES` | Upstream response bound | 8 MiB |
| `PAPERCLIP_MCP_REQUEST_TIMEOUT_SECONDS` | Upstream timeout | 30 seconds |
| `PAPERCLIP_MCP_CA_BUNDLE` | Optional private CA bundle | system trust |
| `PAPERCLIP_MCP_ALLOWED_HOSTS` | MCP DNS-rebinding host allowlist | local patterns |
| `PAPERCLIP_MCP_ALLOWED_ORIGINS` | MCP browser-origin allowlist | local patterns |

Secret files take precedence over direct values and must not be empty. The inbound and
upstream resolved values must differ. The base URL rejects embedded credentials, queries,
and fragments; redirects are not followed.

## Health and readiness

- `/healthz` reports process/version and locally loaded operation counts. It makes no
  upstream request.
- `/readyz` calls Paperclip `/api/health` without upstream credentials because that
  operation is public. Failure returns 503.

Do not use readiness output as evidence that the configured upstream bearer can authorize
board, instance-admin, or agent operations.

## Credential rotation

Generate a new inbound value with `paperclip-mcp token`. Replace the secret atomically in
your secret manager and restart the gateway. Existing stateless HTTP requests do not retain
sessions.

Rotate the Paperclip token in Paperclip first, update `PAPERCLIP_API_TOKEN_FILE`, then
restart. Switching the upstream into `local_mode` is done by removing both upstream token
settings and restarting; the gateway will stop sending the Authorization header.

## Upgrades

1. Review changes to `paperclip-openapi.json`.
2. Run `paperclip-mcp validate-openapi`.
3. Regenerate and review `docs/generated/mcp-tools.md`.
4. Run `scripts/validate.sh`.
5. Rebuild the image and test representative read and write operations with a least-
   privilege credential.

Tool names are derived from method/path, not summary text. A path or method change is an
interface change and should be called out in the changelog.
