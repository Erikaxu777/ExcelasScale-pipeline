# Excelas Scale prospect pipeline — iteration 5

Find five UK data-centre professionals at five different companies, review one 19-column prospect CSV, then prepare a local HubSpot import preview. This iteration checks field formats and primary-key uniqueness, skips duplicates and records row-level failures. It uses the approved four-direction ICP prompt in `prompts/icp.txt`. Work email is primary; LinkedIn is supplementary.

```text
ICP → Web Search → contact lookup → enrichment → Python ranking
                                      ↓
                             review_queue.csv (19 columns)
                                      ↓
                            Human approval → format checks
                                      ↓
                           primary key / email / name + domain
                                      ↓
                       CREATE → import CSV; SKIP / FAILED → report
```

## Setup / Run Notes

Use Python 3.10 or later.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

For online research, set `OPENAI_API_KEY` in the terminal. In Bash or zsh:

```bash
read -r -s OPENAI_API_KEY
export OPENAI_API_KEY
printf '\n'
python main.py research --out runs/excelas
```

The online path uses OpenAI Responses API, Web Search and structured outputs. Default model: `gpt-6-astra`; override with `OPENAI_MODEL` or `--model`. Completed search and analysis requests are cached. Live research consumes API quota. [OpenAI documentation](https://developers.openai.com/api/docs/guides/tools-web-search)

## Review the prospect list

`runs/excelas/research/review_queue.csv` remains the single 19-column list.

| Columns | Purpose |
| --- | --- |
| `prospect_id`, `rank`, `name`, `job_title`, `company`, `focus_areas` | Identity, responsibilities and ranking |
| `why_fit`, `why_fit_sources` | Fit analysis and supporting links |
| `work_email`, `linkedin_url` | Primary and supplementary contact routes |
| `budget`, `authority`, `need`, `timing`, `bant_sources` | BANT and evidence links |
| `lead_score` | Python score |
| `review_decision`, `reviewer`, `review_notes` | Human review |

Check identity, current responsibilities, fit, evidence and contact routes. Set `review_decision` to `Approved`, with reviewer and notes, to submit a row to the preview. Other decisions are `Pending`, `Rejected` and `Needs Research`. For the assignment, review all five real prospects; a preview can process a subset. Rejected and pending rows are excluded. Approval submits a row for validation; it does not override a validation failure.

Edit only the three review columns. Correct research facts in the evidence input and regenerate into a new output directory. Changed research cells fail the row with `RESEARCH_MISMATCH`. The handoff accepts the current 19 columns in their original order, with no older-format compatibility. Rows with research gaps, an unsupported email or missing review details fail individually so other valid rows can continue. A blank email is allowed in the research list; it prevents CRM creation in this email-first prototype.

## HubSpot dry-run

```bash
python main.py handoff --out runs/excelas --crm-snapshot examples/crm_snapshot_mock.csv
```

The local simulated CRM snapshot requires `record_id,email,first_name,last_name,company`. Optional fields are `secondary_emails` (semicolon-separated), `prospect_id` and `company_domain`. For a future authorised export, rename the custom property columns described below to these snapshot names. Include primary keys and company domains when available to improve matching. The bundled snapshot is fictional.

The handoff writes exactly two files per reproducible batch:

- `crm_dry_run_report.csv`: source CSV row, prospect ID, CREATE/SKIP/FAILED, reason(s), matched CRM ID(s), snapshot reference, name, company, domain and work email.
- `hubspot_import_ready.csv`: only valid, unique proposed new contacts. It contains no update rows. An empty result still has its seven-column header.

There is no HubSpot connection or write. CREATE means eligible for the simulated import, not that a record was created. FAILED means local validation failed, not that HubSpot returned an error. The reviewed prospect CSV is retained in place; no copied approved list or mapping JSON is produced.

## Validation and duplicate rules

- Primary key: existing `prospect_id`, in the form `p_` plus 16 lowercase hexadecimal characters. Python derives it from normalised name and employer. It identifies a prospect in this dataset, not an immutable person ID across employer changes. Unknown or malformed keys fail. Repeated valid keys in the submitted CSV are skipped after the first valid occurrence.
- Name: each first/last name is 1–80 characters, has letters and uses Unicode letters/marks, spaces, apostrophes, hyphens or initials. Accented and Chinese names are accepted. Digits, markup, emoji, control characters and basic placeholders fail. Company and job title are required, up to 200 characters, without control characters.
- Domain: a hostname or HTTP(S) homepage, normalised to lowercase IDNA, without `www.`. Reject malformed labels, spaces, underscores, paths, ports, credentials and IP addresses. A dotted hostname with a valid top-level label is required. This is syntax checking; no DNS, ownership or mailbox-deliverability check is claimed.
- Email: required for creation, one `@`, a valid dotted domain, a dot-atom local part up to 64 characters and a total length up to 254. Whitespace, repeated/leading/trailing local-part dots and URL forms fail. The address must also match the published work email retained in the research evidence. An employer-published email alias can use a different domain from its corporate website.
- Duplicate: check primary key, case-insensitive full email (including snapshot secondary emails), normalised full name + company domain, and full name + company name. Any matching existing record is SKIP; repeated batch identifiers are SKIP after the first valid occurrence. A shared domain alone or a shared name alone does not establish a duplicate. Potential name matches are conservatively skipped for review, not automatically merged. A separate one-contact-per-company batch limit remains in force.
- Missing snapshot: valid rows fail with `MISSING_CRM_SNAPSHOT`; the program cannot establish that they are new. An explicitly supplied empty snapshot represents an empty simulated CRM. Unreadable CSV structure, invalid snapshot Record IDs or an ambiguous saved research primary key abort the run with a CLI error.

Per-row failures aggregate all detected reasons. Typical codes: `INVALID_NAME`, `INVALID_DOMAIN`, `INVALID_EMAIL`, `UNSUPPORTED_EMAIL`, `UNKNOWN_PRIMARY_KEY`, `RESEARCH_MISMATCH`. Skips identify `DUPLICATE_IN_FILE`, `DUPLICATE_IN_CRM` or `COMPANY_LIMIT`. Deterministic batches reuse their existing output directory on rerun.

## HubSpot field mapping

The seven import fields are deliberately small. Company domain comes from `candidates.json`; it does not add a twentieth research-list column.

| Input / origin | Import CSV header | HubSpot contact property |
| --- | --- | --- |
| Reviewed `prospect_id` | Prospect ID | `excelas_prospect_id` — custom text, require unique values |
| Candidate `first_name` | First name | `firstname` |
| Candidate `last_name` | Last name | `lastname` |
| Candidate `job_title` | Job title | `jobtitle` |
| Reviewed source-supported `work_email` | Email | `email` |
| Candidate `company` | Company name | `company` |
| Validated candidate `company_domain` | Company domain | `excelas_company_domain` — custom text |

These two custom properties must be configured before any future real import; this prototype does not create them. Map all columns to **Contacts**, including Company domain, which is a custom contact field here. This sample creates no company objects or associations. Why Fit and BANT remain in the reviewed list. The external prospect key supplements the unique Email value. HubSpot accepts custom unique-value properties for imports; Email must still be unique. [HubSpot import format](https://knowledge.hubspot.com/import-and-export/set-up-your-import-file)

HubSpot normally updates an existing contact when an imported email matches. The local SKIP prefilter implements this assignment's duplicate rule before generating the import CSV. A future real run needs a fresh authorised CRM export and [the create-only import option](https://knowledge.hubspot.com/import-and-export/import-records-for-a-single-object) to guard against records added after that snapshot. This dry-run makes no concurrency guarantee. [HubSpot deduplication](https://knowledge.hubspot.com/records/deduplication-of-records)

## Offline demonstration

```bash
python main.py research --seed examples/synthetic_evidence.json --target 3 --out runs/demo
python main.py handoff --review examples/synthetic_review.csv --crm-snapshot examples/crm_snapshot_mock.csv --out runs/demo
python -m unittest discover -s tests -v
```

Expected: Ben Example is CREATE; Alice Example is SKIP because she is already in the simulated CRM; Chris Example123 is FAILED for intentionally invalid name, invalid domain and missing email. Chris is deliberately malformed test data, not a real prospect. Dana is rejected and Ellis is pending. The import CSV contains Ben only. Tests additionally cover duplicate file keys, name/domain matches, primary/secondary emails, unknown IDs, international names and failure isolation.

## Codex-assisted research and scale

```bash
python main.py research --seed ../excelas-five-companies-v5/inputs/public_evidence_seed.json --icp-config ../excelas-five-companies-v5/inputs/icp_plan.json --target 5 --out runs/five-companies
python main.py research --icp-config runs/five-companies/research/icp_config.json --target 50 --out runs/scale-50
```

Codex-assisted replay uses recorded public evidence and analyses; Python validates, ranks and selects one contact per company. The standalone API route can perform discovery and enrichment when configured. Research saves the exact prompt, plan, internal evidence store and review CSV. Search directions are Installation QA/QC, Electrical, Cabling and Operations, with overlapping responsibility labels. Explicit personal UK responsibility ranks first; company-level UK evidence carries a confirmation note. Budget and purchase timing stay Unknown without direct evidence. The score weights are use-case fit 35, possible influence 20, company signals 20, evidence 15 and contactability 10. Search uses batches of up to six and about two candidate slots per intended approval; add evidence when the pool falls short. Human review remains required.

## Delivery

Keep real contact files and CRM snapshots under controlled access. A private repository must grant reviewers access. `.env`, virtual environments, `runs/` and `private/` are ignored; never commit API keys or credentials. Store only synthetic examples in a public source repository. Before any future outreach, assess UK GDPR and PECR, including sole traders and some partnerships. [ICO guidance](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/)
