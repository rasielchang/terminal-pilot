# Mobile project folders

The existing paired `GET /api/directories?directory=...` lists immediate child
folders. `POST /api/directories` adds one child folder and returns its path:

```json
{"parent":"/Users/example/Projects","name":"新專案","requestID":"unique-request-id"}
```

Successful response: `{"created":true,"directory":"/Users/example/Projects/新專案"}`.
Only these three input fields are accepted. Parent must already be a directory;
name is one non-empty filename (up to 255 UTF-8 bytes), not a path. Dot entries,
path separators, and control characters are rejected. Creation uses native
filesystem calls, no shell command or recursive parent creation. An existing
file/folder/symlink causes HTTP 409 and is never overwritten or silently selected.
Filesystem access failures also return 409 with a user-facing explanation.

Pairing, Host/Origin and JSON validation match the existing launch API. Request
IDs deduplicate in-flight, successful and ambiguous operations for the running
web service; a different payload with the same ID is rejected. After a restart,
atomic mkdir still prevents a second creation or replacement at the same path.
A lost response leaves the outcome unconfirmed: reload the parent listing and
explicitly choose the folder if it exists. No automatic rename or numbered copy.

Phone flow: 新工作 → 選擇資料夾 → navigate to parent → 新增資料夾 → enter
name → 建立並使用. Success fills the launch directory and fills an empty work
name. The user then selects Codex/Claude/Terminal and opens the project. Merely
creating a folder does not launch an agent, initialize Git, or grant folder trust.
