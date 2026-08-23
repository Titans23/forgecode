# ForgeCode Office Integrations

ForgeCode office automation uses a chat Channel for inbound requests and MCP
tools for outbound platform operations. The first implemented provider is
Feishu China using its official WebSocket Channel SDK and OpenAPI.

## Feishu setup

1. Create and publish a Feishu enterprise application, enable its bot, and
   grant only the required IM and Docx permissions.
2. Copy `examples/channels.feishu.json` to `.forge/channels.json`, then replace
   the allowed user and chat IDs. An enabled channel must configure at least
   one user or chat allowlist; empty allowlists are rejected.
3. Set credentials in the process environment. Never place their values in
   `channels.json` or MCP command arguments:

   ```powershell
   $env:FEISHU_APP_ID = 'cli_xxx'
   $env:FEISHU_APP_SECRET = 'secret'
   ```

4. Install dependencies and check readiness:

   ```powershell
   uv sync
   uv run forge integrations
   uv run forge gateway --channel feishu-main
   ```

When a Feishu channel is enabled, ForgeCode starts its bundled office MCP
sidecar with credentials passed only through the child environment. It exposes
`feishu_document_read`, `feishu_document_create`,
`feishu_document_update`, and `feishu_message_send`.

Read operations can run automatically. Create, update, and send operations are
high risk and pause for a single-use approval card bound to the requesting
Feishu user and the exact argument hash. Document updates require the revision
returned by the preceding read and preserve unsupported blocks.

The bundled document writer supports text, headings, bullets, ordered lists,
code, quotes, and to-do blocks. It does not delete or reorder tables, images,
attachments, or other unsupported blocks.

## Official OpenAPI MCP

The broader official `@larksuiteoapi/lark-mcp` server can also be configured in
`.mcp.json`. Use its `APP_ID`, `APP_SECRET`, and `LARK_TOOLS` environment
variables instead of command-line secrets, and add explicit `toolPolicies` for
every enabled remote tool. Unclassified MCP tools remain high-risk writes.

Personal WeChat/QQ client automation and desktop injection are intentionally
out of scope. Future WeCom and QQ adapters will implement the same normalized
Channel interface and approval rules using official APIs only.
