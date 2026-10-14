# Open-source preview release checklist

The supported audience is one organisation's trusted internal IT staff. Humans must review
AI proposals, citations, mappings and omissions. This preview is not a compliance certification,
a complete audit conclusion, a multi-tenant service or a portal for external auditors.

## Before publishing a preview revision

- Run backend tests, frontend build, translation and source hygiene checks.
- Run the isolated browser suite (`make e2e`); record failures and skipped checks.
- Audit both dependency lockfiles; resolve applicable advisories before release.
- Scan all Git history for secrets; inspect findings rather than hiding entire test directories.
- Review documentation, comments and fixtures for internal corpus counts, structure, evaluation
  results and source quotations. Aliases do not make these details suitable for publication.
- A deletion commit does not remove earlier copies. Publish a reviewed snapshot with fresh history
  when old history is unsuitable for disclosure. Keep the original checkout private and do not
  push its branches or tags into the publication repository.
- For previously published material, separately review remote branches, tags, pull requests,
  releases, artifacts and caches. Local cleanup cannot remove third-party clones or forks.
- Review distributable files and container contents for institutional material. Git ignores do
  not exclude Docker build files. Build from a clean checkout for distributed images.
- Record the tested commit and results in the release notes. Do not call untested changes stable.

## Before using institutional data

- Restrict access to internal IT; do not issue external auditor accounts, including scoped viewers.
- Configure HTTPS (`SITE_ADDRESS`, `TLS_ISSUER`; see OPERATIONS.md "HTTPS") and restrict 80/443 in the firewall. Only the Caddy proxy publishes ports.
- Generate separate deployment keys and passwords; securely retain the encryption key separately
  from backups. Do not reuse development credentials.
- Approve the selected AI provider under the institution's data policy. Redaction is not a
  guarantee that all confidential text has been removed. Use synthetic data until approval.
- Enable `grc-backup.timer` with an offsite target (`scripts/backup.sh`) and verify alerts on failure.
- Restore onto a different host with the escrowed key (`scripts/restore-drill.sh`, then OPERATIONS.md "Restoring on a new machine") and verify documents, login and AI key access.
- Configure log collection, disk-space monitoring and worker/queue failure monitoring.
- Have 1–2 IT colleagues complete upload, extraction, human review, mapping and export using
  approved documents. Record defects, missed requirements and reviewer effort.
- Manually assess extraction precision and mapping omissions on the intended corpus. Treat the
  60,000-character mapping limit, 25-result batch cap and oversized extraction failures as explicit
  constraints. Coverage indicators are heuristic, not evidence that all obligations were found.

Host configuration, real-data approval, cross-host recovery and human acceptance are deployment
gates. Passing CI does not complete them. See OPERATIONS.md and KNOWN_ISSUES.md for details.
