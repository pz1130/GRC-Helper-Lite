# Operating GRC Helper

Deployment, backups, key management and routine checks. For what the system does
and its current limits, see the [README](README.md).

Everything runs under Docker Compose. The deployment host needs Docker and nothing else.

---

## Development setup

```bash
cp .env.example .env
# Generate the two secrets and put them in .env:
openssl rand -base64 32 | tr '+/' '-_'                # APP_SECRET_KEY
openssl rand -base64 48 | tr '+/' '-_' | tr -d '='    # JWT_SECRET

make up                 # db / redis / api
docker compose up -d    # adds the worker and the frontend dev server
make migrate
make create-admin email=you@example.com name=You password='at least 8 chars'
```

Open http://localhost:5173 and sign in.

AI features stay off until you add a provider under **Settings → AI providers** and bind
the tasks to it (next section explains why this is not automatic).

---

## Production deployment

**Do not copy a development `.env` to production.** Its two keys have already encrypted the
development provider keys and signed development sessions; reusing them means a development
machine can decrypt production secrets. Start production from `.env.example`:

```bash
cp .env.example .env
# APP_SECRET_KEY must be 32 bytes of URL-safe base64 (Fernet format); keep the tr step.
openssl rand -base64 32 | tr '+/' '-_'                # APP_SECRET_KEY
openssl rand -base64 48 | tr '+/' '-_' | tr -d '='    # JWT_SECRET
# Equivalent, if Python with the cryptography package is at hand:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
# Set POSTGRES_PASSWORD to a real password and change the matching part of DATABASE_URL.
# The two must agree: one creates the database account, the other is how the app connects.
# The service refuses to start with the CHANGE_ME placeholder or an empty value.

make prod-up
make prod-migrate
make prod-create-admin email=you@org.example name=You password='...'
make prod-seed-frameworks   # loads the bundled NIST CSF 2.0 and SP 800-53 Rev.5
```

Use `make prod-seed-frameworks`, not `make seed-frameworks`. The latter targets the
development stack: on a production host it starts a separate set of development containers,
loads the frameworks into their database, exits successfully, and leaves production empty.
It can also only run once; a second run exits 1 with "framework key already exists".

After these four commands the database holds the schema, default redaction rules, review
thresholds and one administrator. Two things still have to be done in the interface, because
they need your own input:

| Task | Where | What happens if you skip it |
|---|---|---|
| Add an AI provider and bind tasks to it | `/settings/providers` ("Apply to all reasoning tasks" and "Use as embedding model") | Extraction, mapping and conflict detection fail with a routing error. A missing embedding binding is the quiet one: documents still parse, but no vectors are produced and search falls back to keywords only. |
| Create at least one evidence type | `/settings/evidence-types` | The save button on the evidence form stays disabled. |

Each affected page says what is missing and links to where to fix it.

Production differs from development in shape, not just settings:

- the frontend is a static build served by nginx, not a dev server;
- the backend runs the code baked into the image, with no reload and no source mount;
- only the `proxy` container (Caddy, HTTPS) publishes ports; web, API, database and Redis
  stay on the internal network;
- every service uses `restart: unless-stopped`.

### HTTPS

The `proxy` service (Caddy, config in `deploy/caddy/Caddyfile`) terminates TLS and forwards to
the web container. Configure it in `.env`:

| Variable | Meaning |
|---|---|
| `SITE_ADDRESS` | Host name users type, e.g. `grc.example.org` or an intranet name. |
| `TLS_ISSUER` | `internal` (default): Caddy's own CA issues the certificate — for intranets without public DNS. An e-mail address: certificates from Let's Encrypt, renewed automatically; the name must resolve to this host and ports 80/443 must be reachable from the internet. |
| `PROXY_BIND_ADDRESS` | Interface to listen on (default `0.0.0.0`). Use a private address on multi-homed hosts. |
| `HTTP_PORT` / `HTTPS_PORT` | Published ports (default 80/443). Port 80 only redirects to HTTPS and answers ACME challenges. |

With `TLS_ISSUER=internal`, distribute Caddy's root certificate to client machines (or your
device-management system), otherwise browsers warn:

```bash
docker compose -p grc-prod -f docker-compose.prod.yml cp proxy:/data/caddy/pki/authorities/local/root.crt ./grc-root.crt
```

The `caddy_data` volume holds the certificates and that internal CA; losing it means
re-distributing a new root certificate.

