# Changelog

## Unreleased — internal IT preview

This preview is intended for trusted internal IT staff in one organisation. AI proposals and
coverage indicators require human review. External auditor access is unsupported.

### Optional decision role

- Add a vendor-neutral decision interface and a Jev adapter for the native TypeSafe API.
- Keep the role off by default, independently configured from chat, embeddings and global fallback.
- Add a manual requirement review scan with redaction, usage/audit logs, bounded batches and
  reusable snapshots. Reading pages never sends clause text; viewers cannot initiate scans.
- Show rule fallback for disabled/unconfigured services, failed calls, stale results and limits.
  Preserve keyword matches even when a model disagrees; do not create or accept controls.
- Add migration `0033`. Upgrade/downgrade and schema consistency checked on a disposable database.
- Verification used synthetic data and mocked provider responses. Real-service connectivity and
  real-corpus quality were not tested, and no existing deployment was enabled or upgraded.

### Changes

- State the preview boundary in the README, security policy, login and application interface.
- Default production web access to loopback; document private-proxy and HTTPS deployment.
- Exclude local environments, credentials and private fixtures from Docker build inputs.
- Pin Python runtime and development dependencies with hashes and use npm's lockfile in both
  frontend images. Upgrade React Router and preserve synchronous URL-driven form updates.
- Add dependency audits, full-history secret scanning and isolated browser tests to CI.
- Clean up the isolated browser stack even when setup or tests fail.
- Allow backend tests to validate the configured isolated database instead of a fixed name.

### Local verification — 2026-10-01

- Backend on Python 3.12 with the locked dependencies: 1,405 passed, 29 skipped.
- Isolated browser suite: 198 passed.
- Frontend build, translation checks, source hygiene and diff whitespace checks passed.
- Runtime and development Python dependency audits and npm audit: no known vulnerabilities found.
- Git history secret scan passed with an exact allowlist for one synthetic test key.
- Both production images built; nginx configuration validated. The backend image excludes the
  local virtual environment, environment files and private JSON fixtures.
- Production Compose resolves to loopback-only web access and no published backend ports.

These results cover the local preview changes; remote CI must run after submission. The browser
suite uses an isolated development stack and many mocked responses, not a real-model acceptance
test. Python emitted deprecation warnings and the frontend still has a bundle-size warning.

### Deployment gates still open

No existing deployment was upgraded by this change. HTTPS and firewall configuration, scheduled
off-host backups, cross-host recovery, operational monitoring, AI data-policy approval and human
acceptance on the intended corpus remain required. Mapping scale and recall limits remain as
documented in KNOWN_ISSUES.md. See RELEASE_CHECKLIST.md for the complete acceptance checklist.
