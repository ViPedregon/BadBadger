# BadBadger chapter service boundary

`badbadger.service.RelayService` is the application boundary used by the
authenticated web adapter in `badbadger.web` (see [WEB.md](WEB.md)). Its historical
class name is retained for compatibility; it supports both catalog chapters. It uses
the existing Python game engine. Direct service construction defaults to offline
for deterministic tests/tools. The web adapter passes `offline=False`, enabling
the configured OpenAI backends only when `OPENAI_API_KEY` is present. The web
CLI's `--offline` flag overrides a present key. Provider failures use the safe
fallback, while all proposals still pass through engine validation.

Construct it once at startup with a server-owned SQLite path:

```python
from badbadger.service import RelayService, TurnConflict

game = RelayService("/path/to/persistent/relay.db")
view = game.view()
result = game.submit(
    request_id="unique-id-for-this-submission",
    command="go to Relay Chamber",
    expected_revision=view["revision"],
)
```

Reconstructing the service with that same path resumes the save and transcript.
Do not run its initializer during active gameplay. A pre-existing Relay Crisis
or Blackbarrow save can be opened, but its old CLI transcript cannot be recovered safely from
private engine history; only submissions made through this service appear in
the new transcript. Do not alternate CLI writes with service play: CLI writes
do not update service revisions or receipts.

For a fresh Blackbarrow save, use `RelayService(path, scenario="blackbarrow")`.
The direct Python API retains its Relay Crisis default for compatibility; the
web command defaults to Blackbarrow. Both always resume an existing supported
save without changing its chapter. New-game requests may select only registered
chapter keys; filenames and arbitrary scenario paths are not accepted.

`view()` returns an opening, current public status, revision, and persisted
player-facing transcript. `submit()` returns messages, status, revision, and a
session-exit hint (`should_quit`). Quitting does not reset or end the mission.
Every accepted submission, including a read command, increments the revision.
To refresh without submitting a command, use `view()`.

`new_game(request_id, expected_revision, confirmed=True, scenario=None)` archives the engine's
tables and opening metadata, reseeds the bundled scenario, and updates the
current transcript boundary in one transaction. Revisions continue increasing
across playthroughs; old receipts are retained but old turns do not appear in
the current view. Receipt operations distinguish turns from new-game requests.
This prevents stale tabs or retried resets from mutating the wrong playthrough.
Startup migrates existing web saves with default values that preserve their
transcripts. Archives live in `playthrough_archives`, not a public download route;
old receipts still reference the archived playthrough's turn range. There is no
archive download route. The browser can list public metadata and resume a selected
archive with confirmation. Local archives do not replace off-site backups.

`resume_archive(request_id, expected_revision, archive_id, confirmed=True)` archives
the current engine state and public transcript, then restores the selected engine
snapshot and its transcript in the same transaction. The server supplies archive
ids; callers cannot submit database paths or snapshot contents. Global receipts
remain immutable and revisions increase across every swap. Older archives recover
their transcript from the retained revision range. Incompatible table sets or
invalid foreign keys cause a rollback. Resuming an ended game preserves its ended
status, original authored chapter content and identity snapshots.

The client should generate a request id once, retain it until it has received a
response, and reuse it with the exact same command after a network timeout.
A committed duplicate returns its original response, even if its expected
revision is now old. Reusing an id with different text raises `TurnConflict`.
A new request with an old revision also raises `TurnConflict`; refresh the view
and ask the player to submit again. Do not silently retry a stale new command.
The web adapter should translate conflicts into HTTP 409 and validation errors
into HTTP 400, while keeping unexpected database/engine errors private.

Each call owns its SQLite connection on the calling thread. A `BEGIN IMMEDIATE`
transaction serializes submissions, checks revision/id, runs the full turn,
and commits state, transcript and receipt together. Nested repository operations
use savepoints; they cannot commit the outer turn early. Exceptions roll back
the entire turn. SQLite's default lock timeout can produce a database-busy error
under contention; retry the same request id rather than inventing a new one.
Only local persistent SQLite on one host is supported. A future network adapter
should put this blocking work on a bounded executor, not its async event loop.

The service explicitly disables developer mode, never returns raw engine history
or NPC context, and routes `summary`/`recap` to a player-safe summary of witnessed
consequences and accepted player requests. The private developer summary is not
exposed. `view().guidance` supplies objective, phase, command suggestions and the
public recap; the browser contains no rules for resolving them. Public outcome
memories are created only by validated actions while the player is present.
Hidden description facts are
excluded from the application's examinable targets. This is not a general
scenario visibility redesign: legacy descriptions remain globally examinable.
New `examinations` definitions bind observations to a location; both intent context
and the engine enforce that scope. Successful examination records direct beliefs
and memories only for characters actually present. AI proposals cannot write
examination definitions or select arbitrary chapter files.

Run the complete test suite from the repository root:

```sh
python -m unittest discover -s tests -v
```

Service tests cover same-id retries, stale revisions, simultaneous submissions,
failure after the main action, failure saving a response after NPC processing,
resume, a completed rescue, and representative private-output checks.

The web adapter supplies single-user authentication, a same-origin HTTP API,
an escaped-text browser UI, and input/rate limits. Before remote deployment,
configure HTTPS, persistent storage, off-host SQLite backups and a tested restore.
Never accept a database path from
the browser, serve scenario JSON/save files as assets, or expose engine objects.
Use a fresh save for browser acceptance tests. Live AI dialogue remains a
separate integration requiring additional output/privacy tests and server-only
credentials. This module alone is not a hosted or authenticated application.
