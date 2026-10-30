# GRC Helper — Open-source preview

**Preview scope:** one organisation’s internal IT team, with human review of AI output.
Do not provision external auditor accounts or use this preview as proof of complete compliance.

A self-hosted tool for keeping an IT department's policies, the controls they commit to, and the
evidence behind those controls in one place. Every control points back to the policy sentence
that requires it, so when an auditor asks "where does it say that, and how do you know it's
done", the answer is already written down.

It is written for the IT and GRC teams of multilateral development banks and institutions like
them. It is open source under the MIT licence.

---

## The problem it addresses

In most multilateral development banks there is no national banking regulator setting the IT rules. There is
no rulebook handed down from outside; the IT department's own policy set is the rulebook. It
usually runs to dozens of documents (policies, standards, procedures, guidelines) written by
different people over several years. The board and the audit committee want to see it measured
against frameworks such as NIST CSF or ISO/IEC 27001. Internal audit, external auditors and
shareholder reviews each ask their own version of the same questions.

In practice the link between a policy sentence, the control it creates, the framework item it
satisfies and the evidence that proves it lives in spreadsheets and in people's heads. Each audit
rebuilds it by hand. When a policy is revised, nobody can say with confidence which controls and
evidence the change touches.

GRC Helper makes that chain explicit and keeps it current.

## What it does

**Reads your policies.** Import Word files, PDFs with a text layer, or pasted text. Each document
is split into a clause tree with its real clause numbers, including numbers that Word renders
from automatic numbering and that are not in the text itself. Tables in Word files, and ruled tables
in PDFs, keep their rows, columns and merged cells.

**Proposes controls, with quotes.** An AI model reads the clauses and proposes controls. Every
proposal carries quotes from the clauses it relies on, and code, not the model, checks that each
quote appears word for word in that clause. A proposal that fails the check is sent back to the
model to correct; if it still fails, the batch shows up in the review queue as failed, with the
reason, so nothing disappears quietly.

**Keeps a person in charge.** Nothing the model produces becomes part of the control library
until someone accepts it. Reviewers can accept, edit, merge into an existing control, or reject.
Every change to confirmed data is written to an audit log, and nothing is deleted: merged
controls are marked as merged and stay traceable.

**Maps controls to frameworks.** NIST CSF 2.0 and NIST SP 800-53 Rev.5 are included. Other
frameworks, such as ISO/IEC 27001 or COBIT, can be imported from a spreadsheet template. For each
framework the system shows which items are covered, by which controls, and which are not covered
at all. An existing control matrix in Excel can be imported and mapped the same way.

**Shows what a policy change affects.** When a new version of a policy replaces the old one, the
system lines up the two versions clause by clause and lists what was added, removed or changed,
together with the controls, framework mappings and evidence that depend on those clauses. This
comparison uses no AI. Clauses are matched by their number and heading; anything it cannot match
is listed for a person to look at rather than guessed.

**Supports the audit itself.** Import an auditor's question list from a spreadsheet. The system
drafts an answer for each question, quoting the policy clauses it draws on, and a reviewer edits
and finalises it. Answers, a readiness package (Word) and mapping and evidence registers
(Excel) can be exported.

Around this core there is an evidence register with validity dates, relationship and conflict
detection between controls, a maturity assessment, a risk register, a register of the technology
assets controls apply to, and search across all of it.

