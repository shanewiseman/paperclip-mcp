# Security model

## Trust boundaries

The gateway trusts its operator-controlled configuration, bundled OpenAPI document, mounted
secret files, and configured Paperclip origin. MCP clients are untrusted callers. Paperclip
responses are untrusted and bounded before decoding.

The service does not provide a human approval workflow. It exposes write-capable,
administrative, secret-bearing, plugin/runtime, and destructive Paperclip operations because
they are present in the supplied interface. MCP tool annotations are advisory only.

## Controls

- HTTP binds to loopback by default and refuses a non-loopback bind without an inbound
  bearer token.
- Inbound and upstream tokens are resolved independently, compared using constant-time
  equality, required to differ, and never accepted as tool arguments.
- Inbound authorization is never forwarded. The upstream token is created from operator
  configuration and is omitted for public operations and tokenless `local_mode`.
- The Paperclip origin is fixed at startup. Redirects, arbitrary URLs, arbitrary headers,
  cookies, and caller-provided authorization are disallowed.
- Path values are percent-encoded and query values follow declared OpenAPI serialization.
- Response and multipart file bytes are bounded. Non-JSON content is text or base64 rather
  than written to disk.
- Error excerpts are short and redact common credential fields plus the configured upstream
  token. Successful secret-fetch operations intentionally return the upstream result to the
  authorized MCP caller; avoid logging tool results.
- MCP transport Host and Origin checks protect against DNS rebinding.
- Compose runs as a fixed non-root user with a read-only filesystem, all capabilities
  dropped, no-new-privileges, bounded resources, and no host mounts or Docker socket.

## Deployment guidance

Keep the default loopback publication or place a TLS reverse proxy with equivalent bearer
enforcement in front of the service. Give the Paperclip token only the board/agent grants
the intended tools require. In `local_mode`, treat every process and user able to reach the
Paperclip port as privileged; the absence of upstream authentication is a Paperclip trust
decision, not an authorization control added by this gateway.

Host and container administrators can read process configuration and Docker secrets. Use a
single-tenant trusted host, rotate exposed tokens, and do not place secrets in `.env`, client
JSON, logs, issue comments, or generated documentation.

The OpenAPI source has incomplete schemas around uploads, OAuth callbacks, and some
plugin/import endpoints. Review the exact generated tool before allowing those operations,
and prefer corrected upstream schemas over expanding compatibility fallbacks.
