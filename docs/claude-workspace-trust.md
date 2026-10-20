# Claude workspace trust on mobile

The existing paired `POST /api/approval` also accepts a native-issued Claude
workspace trust request. Its JSON fields remain `session`, `binding`, `approval`,
`requestID`, `action`, and `content`. Only `accept` and `decline` are supported;
`content` must be absent, null, or an empty object. Clients cannot provide a path,
terminal text, key sequence, process ID, or arbitrary approval operation.

`GET /api/state` and `/api/thread` expose this request in the existing
`session.runtime.approvals` array with `kind: workspace_trust`, the exact project
directory, original prompt, and explicit trust/exit actions. The session is
waiting and normal message sending is disabled while this prompt is present.
Both manager and Brain reuse the same confirmation card. Reading a card never
answers it.

Native detection is limited to a Pilot-launched Claude before its first
SessionStart hook, an exact live Terminal binding, a foreground Claude process,
and a recognized current-screen prompt naming the launched directory. It does
not search scrollback. Unknown prompt formats remain unapproved.

Immediately before answering, the native bridge rechecks the process and current
prompt. The final Terminal operation compares the screen again before sending
only the fixed selection movement and Return. Claude itself records trust; Pilot
does not edit trust settings or bypass permissions. Acceptance permits Claude to
read, edit and execute in that folder, including its project configuration.

Decisions are journaled locally before terminal input, keyed to the native
approval identity and request ID. Duplicate or ambiguous delivery never sends
input again, including after a Pilot restart. A lost response leaves the card
locked pending observation; a changed or resolved prompt cannot receive stale
input. Existing pairing/Host/Origin validation and Codex protocol routing remain.

Reference: [Claude hooks: workspace trust](https://code.claude.com/docs/en/hooks#workspace-trust).
The installed Claude Code 2.1.302 prompt was observed in an isolated CLI test;
it defaults to `No, exit` above `Yes, I trust this folder`.
