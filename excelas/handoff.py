"""Validate approved rows and preview create-only HubSpot imports locally."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

from excelas.data import csv_value, digest, key, read_csv, read_json, write_csv
from excelas.research import DISPLAY, QUEUE_COLUMNS, company_keys, queue_row
from excelas.validation import PRIMARY_KEY, domain, email, validation_errors

IMPORT_FIELDS = {
    "prospect_id": "Prospect ID",
    "first_name": "First name",
    "last_name": "Last name",
    "job_title": "Job title",
    "email": "Email",
    "company": "Company name",
    "company_domain": "Company domain",
}
IMPORT_HEADERS = tuple(IMPORT_FIELDS.values())
SNAPSHOT_HEADERS = ("record_id", "email", "first_name", "last_name", "company")
REPORT_HEADERS = ("source_row", "prospect_id", "crm_action", "reason", "matched_record_id", "snapshot",
                  "name", "company", "company_domain", "work_email")
DECISIONS = {"", "pending", "approved", "rejected", "needs research"}


def name_key(first, last):
    return key(unicodedata.normalize("NFC", f"{first} {last}"))


def approved_rows(data, reviews):
    records = {record["prospect_id"]: (record, rank) for rank, record in enumerate(data["records"], 1)}
    if len(records) != len(data["records"]):
        raise ValueError("Saved research contains duplicate primary keys; regenerate the research.")
    approved = []
    for source_row, row in enumerate(reviews, 2):
        decision = row["review_decision"].casefold()
        if decision not in DECISIONS:
            raise ValueError(f"Invalid review decision at CSV row {source_row}")
        if decision == "approved":
            record, rank = records.get(row["prospect_id"], (None, 0))
            approved.append((record, row, rank))
    if not approved:
        raise ValueError("No approved rows. Review the prospect CSV before preparing an import.")
    return approved


def plan(approved, snapshot):
    existing = defaultdict(set)
    for row in snapshot or []:
        record_id = row["record_id"]
        if row.get("prospect_id"):
            existing[("primary_key", row["prospect_id"])].add(record_id)
        addresses = [row["email"], *row.get("secondary_emails", "").split(";")]
        for address in addresses:
            if normalised := email(address):
                existing[("email", normalised)].add(record_id)
        person_name = name_key(row["first_name"], row["last_name"])
        employer_domain = domain(row.get("company_domain", "")) or domain(row["email"].rpartition("@")[2])
        if person_name and employer_domain:
            existing[("name_domain", person_name, employer_domain)].add(record_id)
        if person_name and key(row["company"]):
            existing[("name_company", person_name, key(row["company"]))].add(record_id)

    seen, companies, results = set(), set(), []
    for record, review, rank in approved:
        pid = review["prospect_id"]
        if record is None:
            code = "UNKNOWN_PRIMARY_KEY" if PRIMARY_KEY.fullmatch(pid) else "INVALID_PRIMARY_KEY"
            results.append(("FAILED", f"{code}: no matching saved research record", "", review["work_email"]))
            continue
        work_email, employer_domain, errors = validation_errors(record, review)
        expected = queue_row(record, rank)
        changed = [field for field in DISPLAY if review[field] != csv_value(expected[field]).strip()]
        if changed:
            errors.append("RESEARCH_MISMATCH: edited or stale fields: " + ", ".join(changed))
        if not review["reviewer"] or not review["review_notes"]:
            errors.append("MISSING_REVIEW: reviewer and review_notes are required")
        if errors:
            results.append(("FAILED", " | ".join(errors), "", review["work_email"]))
            continue
        person = record["candidate"]
        person_name = name_key(person["first_name"], person["last_name"])
        identities = [("primary_key", pid), ("email", work_email),
                      ("name_domain", person_name, employer_domain),
                      ("name_company", person_name, key(person["company"]))]
        duplicates = [item[0] for item in identities if item in seen]
        seen.update(identities)
        if duplicates:
            results.append(("SKIP", "DUPLICATE_IN_FILE: " + ", ".join(duplicates), "", work_email))
            continue
        hits = {record_id for item in identities for record_id in existing[item]}
        if hits:
            matched_by = [item[0] for item in identities if existing[item]]
            results.append(("SKIP", "DUPLICATE_IN_CRM: " + ", ".join(matched_by), ";".join(sorted(hits)), work_email))
            continue
        company_identity = company_keys(person)
        if not company_identity.isdisjoint(companies):
            results.append(("SKIP", "COMPANY_LIMIT: this batch already has one contact from the company", "", work_email))
            continue
        if snapshot is None:
            results.append(("FAILED", "MISSING_CRM_SNAPSHOT: cannot check existing records", "", work_email))
            continue
        companies.update(company_identity)
        results.append(("CREATE", "VALID_NEW_RECORD: format and uniqueness checks passed", "", work_email))
    return results


def handoff_run(out, review_path, snapshot_path):
    research = out / "research"
    data = read_json(research / "candidates.json")
    reviews = read_csv(review_path or research / "review_queue.csv", QUEUE_COLUMNS, exact=True)
    approved = approved_rows(data, reviews)
    snapshot = read_csv(snapshot_path, SNAPSHOT_HEADERS) if snapshot_path else None
    if snapshot and any(not re.fullmatch(r"[1-9][0-9]*", row["record_id"]) for row in snapshot):
        raise ValueError("CRM snapshot contains an invalid Record ID; use positive numeric IDs.")
    snapshot_label = snapshot_path.name if snapshot_path else "No simulated snapshot supplied"
    batch = digest([6, data, reviews, snapshot, snapshot_label])[:16]
    handoffs = out / "handoff"
    destination = handoffs / batch
    if destination.exists():
        print(f"Existing dry-run: {destination}")
        return
    handoffs.mkdir(parents=True, exist_ok=True)
    imports, report = [], []
    source_rows = {id(row): index for index, row in enumerate(reviews, 2)}
    for (record, review, _), (action, reason, record_id, work_email) in zip(approved, plan(approved, snapshot), strict=True):
        person = record["candidate"] if record else {}
        report.append({
            "source_row": source_rows[id(review)], "prospect_id": review["prospect_id"],
            "crm_action": action, "reason": reason, "matched_record_id": record_id, "snapshot": snapshot_label,
            "name": review["name"], "company": review["company"],
            "company_domain": person.get("company_domain", ""), "work_email": work_email,
        })
        if action == "CREATE":
            values = {**person, "prospect_id": review["prospect_id"], "email": work_email,
                      "company_domain": domain(person["company_domain"])}
            imports.append({header: values[source] for source, header in IMPORT_FIELDS.items()})
    with TemporaryDirectory(dir=handoffs, prefix=".handoff-") as temporary:
        staging = Path(temporary) / batch
        staging.mkdir()
        write_csv(staging / "crm_dry_run_report.csv", REPORT_HEADERS, report)
        write_csv(staging / "hubspot_import_ready.csv", IMPORT_HEADERS, imports)
        try:
            staging.rename(destination)
        except OSError:
            if not destination.is_dir():
                raise
    counts = Counter(row["crm_action"] for row in report)
    print(f"Dry-run only: {counts['CREATE']} CREATE, {counts['SKIP']} SKIP, {counts['FAILED']} FAILED. {destination}")