Caddy adds HSTS, `Permissions-Policy`, `Cross-Origin-Opener-Policy` and removes the `Server`
header; nginx in the web container keeps sending CSP, `X-Frame-Options`, `nosniff` and
`Referrer-Policy`. If your organisation already has a load balancer that terminates TLS,
point it at the proxy's HTTPS port, or remove the `proxy` service and publish `web` on a
private address instead — never on a public interface.

Firewall: allow 80/443 only from the networks that should use the system.

Client addresses: the audit log and the per-IP sign-in limit use the address Caddy saw. The
API trusts `X-Forwarded-For` only from the Docker address pools (`FORWARDED_ALLOW_IPS` in
`docker-compose.prod.yml`), and Caddy replaces any `X-Forwarded-For` a client sends, so it
cannot be forged. If a load balancer sits in front of Caddy, every request will appear to come
from the load balancer until you list it under `trusted_proxies` in the Caddyfile.

### Upgrading

Back up first, then upgrade, then migrate, in that order:

```bash
make backup
make prod-up        # rebuilds the images from the current code
make prod-migrate
```

Between the new API starting and the migrations finishing, the API runs against the old
schema, so run `prod-migrate` straight after `prod-up`. Until then `/api/health` answers 503
with `"schema": "mismatch"` and the api container reports unhealthy. The same happens when the
database is newer than the code, for example after rolling the code back without restoring.

---

## Backup and restore

```bash
make backup                                          # database + document volume, into backups/
make backup KEEP=30                                  # keep more copies (default: the latest 14)
make restore db=backups/db-....sql docs=backups/docstore-....tar.gz
```

You need both halves. Without the documents, citations point at nothing; without the
database, every review decision is gone. `backups/` is git-ignored because it contains the
encrypted provider keys. Do not commit or share it.

### Scheduled backups and offsite copies

`scripts/backup.sh` (also `make backup-scheduled`) wraps `make backup` for unattended use:

1. dumps the database and archives the document volume under one timestamp;
2. checks integrity: the dump ends with pg_dump's completion marker, the archive passes
   `gzip -t`, and (unless `BACKUP_VERIFY_RESTORE=0`) the dump is loaded into a throw-away
   database inside the db container to prove it imports completely;
3. writes `backups/manifest-<stamp>.txt`: migration version, row count of every table,
   number of document files, sha256 of both files, and a **fingerprint** (sha256 prefix) of
   `APP_SECRET_KEY` — never the key itself;
4. copies the three files offsite with `rsync` (`BACKUP_OFFSITE_RSYNC=user@host:/path/`) or
   `rclone` (`BACKUP_OFFSITE_RCLONE=remote:bucket/path`);
5. keeps the newest `BACKUP_KEEP` (default 14) copies locally.

Any failure exits non-zero. Schedule it with the bundled systemd timer (02:30 nightly,
catches up after downtime):

```bash
sudo cp deploy/systemd/grc-backup.{service,timer} /etc/systemd/system/
# edit WorkingDirectory/ExecStart if the checkout is not /srv/grc-helper
sudo systemctl daemon-reload && sudo systemctl enable --now grc-backup.timer
systemctl list-timers grc-backup.timer        # next run
journalctl -u grc-backup -n 50                # last run
```

or cron: `30 2 * * *  cd /srv/grc-helper && scripts/backup.sh >> /var/log/grc-backup.log 2>&1`.
Without an offsite target the script still runs but warns: a failed disk takes the data and
its backups together. Retention on the offsite side is up to the target (bucket lifecycle
rule, or a cleanup job on the backup host); the script only adds files there.

### Restore drill

Run one at least once, and after every upgrade that adds migrations. A backup that has never
been restored has not been tested.

```bash
scripts/restore-drill.sh                   # newest backup
scripts/restore-drill.sh 20261013-201500   # a specific one
KEEP_DRILL=1 scripts/restore-drill.sh      # leave the stack up at https://localhost:18443
```

It restores into a separate project (`grc-drill`, ports 18080/18443 on 127.0.0.1) and never
touches production. It checks, in order: the manifest sha256 of both files; that the current
`APP_SECRET_KEY` matches the fingerprint recorded at backup time; the migration version; the
row count of every table; the number of document files; and finally, after migrating to the
current code (a backup taken before an upgrade is one schema behind), that the full stack
answers `/api/health` over HTTPS with the HSTS header. It removes the temporary stack (and
its volumes) when done.

To compare against live production instead of the manifest, the manual queries are:

```bash
COMPOSE_PROJECT_NAME=grc-drill docker compose -f docker-compose.prod.yml exec -T db psql -U grc -d grc -At -c \
  "select table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) c from %I', table_name), false, true, '')))[1]::text
   from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1"
```

