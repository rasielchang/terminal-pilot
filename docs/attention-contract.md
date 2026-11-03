# Assistant attention contract

Conversation ingestion, model work, and human notification are separate operations.

1. Journal direct worker discussion, progress, scans, recovery and derived summaries without starting Brain inference. Read relevant deltas on the next useful human, delegated-result or briefing turn.
2. Gate inference before queueing and again before starting a queued turn. Only a current delegated result or actual delegated failure requires orchestration. Formal approvals/questions are actionable native cards, not requests for a model to paraphrase them. Transient source health failures recover silently; sustained incidents are one deterministic diagnostic, never per-poll model calls.
3. A source result/wait has one durable attention identity across worker, job report, Brain summary and push. Read/answer/resolution are distinct. Acknowledging any representation suppresses its copies, while retaining unresolved cards and history. Replay/restart does not create another identity; distinct events with equal text remain distinct.
4. Badges represent unseen substantive delivery or actionable human work. Progress, synchronization and empty reports do not light badges. An answered/resolved item cannot reappear from an old observation.

Delegated work and direct discussion retain precise thread/home and binding-generation guards. Reading is not approval. No change grants autonomous execution or changes agent permissions.

## Acceptance gates

- Ten direct worker exchanges: zero new Brain inference and zero Brain pushes.
- No new business source, repeated scans, summary writes and transient recovery: zero inference/push.
- One result arriving via job and observer in either order: one orchestration request and one attention identity, preserved across restart.
- Formal approval/new question/real failure remains visible; a direct answer suppresses the old item without suppressing a later distinct question.
- Desktop and PWA use the same attention/read projection; stale acknowledgements do not clear newer items.
- Tests count actual caller invocations and push-sender attempts using isolated providers. No production model calls or worker send/approve/close are used for acceptance.
