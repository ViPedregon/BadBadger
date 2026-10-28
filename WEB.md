# Relay Crisis in a browser

The browser app runs the existing Python game engine through `RelayService`.
It is single-player and uses optional AI dialogue and unfamiliar-input
interpretation when `OPENAI_API_KEY` is configured on the server. Without a key,
or with `--offline`, it uses deterministic offline dialogue. No engine rules
live in JavaScript. The current cast is
Player, Director Sable, Technician Orr and Dr. Lin; future character authoring
is separate.

## Install and run

Use Python 3.11 or newer. From the repository root:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[web,openai]'
```

Set `BADBADGER_WEB_PASSWORD` securely in the server's environment to a randomly
generated password of at least 24 characters. Do not put it in source control,
URLs, browser code, or a command-line argument. For a local Bash session, this
prompts without displaying the value or putting it in shell history:

```sh
read -rsp 'Web password: ' BADBADGER_WEB_PASSWORD
export BADBADGER_WEB_PASSWORD
.venv/bin/badbadger-web --save "$PWD/saves/browser-relay.db" --port 8000
```

The listener binds only to loopback. Open the loopback origin at port 8000 in
a browser **on the same machine**. The browser's native authentication prompt
uses username **victor** and your web password. This username is an access
credential, not a change to the scenario's player character. A remote Codex
machine's loopback address is not accessible from your laptop; remote access
requires the HTTPS deployment described below. No publicly reachable site has
been deployed by these changes.

In Codex Cloud, the prepared Python environment is at
`/workspace/badbadger-venv`. Use its `bin/python` and `bin/badbadger-web` instead
of `.venv`. Store retained saves outside the checkout, for example under
`/workspace/badbadger-saves/`. The web password is intentionally not generated
or added to environment settings automatically.

The page shows current surroundings, the saved story and a text input. Try
`look`, `choices`, `journal`, or `help`. Each successful submission is committed
before it is shown as saved. Close the page and reopen it to resume; the save
also survives a server restart when the same database path is used. Game time
does not advance while the page is closed. There is no reset button and no
developer inspector. `summary` currently returns public status rather than
private NPC goal outcomes.

Use a fresh browser save rather than the baseline playthrough saves, which
have already ended. Do not run the CLI against the web save while the service
is running: CLI writes do not participate in web request revisions.

## Connection failures and multiple tabs

The browser stores only the pending submission (id, command, expected revision)
in local storage until a response arrives. It does not store API keys, passwords,
NPC context, or the save database there. Allow site storage for safe retries.
The transcript remains on the server. A command can contain personal text, so
use a trusted browser profile. HTTP Basic credentials may stay cached in the
browser; close all private browsing windows or clear the browser's credentials
when leaving a shared machine. There is no application logout/session system.

If a response is lost, choose **Retry pending turn**. The same request id returns
its committed response and does not advance the game twice. A second tab that
submits against an outdated revision is refreshed and asked to submit again;
the app does not automatically execute that stale command. A page refresh or
server restart also preserves recovery through the durable request receipts.

## Private remote hosting

This is a prepared application, not a deployed hosting service. The minimum
deployment is one Linux host with:

1. A mounted persistent local disk for the save, readable only by the service
   account. Keep this path independent of checkout/deploy directories. Use one
   Uvicorn worker and one instance, with a process supervisor to restart it.
2. An HTTPS reverse proxy on the same host forwarding to the loopback listener.
   Set `BADBADGER_WEB_ORIGIN` to the exact public HTTPS origin, with no trailing
   slash. Preserve its Host header. Restrict the firewall to HTTPS, keep the
   Python listener private, and configure proxy body/time limits. Origin/host
   validation intentionally rejects alternate domains and guessed forwarded
   headers. No CORS access is enabled.
3. A high-entropy `BADBADGER_WEB_PASSWORD` injected by the host's secret manager
   or a tightly permissioned environment file. Only one account (`victor`) owns
   the game. HTTPS is mandatory for transmitting HTTP Basic credentials remotely.
   For a wider audience, replace this with an identity-aware gateway and explicit
   per-user ownership before adding accounts. No provider API credentials are
   needed for offline play; optional AI setup is described below.
4. Encrypted, versioned off-host backups and a restore drill. SQLite's online
   backup API can create a consistent snapshot while the app is running:

   ```python
   import sqlite3
   from contextlib import closing

   with closing(sqlite3.connect('/persistent/relay.db')) as source:
       with closing(sqlite3.connect('/private-backups/relay-snapshot.db')) as target:
           source.backup(target)
   ```

   Choose a new backup destination each run, upload it to private off-host
   storage and set retention. Stop the service before replacing a live save
   during restoration. Check `PRAGMA integrity_check`, then start against a
   restored copy and verify transcript/revision/resume. Copying the main `.db`
   file alone while writes or WAL files exist is not a valid backup procedure.
   Off-host scheduling and recovery are deployment tasks, not configured here.

The app authenticates the page, assets, reads and writes; limits failed login
attempts and turn submissions; caps request bodies and commands; rejects foreign
origins; and disables API docs. Client output uses text nodes rather than HTML.
Private database files, scenario JSON, NPC context and engine history have no
download routes. Security headers block framing and external script execution.
Passwords, saves and backups still require appropriate host-level protection.

For a ready-to-review Render Blueprint, Linux service templates, and the
`badbadger-save backup/restore` recovery workflow, see
[deploy/README.md](deploy/README.md). The default listener remains loopback-only;
the explicit `--host 0.0.0.0` container option requires an HTTPS origin.
`BADBADGER_WEB_ORIGIN` takes precedence over Render's `RENDER_EXTERNAL_URL`.
The public `/healthz` probe returns only a generic status, never game data.

## Enable AI dialogue on Render

Deploy the updated branch, then open the Render service's **Environment** page.
Add `OPENAI_API_KEY` using your OpenAI project's API key. Enter the value securely
in Render, never in chat, repository files, browser code or a URL. OpenAI usage
is billed separately from hosting. The key is used only by the Python server.

Optionally set `BADBADGER_OPENAI_MODEL` to a model your OpenAI project can use
with Responses API structured outputs. The default is `gpt-4.1-mini`. Saving
environment changes must restart/redeploy the service before they take effect.
The Render build must install the updated pinned requirements and `[web,openai]`
extras; a key alone does not update a previously deployed offline-only build.

Existing saves and transcripts resume unchanged. The page indicates whether
AI or offline dialogue is configured (this indicator does not verify that the
provider accepted the key). Try a question addressed to a character
who is present, such as `ask Director Sable why he favors containment`. Familiar
commands still parse locally. Unfamiliar input can use the AI intent interpreter;
NPC responses use that NPC's filtered context. Neither interpreter can write
canonical world state directly. Free-form AI prose can still be wrong or echo
context; engine validation protects consequences, not every generated sentence.

Provider calls have explicit timeouts and no automatic SDK retries. Malformed
structured responses retain the existing one-retry policy. The page and sample
proxy allow longer requests for AI turns. If a response is lost, keep using the
same pending request id. Committed retries do not make a second model call.
An uncommitted failed/crashed turn can repeat provider work when retried, so
database atomicity cannot guarantee exactly-once external API billing.

If the provider rejects the key/model or times out, normal player output gives
short recovery guidance and retains offline fallback. Check private Render logs
for sanitized failure types. Remove the key or start with `--offline` to disable
calls. Do not send key values or the game's HTTP Basic password for diagnostics.

## Validation

Install development extras to exercise HTTP tests:

```sh
.venv/bin/python -m pip install -e '.[web,web-test]'
.venv/bin/python -m unittest discover -s tests -v
```

Real-browser tests additionally use a system `chromium` or `chromium-browser`
executable and Playwright's Python package. They create temporary saves, choose
local test ports and random test credentials, and terminate their own server
processes. Missing web dependencies or a system Chromium executable cause the
corresponding optional tests to be reported as skipped; do not count skips as
browser validation.

Verified in Codex Cloud: authenticated browser play through the rescue outcome,
closing/reopening the browser, restarting the Python process, recovering a lost
response after reload without a duplicate turn, rejecting a stale second tab,
and rendering HTML-like input as plain text. API checks cover authentication,
origin/host checks, limits, private routes, duplicate submissions and safe errors.
Remote HTTPS, a real host's persistent disk lifecycle, off-host backup scheduling,
and live AI behavior are not established by these local tests.
