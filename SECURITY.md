# Security

Terminal Pilot can read selected agent conversations and send instructions to
local Terminal sessions. Pair only devices you trust. Keep the service on
localhost or a private network; use Tailscale HTTPS when connecting remotely.

## Reporting a vulnerability

Use this repository's **Security → Report a vulnerability** when private
vulnerability reporting is enabled. If absent, open an issue containing only
“Private security contact requested” and a preferred contact method; wait for a
private channel before sharing details. Do not post tokens, pairing codes,
transcripts, exploit payloads or private paths in public issues.

Include the Pilot/macOS version, affected feature and a synthetic reproduction.
Fixes target the latest preview; there is no promised response SLA or support for
historical previews. Publication scans are not a security audit or a guarantee
that every secret is detectable.

## Boundaries

- Native app owns Terminal Automation permission; web access requires pairing.
- Origin, session/window binding, notice identity and request deadlines are
  validated. Ambiguous delivery is not silently retried.
- Brain does not grant agent permission. Explicit decisions use formal approval
  channels where supported.
- API keys stay in Mac Keychain; pairing and history stay in Application Support.
- Push includes work titles/status; voice sends selected audio or content to
  OpenAI. See [privacy](docs/privacy.md).