### Restoring on a new machine

1. **Retrieve `APP_SECRET_KEY` from escrow** (see "Master key"). Without the key that was in
   use when the backup was taken, the data restores but every stored AI provider key is
   undecryptable. `JWT_SECRET` may be new (users just sign in again).
2. Install Docker, clone the repository at the release that wrote the backup (or newer), and
   create `.env` from `.env.example` with the escrowed `APP_SECRET_KEY`, a database password,
   and the HTTPS settings.
3. Copy the backup set (`db-`, `docstore-`, `manifest-` with the same stamp) into `backups/`.
4. Run `scripts/restore-drill.sh <stamp>`. Step 2 of the drill fails immediately with a clear
   message if the key is wrong — fix that before going further.
5. Restore for real into an empty production stack, then migrate if the code is newer:

```bash
COMPOSE_PROJECT_NAME=grc-prod docker compose -f docker-compose.prod.yml up -d --wait db redis
make restore db=backups/db-<stamp>.sql docs=backups/docstore-<stamp>.tar.gz
make prod-up && make prod-migrate
```

6. Sign in, open a document and re-parse it (proves the worker reads restored originals), and
   open Settings → AI providers and run a test call (proves the key decrypts).

---

## Mapping candidate prefilter with Jev (optional, off by default)

Framework mapping first picks 20 candidate controls per framework item, by default with
keyword (BM25) and vector ranking. Setting `MAPPING_JEV_PREFILTER=true` in `.env` switches
this ranking to the Jev judge model (TypeSafe) configured under Settings → Decision model.

**This sends your institution's control titles and statements (after redaction rules) to
TypeSafe, one request per framework item.** Enable it only after your data policy approves
that provider (KNOWN_ISSUES K17).

- Requires both the flag and a Jev provider selected in the decision-model settings (the
  separate "enabled" switch there is for the document requirement scan and is not needed).
- Each Jev request is logged as an AI call (`task_key=mapping_jev_prefilter`) and counts
  against the monthly and provider budgets like other batch work.
- If the flag is on but Jev is not configured, the budget is exhausted, a call fails or
  times out (30 s), the whole run falls back to keyword + vector ranking; the audit log gets a
  `mapping.prefilter_fallback` entry with the reason, and the mapping summary reports
  `candidate_source` and `candidate_fallback_reason`.
- Measured on public NIST data (`backend/evals/`): top-20 candidate recall 0.40 → 0.57;
  end-to-end with MiniMax M3, precision 0.51 → 0.54, recall 0.23 → 0.27, F1 0.32 → 0.36
  (single runs).

---

## Master key

`APP_SECRET_KEY` is a Fernet key. It encrypts every AI provider API key stored in the database.

- Back it up on its own, and not next to the database backups. Keeping them together defeats
  the encryption. **Escrow it** in the organisation's password manager or secrets vault, with
  at least two people able to retrieve it; record the fingerprint
  (`printf %s "$APP_SECRET_KEY" | sha256sum | cut -c1-16`) next to it so a restorer can match
  it against `app_secret_key_sha256_prefix` in a backup manifest.
- After a rotation, backups taken before it need the **old** key. Keep retired keys in escrow
  until the last backup that needs them has expired.
- If it is lost, the stored provider keys cannot be decrypted. The system keeps working, but
  every AI feature reports a decryption error until each provider key is entered again.
- If it leaks, put a new value in `.env`, restart, and run a rotation with the **old** value.
  Stored ciphertext is re-encrypted; nothing has to be re-entered by hand:

```bash
make prod-rotate-secret old='the previous APP_SECRET_KEY'
```

An interrupted rotation can simply be run again. Rows already rotated are skipped.

---

## Sign-in protection

Five failed sign-ins for one account within 15 minutes trigger a cool-down. The limit per IP
address is 50, because an office network usually shares one outbound address. Counts are kept
in Redis. If Redis is unavailable, the limit is not enforced. That is deliberate: being locked
out of the system by a cache failure is worse than rate limiting being briefly off.

An administrator can reset a user's password from the user list, and users can change their
own password on the Account page (reached from their name in the sidebar). Either way, every
session that account already has is ended: tokens carry a version that the password change
bumps, so an old or stolen token stops working at once rather than when it expires. A reset
also clears the account's cool-down. Guessing the current password on the change form counts
towards the same sign-in limit.

---

## Checking a new set of policy documents

