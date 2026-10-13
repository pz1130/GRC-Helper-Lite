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

The stack listens on `127.0.0.1:8080` by default (change the port with `WEB_PORT`).
Place an HTTPS reverse proxy on the same host. If a separate proxy needs access, set
`WEB_BIND_ADDRESS` to a private interface address and restrict access to that proxy in the firewall.
Do not bind the preview directly to a public interface. Production differs from development
in shape, not just settings:

- the frontend is a static build served by nginx, not a dev server;
- the backend runs the code baked into the image, with no reload and no source mount;
- only the web container publishes a port; the database, Redis and the API stay on the
  internal network;
- every service uses `restart: unless-stopped`.

**Not included, and needed before real use:** HTTPS (put a reverse proxy or load balancer in
front), restricting `8080` to the internal network, log collection, and scheduled backups.

### Upgrading

Back up first, then upgrade, then migrate, in that order:

```bash
make backup
make prod-up        # rebuilds the images from the current code
make prod-migrate
```

Between the new API starting and the migrations finishing, the API runs against the old
schema, so run `prod-migrate` straight after `prod-up`.

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

`make backup` does not schedule itself and only writes to the local disk, so a failed disk
takes both the data and its backups. Schedule it and copy the result elsewhere:

```cron
30 2 * * *  cd /srv/grc-helper && make backup >> /var/log/grc-backup.log 2>&1
0  3 * * *  rsync -a /srv/grc-helper/backups/ backup-host:/grc/          # or object storage
```

Backups beyond `KEEP` are deleted automatically. An unattended job without rotation fills the
disk eventually, and a full disk stops new backups exactly when you need one.

### Restore drill

Run one at least once. A backup that has never been restored has not been tested.

Do not use plain `make restore` for a drill: it restores into the production database.
Restore into a separate, temporary stack instead. A different project name gives it its own
volumes, and a different port keeps it apart.

```bash
# 1. Start an empty temporary stack (database and Redis only)
COMPOSE_PROJECT_NAME=grc-drill WEB_PORT=8081 docker compose -f docker-compose.prod.yml up -d db redis

# 2. Use the normal restore command, pointed at the temporary stack
make restore PROD_PROJECT=grc-drill db=backups/db-....sql docs=backups/docstore-....tar.gz

# 3. Compare every table's row count, the migration version and the sha256 of each stored
#    document with production (run the same queries with COMPOSE_PROJECT_NAME=grc-prod)
COMPOSE_PROJECT_NAME=grc-drill docker compose -f docker-compose.prod.yml exec -T db psql -U grc -d grc -At -c \
  "select table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) c from %I', table_name), false, true, '')))[1]::text
   from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1"
COMPOSE_PROJECT_NAME=grc-drill docker compose -f docker-compose.prod.yml run --rm --no-deps worker \
  sh -c 'cd /data/documents && find . -type f -exec sha256sum {} + | sort'

# 4. Start the full stack and check it works: sign in at http://localhost:8081, open a
#    document, then re-parse it, which makes the worker read the restored original file
COMPOSE_PROJECT_NAME=grc-drill WEB_PORT=8081 docker compose -f docker-compose.prod.yml up -d

# 5. Remove the temporary stack (-v deletes only grc-drill's volumes; --rmi local deletes
#    the images built for it)
COMPOSE_PROJECT_NAME=grc-drill WEB_PORT=8081 docker compose -f docker-compose.prod.yml down -v --rmi local
```

Matching row counts show the data arrived. Step 4 shows the system works.

A drill on the same machine cannot test the master key, because the temporary stack reads the
same `.env`. To restore on another machine, you need the `APP_SECRET_KEY` that was in use when
the backup was taken (see below). Store the key and the backups separately, but keep both.

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
  the encryption.
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
