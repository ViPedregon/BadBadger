# Deploying a private BadBadger game

## Recommended path with your existing Netlify/Supabase setup

Use **one paid Render Python web service with a persistent disk** for this
milestone. It serves both the browser page and the existing engine, with Render
providing HTTPS and process restarts. Keep your existing Netlify app and Supabase
project unchanged. You can link to the game later from your existing app.

Netlify's static hosting does not run this long-lived Python/SQLite application.
Moving just the frontend there would introduce cross-origin authentication and
another deployment without making the first milestone easier. Supabase Postgres
is useful for future multi-user saves, but adopting it now would require a
storage migration. Supabase Auth and private Storage for off-site snapshots are
possible later integrations; neither is enabled here. No server API credentials
belong in browser code.

Render is recommended for ease of operation. A small Linux VPS with the Caddy
and systemd templates below is the alternative if you prefer managing a server.
Check the provider's current service **and disk** prices before creating either.
No current price quote, account access, live Render deployment, or published
domain has been verified in this task.

## Render Blueprint

The root [render.yaml](../render.yaml) declares one paid Python service, one
1 GB persistent disk, a generated web password and a generic health check.
It disables automatic deploys so changes can be reviewed and backed up first.
The runtime uses Render's `RENDER_EXTERNAL_URL` as its exact browser origin.
Optional AI dialogue is enabled by adding `OPENAI_API_KEY` securely to the
service environment after deploying the updated code. The pinned build includes
the SDK; without a key it remains offline. See WEB.md for model configuration,
usage charges and fallback behavior. Never reuse the game password as an API key.
`requirements-web.txt` pins the runtime dependency versions exercised here;
deliberately update those pins with the dependency declarations and rerun tests
when upgrading. Development/browser-test tools are not installed on the host.

1. Publish the reviewed implementation branch to your GitHub repository. In
   Render, create a Blueprint from that repository and select this branch.
   Review the paid service/disk settings before applying it. The Blueprint is
   provided for provider-side validation; it has not been submitted to Render.
2. Keep **one instance and one worker**. The save path is
   `/var/data/badbadger/relay.db`; only the mounted disk survives replacements
   of application instances. Do not switch to a free ephemeral service.
3. After a successful deployment, use Render's HTTPS service address. Sign in
   as `victor`, retrieving the generated `BADBADGER_WEB_PASSWORD` privately from
   Render's environment settings. Do not paste it in chat. For a custom domain,
   set `BADBADGER_WEB_ORIGIN` to that exact HTTPS origin, without a trailing
   slash. Using an alternate hostname intentionally fails host validation.
4. Play a few turns, close the browser, reopen it, then restart the service and
   confirm the same location, mission time and transcript return. Verify a
   backup/restore drill before treating this as your only durable save.
5. Establish off-site encrypted backups and monitoring. A disk on the same host
   is persistence, not an off-site backup. Do not assume provider snapshots
   replace an application-level restore test.

Render must forward only HTTPS requests to the public application endpoint;
confirm its HTTP-to-HTTPS redirect in your account. The app itself has a public
bind only when explicitly requested with an HTTPS origin. The generic
`/healthz` endpoint is unauthenticated and contains no game information. All
game routes and browser assets still require the password.

## Backup and recovery commands

The `badbadger-save` command is installed with the project. It copies committed
SQLite state using the online backup API, checks database integrity and foreign
keys, and checks the web save's revision against its turn receipts. It writes a
private new file and **never overwrites** a destination. Both operations use the
same verified-copy routine: restore always produces a new candidate save.

For example, from a Render shell with the service's persistent disk mounted:

```sh
badbadger-save backup \
  --source /var/data/badbadger/relay.db \
  --destination /var/data/badbadger/backups/relay-UNIQUE-TIMESTAMP.db
```

Choose a unique name. Securely transfer the snapshot to encrypted private
off-host storage. This implementation does not upload to Supabase, configure
its policies, or delete old snapshots. Establish transfer, retention and
disk-space alerts before leaving automated backups unattended. Treat backups
as private: they contain NPC secrets, dialogue and the full authoritative state.

To rehearse recovery without touching the live save:

```sh
badbadger-save restore \
  --source /private-backups/relay-snapshot.db \
  --destination /private-recovery/recovered.db
```

Run a separate local application against `recovered.db` with fresh test
credentials and confirm the transcript, revision and next turn. For actual
recovery, put the service into maintenance/stop it using the provider's supported
workflow, retain the old save, then change the configured save path to the
verified recovered copy. Never replace a live database or combine old WAL/SHM
files with a recovered main file. Resume writes only after checking the game.
Read-only integrity checks alone do not prove gameplay recovery.

## Alternative: Linux VPS

The checked-in templates assume:

- A dedicated `badbadger` system account with no interactive login.
- The reviewed checkout under `/opt/badbadger`, with the web extra installed
  into `/opt/badbadger/venv`: install `deploy/requirements-web.txt`, then install
  `--no-deps '.[web]'` using that environment's pip.
- `/etc/badbadger/web.env`, readable only by root, containing
  `BADBADGER_WEB_PASSWORD` and `BADBADGER_WEB_ORIGIN`. Enter values securely on
  the host; the origin must match the domain in the Caddyfile.
- `badbadger.service` installed in `/etc/systemd/system/`. systemd creates the
  private persistent directory `/var/lib/badbadger` and restarts failed processes.
- Caddy installed through its official signed packages, with the placeholder
  domain in `Caddyfile` replaced by yours. Point DNS at the server, permit HTTPS
  and the certificate issuer's required challenge traffic, and keep port 8000
  private. Validate the edited Caddyfile before reloading it.

The optional `badbadger-backup.service` and `.timer` create hourly verified local
snapshots under `/var/lib/badbadger/backups`. The task runs
`deploy/hourly_backup.py`; it never deletes snapshots or uploads them off-host.
Enable the timer only after arranging off-site copying, retention, disk-space
monitoring and alerts for failed timer jobs. The templates are not installed or
enabled by repository setup. Review paths and account permissions on the host.

## What has been verified locally

- Whole-game backup and restoration, transcript and request-id receipt recovery.
- Backup of committed changes while WAL mode has an active reader.
- Rejection of corrupt/unrelated/missing sources and existing destinations.
- Authenticated requests and turn submissions through Caddy using a generated
  local certificate explicitly trusted by the test client; TLS verification
  stays enabled.
- Restarting the backend against a restored save, then safely replaying a
  committed request through HTTPS without a second turn.
- Caddy configuration validation. The exact production systemd executable paths
  are not installed in Codex; local-path copies can be structurally checked.

Run the optional proxy drill with an official, verified Caddy binary installed:

```sh
BADBADGER_TEST_CADDY=/path/to/caddy python -m unittest discover -s tests -v
```

Without Caddy, this test is explicitly skipped. The test generates local
certificates and temporary saves and stops its own processes. It does not
provision a host, obtain a public certificate or configure off-host storage.