```bash
make corpus CORPUS_DIR=/absolute/path/to/policies
```

This runs the parser only and writes nothing to any database. For each file it reports how many
clauses were found, whether the tree collapsed (one clause holding most of the text), whether
body lines were taken for clauses, whether a heading is really a paragraph, and whether the
numbering is unique. Run it before importing; finding a problem here is much cheaper than after.

The check also looks for "anchor" headings that every document in a set is expected to have.
The defaults (`Introduction`, `Roles and Responsibilities`) reflect the house style of the
documents the parser was first tuned on. For a different house style, pass your own
(`CORPUS_ANCHORS="Purpose,Scope"`) or skip the check (`CORPUS_ANCHORS=none`). The path must be
absolute: Docker bind mounts do not accept relative paths or `~`.

---

## Verification (for contributors)

```bash
make verify   # pytest / ruff / frontend build / i18n check / source hygiene / e2e, each with its own exit code
make test     # backend tests only; use this while writing code
```

`ruff` reports a count and does not fail the run: the code base has a baseline of existing
warnings, so check only that a change does not add to it. End-to-end tests run in an isolated
Compose project and clean up after themselves.

## Reproducible application dependencies

Python runtime and development dependencies are pinned with hashes in `backend/requirements.lock`
and `backend/requirements-dev.lock`; images and CI install from these files. To update them, use
`uv pip compile pyproject.toml --python-version 3.12 --universal --generate-hashes -o requirements.lock`
and the same command with `--extra dev -o requirements-dev.lock` from `backend/`. Review both diffs
and run the release checks. Frontend builds use `npm ci` and the committed lockfile. Container base
image tags still move: record image IDs with each deployed revision.

## Optional decision model

The decision role is separate from chat and embeddings and is **off by default**. The first
adapter is Jev through TypeSafe's [native API](https://docs.typesafe.ai/api). It is used only
for the supplementary requirement review scan, not framework mapping or document parsing.

1. Apply migration `0033` using the normal backup and migration procedure. Existing deployments
   remain off; no provider or credentials are added by the migration.
2. In Settings → AI providers, add a provider of kind `jev`, a pinned model such as
   `jev-1.13.0`, and your API key. The default base URL is `https://api.typesafe.ai/v1`.
   The optional base URL is the prefix before `/systemone`, not a chat-completions endpoint.
   The Test button sends only a synthetic connectivity question.
3. After approval under your data policy, select that provider in **Decision model (optional)**,
   check **Enable manual decision scans**, and save. Chat bulk assignment never enables this role.
4. Open an active document and choose **Run decision scan**. Reading a page never invokes the
   model. Viewers can read the result but cannot start a scan. To stop further scans, uncheck the
   option and save; an already-sent request cannot be recalled, but later batches stop.

Only clause text and heading paths are sent, after generation redaction. Review the redaction
rules first; they do not guarantee removal of all confidential information. The runner never
silently substitutes a global fallback provider. API keys use the existing encrypted store;
call logs contain usage, hashes, redaction counts and fixed error categories, not clause text
or upstream error bodies. Configuration changes and scan counts are audited.

The scan retains every keyword match and adds model suggestions with probability at least 0.5.
That threshold is a review heuristic, not an institution-specific calibrated decision boundary.
It creates no controls and accepts no proposals. Model suggestions remain for a person to review.
The document-list counters still use keyword rules; the document's review scan is a separate aid.

Successful unchanged batches are reused. Changes to clause text/headings, the selected provider,
model, endpoint, key or redaction rules invalidate the snapshot. Prefer pinned model versions:
alias updates at the provider cannot be detected locally. A failed or incomplete scan can be
retried; successfully assessed clauses are not sent again while its input fingerprint matches.

Unconfigured/disabled providers, upstream failures, invalid responses, exhausted recorded budgets,
stale results and size limits are explicitly shown as rule fallback. Requests use bounded batches
(up to 40 questions and 24,000 bytes before redaction; oversized clauses over 12,000 bytes are
not sent or truncated) with a 45-second scan deadline. Unassessed clauses remain rule-only,
which can miss implicit requirements. Jev is currently unpriced in the local price table: its
calls are flagged as unpriced, so the recorded budget cannot guarantee its actual spending cap.

For contributors: business code depends on `DecisionProvider` and `DecisionRequest/Response` in
`app/decision/base.py`. Register another adapter in `app/decision/factory.py` with its provider
kind/migration; do not put vendor-specific HTTP details in the requirement scanning service.
No institutional corpus or real provider call was used to validate this integration.
