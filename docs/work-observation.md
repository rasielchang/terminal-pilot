# Work observation: persistent read-only core

Work observation is independent of a dispatch job. Pilot registers only its own
known exact conversation targets. It does not discover every agent history on the
computer. An explicit completed topic retires observation; a completed job or a
closed Terminal does not imply that the task is finished.

The additive SQLite tables `work_observers` and `work_events` retain exact tool,
agent home, directory and thread, source revision/offset, last verified time,
coverage gaps, task references and source-linked excerpts. Events and cursors
commit together. Source event identity includes the original item/turn and content
digest; identical words in different events are distinct. Runtime updates use a
sequence and timestamp fence. Missing/ambiguous sources retain prior evidence.
These records are observations, not user authorization or automatic dispatch.

The restart-safe source scanner reads bounded records/pages, retains its byte
position and turn, validates the file identity and preceding bytes, and retries a
partial trailing line. Replacement/truncation triggers exact-source reindexing;
malformed complete lines expose a gap instead of silently skipping evidence.
Historic bootstrap is silent; future important changes remain identifiable.
There is no whole-history context injection or persistent second chat timeline.

Native binding requires a unique physical TTY and verified process birth/TTY
ownership. Saved process/Terminal generations prevent PID reuse from inheriting
another conversation. Ambiguities are exposed as `bindingConflicts`, and opening
that target is blocked rather than launching a replacement. Existing expired
metadata without evidence is not guessed into a new binding.

`closureStatus` is an internal read-only native operation over a saved binding and
Terminal generation. A fresh, reliable snapshot can confirm the exact old window
is absent. Failed snapshots remain unknown. An uncertain close intent can be
reconciled without repeating a signal or close; task history is retained. New
window instances cannot receive an old close intent.

Manual observation handles waiting/error before interpreting assistant replies;
an error is never completed. New waits use transition identities, even if their
text repeats. Already supported dispatch additionally waits for directly operated
busy work in the same directory. This is a conservative guard, not a new resource
lease system or broader authority to continue tasks.

Core regression entry points:

```sh
python -m unittest discover -s tests -p 'test_brain_observ*.py'
python scripts/test-model.py
python scripts/test-all.py
```

Tests use temporary stores and injected source/Terminal fixtures. They do not
send, approve, close or launch real engineering work. Real Terminal behavior,
phone navigation and production source formats still require staged acceptance.
This additive core does not install a schedule, call Asana/GitLab, or change the
agent's approval policy. Reliable Brain delivery and query contracts are a
separate dependent change.
