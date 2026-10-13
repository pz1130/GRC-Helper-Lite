# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub:
**[Report a vulnerability](https://github.com/pz1130/grc-helper/security/advisories/new)**
(the Security tab of this repository, then "Report a vulnerability").

Do not open a public issue for a security problem.

A useful report says which version (commit) you tested, what an attacker can do, the steps to
reproduce it, and what you expected instead. Please do not attach real policy documents,
credentials or personal data. A made-up document that shows the problem is enough.

This project is maintained by one person. I aim to acknowledge a report within a week and to
agree a disclosure date with you once the problem is understood. I will credit you in the fix
unless you prefer otherwise.

## Supported versions

Only the latest commit on `main` receives fixes. There are no release branches yet.

## Preview boundary

This preview is for trusted internal IT staff in one organisation. All readers can read
organisation-wide policy, control and evidence data. Engagement-scoped accounts are not
a whole-application isolation boundary; do not issue accounts to external auditors.
AI proposals and coverage indicators require human review.

## Scope

In scope: the application code in this repository, including the parsers for uploaded Word and
PDF files, authentication and roles, the review and audit trail, exports, and the handling of
AI provider keys and redaction.

Out of scope, because they depend on how the system is deployed rather than on this code:

- running without HTTPS, or exposing port `8080` beyond the internal network
  (see [OPERATIONS.md](OPERATIONS.md));
- weak or reused values for `APP_SECRET_KEY`, `JWT_SECRET` or the database password;
- the behaviour of third-party AI providers once text has been sent to them;
- findings that need an administrator account to exploit, unless they let that administrator
  do something the role is not meant to allow.

## Handling of sensitive data

The system stores an organisation's internal policies and sends clause text to the configured AI
provider. Before deploying it, read the "Your data" section of the [README](README.md) and the
production and master-key sections of [OPERATIONS.md](OPERATIONS.md).