**Optionally finds requirements that keywords miss.** A separately enabled decision model can
scan an active document for additional requirements needing review. Jev is the first adapter.
It stays off until an administrator enables it, and only a manual scan sends redacted text.
Failures fall back visibly to keyword rules. See [setup and limits](OPERATIONS.md#optional-decision-model).

## Your data

The whole system runs on your own server under Docker Compose. Only the web interface publishes a
port; the database, the queue and the API stay on the internal network.

Policy text does leave that network when it is sent to an AI model. You choose the model and
decide where it runs:

- Any hosted model with an OpenAI-compatible API can be used, as can Anthropic, Azure OpenAI and
  several others. It can also point at a model server inside your own network, such as Ollama,
  in which case no text leaves it. (So far, quality has been evaluated with a hosted model only.)
- Before text is sent, redaction rules replace sensitive strings with placeholders and restore
  them in the response. The defaults cover IP addresses, email addresses, and mainland China mobile
  and ID card numbers. You can add rules, either patterns or word lists, for server names,
  system names, staff names or anything else you do not want sent.
- Every model call is logged with the model, token counts, cost and how many items were redacted.
  A monthly budget stops batch jobs when it is reached. It only counts models whose prices the
  system knows; calls to other models are logged at zero cost and flagged as unpriced, so for
  those the budget is not a real limit.
- Provider API keys are stored encrypted.

Access is by role (administrator, GRC lead, contributor, viewer). Accounts can be given an expiry
date, and repeated failed sign-ins lock an account for a while. Changing or resetting a password
signs that account out everywhere else straight away.

## Where it stands

This is an early preview, not a production-ready release. See [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)
for the checks required before a release and before using real institutional data.

Mapping currently stops when the rendered control library exceeds 60,000 characters and returns
at most 25 mappings per batch. A recorded benchmark measured mapping recall at 0.15; its reference
set has limitations, but automatic mapping must not be treated as exhaustive. Requirement detection
is also heuristic and can miss obligations without explicit keywords. Review the source
policies and missing mappings manually, including failed and oversized extraction batches.

This is working software, but it has not been run in production yet. Before you rely on
it, you should know:

| | |
|---|---|
| Language | English policies only. Clause numbering, full-text search and the prompts all assume English. |
| Documents | Parsing handles documents with consistent numbering or heading styles. Scanned PDFs without a text layer are not supported (no OCR); paste the text instead. Unusual layouts can still produce a poor clause tree, which is why the parser can be run on its own first (see below), and clauses can be split and merged by hand. |
| AI quality | On four public regulatory documents, extraction found 26 of 28 key requirements. 83% of extraction batches passed the verbatim-citation check on the first run (the target is 90%); the rest are flagged for retry. How many proposed controls are genuinely useful has not been measured yet; that takes a person reading them. Results vary between runs. |
| Real audits | Not yet tested in a live audit. |
| Deployment | Designed for one organisation's IT staff. Accounts can be limited to one audit engagement, but that limit only applies inside the audit module, so the system is not ready to give external auditors their own logins. |
| Screens | Built for desktop. Narrow screens work, but tables and the relationship graph are hard to use on a phone. |

[KNOWN_ISSUES.md](KNOWN_ISSUES.md) lists every known gap, why it has not been fixed yet, and
what would make it worth fixing.

**Document segmentation (2026-10-29).** Import now recognises Chinese regulatory
numbering (第X编/章/节/条, 一、/（一）/1.) in both DOCX and PDF, English
`CHAPTER I` + `Article N` layouts with the title on the following line, PDF headings
that wrap onto a second line, and stray `1. 2. 3.` body lists in unstyled DOCX.
On the synthetic segmentation eval (`backend/evals/segmentation/`, 18 documents)
accuracy went from **46.1% to 99.3%** (Chinese 29.8% → 100%, English 66.4% → 98.4%).
On a corpus of **50 real public policy/regulation documents** (`backend/evals/real/`:
35 Chinese, 15 English; 31 PDF, 19 DOCX) accuracy went from **18.8% to 96.6%** (95.0% after the 2026-10-29 boundary round, 93.7% before it)
(Chinese 0.1% → 98.4%, English 62.3% → 82.6%; PDF 21.3% → 92.4%, DOCX 14.8% → 95.6%),
scored against a parser-independent gold standard (v2) with all 50 documents manually reviewed.


## Trying it on your own policies

The quickest way to judge whether it will work for your documents is to run the parser alone on a
folder of them. This writes nothing to any database:

```bash
make corpus CORPUS_DIR=/absolute/path/to/your/policies
```

For each file it reports how many clauses it found and flags the usual problems: the whole
document collapsing into one clause, body text taken for headings, duplicate numbering. If the
clause trees look right, the rest of the system has something solid to work with.

## Getting started

You need Docker and Docker Compose.

```bash
cp .env.example .env        # then generate the two secrets, see OPERATIONS.md
make up && docker compose up -d
make migrate
make create-admin email=you@example.com name=You password='at least 8 chars'
```

Open http://localhost:5173, add an AI provider under Settings, and import a document.

Production deployment, backups, restore drills and key management are covered in
[OPERATIONS.md](OPERATIONS.md). Please read it before putting real policies into the system.

## Documentation

- [CHANGELOG.md](CHANGELOG.md): preview changes and local verification results.
- [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md): release checks and real-data deployment gates.
- [OPERATIONS.md](OPERATIONS.md): deployment, upgrades, backup and restore, key management.
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md): known gaps, go-live checklist, and decisions taken.
- [CLAUDE.md](CLAUDE.md): conventions for contributors (and coding assistants), in Chinese.
  Code comments are in Chinese as well; the interface, the commit history and this README are
  in English.

## Licence

MIT. See [LICENSE](LICENSE).
