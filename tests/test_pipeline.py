import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, rows[0])
        writer.writeheader()
        writer.writerows(rows)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.out = Path(directory.name) / "run"
        self.snapshot = EXAMPLES / "crm_snapshot_mock.csv"
        self.cli("research", "--target", "3", "--seed", EXAMPLES / "synthetic_evidence.json")
        self.queue = self.out / "research" / "review_queue.csv"
        self.rows = read_csv(self.queue)

    def cli(self, *args, expected=0):
        env = {name: value for name, value in os.environ.items() if name != "OPENAI_API_KEY"}
        result = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), *map(str, args), "--out", str(self.out)],
            cwd=ROOT, capture_output=True, text=True, env=env, check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def approve(self):
        for row in self.rows:
            first = row["name"].split()[0]
            if first in {"Alice", "Ben", "Chris"}:
                row.update(review_decision="Approved", reviewer="Fixture reviewer", review_notes="Synthetic example.")
            elif first == "Dana":
                row["review_decision"] = "Rejected"
        write_csv(self.queue, self.rows)

    def test_compact_queue_and_normal_handoff(self):
        self.assertEqual(len(self.rows[0]), 19)
        self.assertNotIn("research_fingerprint", self.rows[0])
        self.assertNotIn("email_source", self.rows[0])
        self.assertFalse((self.out / "research" / "ranked_candidates.csv").exists())
        self.cli("handoff", "--crm-snapshot", self.snapshot, expected=1)
        self.approve()
        self.cli("handoff", "--crm-snapshot", self.snapshot)
        batch, = (self.out / "handoff").iterdir()
        data = json.loads((self.out / "research" / "candidates.json").read_text())
        self.assertTrue(all("research_fingerprint" not in record for record in data["records"]))
        report = read_csv(batch / "crm_dry_run_report.csv")
        self.assertEqual({row["crm_action"] for row in report}, {"CREATE", "SKIP", "FAILED"})
        imports = {row["First name"]: row for row in read_csv(batch / "hubspot_import_ready.csv")}
        self.assertEqual(set(imports), {"Ben"})
        self.assertTrue(imports["Ben"]["Prospect ID"].startswith("p_"))
        self.assertEqual(imports["Ben"]["Company domain"], "example.org")
        self.assertEqual(imports["Ben"]["Email"], "ben@example.org")
        self.assertEqual({p.name for p in batch.iterdir()}, {"crm_dry_run_report.csv", "hubspot_import_ready.csv"})
        self.assertIn("Existing dry-run", self.cli("handoff", "--crm-snapshot", self.snapshot).stdout)

    def test_missing_snapshot_and_changed_research_fields(self):
        self.approve()
        self.cli("handoff")
        batch, = (self.out / "handoff").iterdir()
        self.assertEqual({r["crm_action"] for r in read_csv(batch / "crm_dry_run_report.csv")}, {"FAILED"})
        self.rows[0]["job_title"] = "Changed role"
        write_csv(self.queue, self.rows)
        self.cli("handoff")
        batches = list((self.out / "handoff").iterdir())
        self.assertEqual(len(batches), 2)
        report = read_csv(next(path for path in batches if path != batch) / "crm_dry_run_report.csv")
        self.assertTrue(any("RESEARCH_MISMATCH" in row["reason"] for row in report))

    def test_local_run_uses_the_supplied_icp(self):
        from excelas.research import SEGMENTS

        plan = {"segments": [{"segment": segment, "responsibilities": ["Custom evidence review duty"],
                "pain_hypotheses": ["A hypothesis to verify"], "target_titles": ["UK Delivery Manager"],
                "search_queries": ["UK named project responsibility"]} for segment in SEGMENTS]}
        plan_path = self.out.parent / "custom_icp.json"
        plan_path.write_text(json.dumps(plan))
        self.out = self.out.parent / "custom_plan_run"
        self.cli("research", "--target", "3", "--seed", EXAMPLES / "synthetic_evidence.json", "--icp-config", plan_path)
        saved = json.loads((self.out / "research" / "icp_config.json").read_text())
        self.assertEqual(saved["segments"], plan["segments"])
        prompt = (self.out / "research" / "icp_prompt.txt").read_text()
        self.assertIn("FOUR SEARCH DIRECTIONS", prompt)
        self.assertIn("UK RELEVANCE", prompt)

    def test_review_requires_current_19_columns(self):
        self.approve()
        for obsolete in ("verified_work_email", "email_evidence_url", "crm_record_id", "research_fingerprint"):
            changed = [{**row, obsolete: "old value"} for row in self.rows]
            write_csv(self.queue, changed)
            self.assertIn("current 19-column", self.cli("handoff", expected=1).stderr)
        old_rows = [{key: row[key] for key in ("prospect_id", "review_decision", "reviewer", "review_notes")}
                    for row in self.rows]
        write_csv(self.queue, old_rows)
        self.assertIn("missing or duplicate headers", self.cli("handoff", expected=1).stderr)

    def test_existing_email_and_name_duplicates_are_skipped(self):
        self.approve()
        snapshot = read_csv(self.snapshot)
        snapshot.append({**snapshot[0], "record_id": "1002"})
        custom = self.out.parent / "ambiguous.csv"
        write_csv(custom, snapshot)
        self.cli("handoff", "--crm-snapshot", custom)
        batch, = (self.out / "handoff").iterdir()
        report = {row["name"]: row for row in read_csv(batch / "crm_dry_run_report.csv")}
        self.assertEqual(report["Alice Example"]["crm_action"], "SKIP")
        self.assertIn("DUPLICATE_IN_CRM", report["Alice Example"]["reason"])
        self.assertEqual({r["First name"] for r in read_csv(batch / "hubspot_import_ready.csv")}, {"Ben"})
        custom = self.out.parent / "possible_duplicate.csv"
        write_csv(custom, [{**snapshot[0], "email": "other@example.com"}])
        self.cli("handoff", "--crm-snapshot", custom)
        latest = next(path for path in (self.out / "handoff").iterdir() if path != batch)
        report = {row["name"]: row for row in read_csv(latest / "crm_dry_run_report.csv")}
        self.assertIn("DUPLICATE_IN_CRM", report["Alice Example"]["reason"])

    def test_removed_fit_and_contact_fields_are_rejected(self):
        from pydantic import ValidationError
        from excelas.research import Enriched

        raw = json.loads((EXAMPLES / "synthetic_evidence.json").read_text())["records"][0]
        for field in ("role_relevance", "use_case", "buyer_value", "qualification_gap"):
            changed = json.loads(json.dumps(raw))
            changed["analysis"]["why_fit"][field] = "old field"
            with self.assertRaises(ValidationError):
                Enriched.model_validate(changed)
        raw["candidate"]["contact_url"] = "https://example.com/contact"
        with self.assertRaises(ValidationError):
            Enriched.model_validate(raw)

    def test_company_selection_matches_domains_and_names(self):
        from excelas.research import Enriched, qualify, sort_key, unique_companies

        records = json.loads((EXAMPLES / "synthetic_evidence.json").read_text())["records"]
        records[1]["candidate"]["company_domain"] = "HTTPS://WWW.EXAMPLE.COM/"
        scored = sorted([qualify(Enriched.model_validate(record)) for record in records], key=sort_key)
        self.assertEqual(len(unique_companies(scored)), 4)
        records[1]["candidate"]["company_domain"] = "different.example"
        records[1]["candidate"]["company"] = records[0]["candidate"]["company"].upper()
        scored = sorted([qualify(Enriched.model_validate(record)) for record in records], key=sort_key)
        self.assertEqual(len(unique_companies(scored)), 4)

    def test_handoff_skips_second_contact_from_one_company(self):
        from excelas.research import Enriched, qualify, queue_row
        from excelas.handoff import approved_rows, plan

        records = json.loads((EXAMPLES / "synthetic_evidence.json").read_text())["records"][:2]
        records[1]["candidate"]["company_domain"] = records[0]["candidate"]["company_domain"]
        scored = [qualify(Enriched.model_validate(record)) for record in records]
        reviews = [{**{key: str(value).strip() for key, value in queue_row(record, rank).items()},
                    "review_decision": "Approved", "reviewer": "Fixture reviewer", "review_notes": "Checked"}
                   for rank, record in enumerate(scored, 1)]
        results = plan(approved_rows({"target": 2, "records": scored}, reviews), [])
        self.assertEqual([row[0] for row in results], ["CREATE", "SKIP"])
        self.assertIn("COMPANY_LIMIT", results[1][1])

    def test_overlapping_responsibilities_reach_the_csv(self):
        seed = json.loads((EXAMPLES / "synthetic_evidence.json").read_text())
        seed["records"][0]["candidate"]["focus_areas"] = ["Electrical", "QA", "Commissioning"]
        seed_path = self.out.parent / "overlapping_roles.json"
        seed_path.write_text(json.dumps(seed))
        self.out = self.out.parent / "overlapping_run"
        self.cli("research", "--target", "1", "--seed", seed_path)
        rows = read_csv(self.out / "research" / "review_queue.csv")
        alice = next(row for row in rows if row["name"] == "Alice Example")
        self.assertEqual(alice["focus_areas"], "Electrical | QA | Commissioning")
        self.assertNotIn("segment", alice)
        self.assertEqual(len(alice), 19)

    def test_analysis_links_are_preserved_and_unbacked_budget_is_unknown(self):
        from excelas.research import Enriched, qualify, queue_row

        raw = json.loads((EXAMPLES / "synthetic_evidence.json").read_text())["records"][0]
        biography = raw["candidate"]["evidence"][0]["url"]
        raw["analysis"]["authority"] = {"status": "Inference", "note": "Possible influence", "urls": [biography]}
        raw["analysis"]["budget"] = {"status": "Evidence", "note": "Unbacked claim", "urls": [biography]}
        record = qualify(Enriched.model_validate(raw))
        row = queue_row(record, 1)
        self.assertIn(biography, row["why_fit_sources"])
        self.assertEqual(row["bant_sources"], f"Authority: {biography}")
        self.assertTrue(row["budget"].startswith("Unknown"))
        self.assertNotIn("why_fit_use_case", row)
        self.assertNotIn("why_fit_evidence", row)
        self.assertNotIn("https://", row["authority"])

    def test_work_email_and_supplementary_linkedin(self):
        from excelas.contacts import contact_summary

        person = json.loads((EXAMPLES / "synthetic_evidence.json").read_text())["records"][0]["candidate"]
        linkedin = {"kind": "LinkedIn", "value": "https://www.linkedin.com/in/alice-example",
                    "source_url": "https://www.linkedin.com/in/alice-example", "identity_status": "Matched",
                    "identity_note": "Matched fictional professional profile"}
        person["contacts"].insert(0, linkedin)
        contact = contact_summary(person)
        self.assertEqual(contact["work_email"], "alice@example.com")
        self.assertEqual(contact["linkedin_url"], linkedin["value"])
        person["contacts"] = [linkedin]
        contact = contact_summary(person)
        self.assertEqual(contact["work_email"], "")
        self.assertEqual(contact["linkedin_url"], linkedin["value"])

    def test_explicit_uk_responsibility_has_priority(self):
        from excelas.research import Enriched, qualify, queue_row, sort_key

        seed = json.loads((EXAMPLES / "synthetic_evidence.json").read_text())
        first, second = seed["records"][:2]
        for item in second["candidate"]["evidence"]:
            if item["kind"] == "uk_scope":
                item["scope"] = "Person"
                item["note"] = "The fictional role explicitly manages UK installation projects."
        general = qualify(Enriched.model_validate(first))
        specific = qualify(Enriched.model_validate(second))
        general["lead_score"], specific["lead_score"] = 100, 20
        ranked = sorted([general, specific], key=sort_key)
        self.assertEqual(ranked[0]["prospect_id"], specific["prospect_id"])
        self.assertIn("awaits confirmation", queue_row(general, 2)["review_notes"])
        self.assertEqual(queue_row(specific, 1)["review_notes"], "")

    def test_guessed_email_and_employer_conflict(self):
        from excelas.research import Enriched, qualify
        from excelas.handoff import approved_rows

        raw = json.loads((EXAMPLES / "synthetic_evidence.json").read_text())["records"][0]
        raw["candidate"]["contacts"][0]["publication_excerpt"] = "No email published."
        record = qualify(Enriched.model_validate(raw))
        from excelas.research import queue_row
        self.assertEqual(queue_row(record, 1)["work_email"], "")
        raw["candidate"]["contacts"] = [{
            "kind": "LinkedIn", "value": "https://www.linkedin.com/in/alice-example",
            "source_url": "https://www.linkedin.com/in/alice-example", "identity_status": "Conflict",
            "identity_note": "The same person's current employer differs.",
        }]
        record = qualify(Enriched.model_validate(raw))
        row = queue_row(record, 1)
        self.assertEqual(row["review_decision"], "Needs Research")
        row.update(review_decision="Approved", reviewer="Fixture reviewer")
        row = {name: str(value) for name, value in row.items()}
        from excelas.handoff import plan
        row["review_notes"] = "Reviewed synthetic failure"
        result, = plan(approved_rows({"target": 1, "records": [record]}, [row]), [])
        self.assertEqual(result[0], "FAILED")
        self.assertIn("RESEARCH_GAPS", result[1])

    def test_duplicate_csv_primary_key_is_skipped_with_source_row(self):
        self.approve()
        ben = next(row for row in self.rows if row["name"] == "Ben Example")
        self.rows.append(dict(ben))
        write_csv(self.queue, self.rows)
        self.cli("handoff", "--crm-snapshot", self.snapshot)
        batch, = (self.out / "handoff").iterdir()
        report = read_csv(batch / "crm_dry_run_report.csv")
        self.assertEqual(sum(row["crm_action"] == "CREATE" for row in report), 1)
        duplicate, = [row for row in report if "DUPLICATE_IN_FILE" in row["reason"]]
        self.assertIn("primary_key", duplicate["reason"])
        self.assertEqual(duplicate["source_row"], "7")
        self.assertEqual(len(read_csv(batch / "hubspot_import_ready.csv")), 1)

    def test_invalid_rows_report_all_reasons_without_blocking_good_rows(self):
        self.approve()
        self.cli("handoff", "--crm-snapshot", self.snapshot)
        batch, = (self.out / "handoff").iterdir()
        report = read_csv(batch / "crm_dry_run_report.csv")
        failed, = [row for row in report if row["crm_action"] == "FAILED"]
        for code in ("INVALID_NAME", "INVALID_DOMAIN", "INVALID_EMAIL"):
            self.assertIn(code, failed["reason"])
        self.assertEqual(len(read_csv(batch / "hubspot_import_ready.csv")), 1)

    def test_unknown_and_invalid_primary_keys_report_failures(self):
        self.approve()
        for pid, code in (("p_0000000000000000", "UNKNOWN_PRIMARY_KEY"), ("bad id", "INVALID_PRIMARY_KEY")):
            ben = next(row for row in self.rows if row["name"] == "Ben Example")
            ben["prospect_id"] = pid
            write_csv(self.queue, self.rows)
            self.cli("handoff", "--crm-snapshot", self.snapshot)
            reports = [row for batch in (self.out / "handoff").iterdir()
                       for row in read_csv(batch / "crm_dry_run_report.csv")]
            self.assertTrue(any(code in row["reason"] for row in reports))

    def test_snapshot_primary_key_and_name_domain_and_secondary_email(self):
        self.approve()
        ben = next(row for row in self.rows if row["name"] == "Ben Example")
        variants = [
            {"prospect_id": ben["prospect_id"], "email": "old@other.example", "first_name": "Old", "last_name": "Person", "company": "Other"},
            {"email": "old@example.org", "first_name": "BEN", "last_name": "EXAMPLE", "company": "Renamed employer", "company_domain": "HTTPS://WWW.EXAMPLE.ORG/"},
            {"email": "old@other.example", "first_name": "Other", "last_name": "Person", "company": "Other", "secondary_emails": "BEN@EXAMPLE.ORG"},
        ]
        for number, variant in enumerate(variants):
            snapshot = self.out.parent / f"snapshot-{number}.csv"
            write_csv(snapshot, [{"record_id": "2001", "email": "", "first_name": "", "last_name": "", "company": "", **variant}])
            self.cli("handoff", "--crm-snapshot", snapshot)
        for batch in (self.out / "handoff").iterdir():
            report = read_csv(batch / "crm_dry_run_report.csv")
            row, = [row for row in report if row["name"] == "Ben Example"]
            self.assertEqual(row["crm_action"], "SKIP")
            self.assertEqual(row["matched_record_id"], "2001")


class ValidationTest(unittest.TestCase):
    def test_names_allow_international_names_and_reject_bad_format(self):
        from excelas.validation import valid_name
        for name in ("Glen", "Perrier-Flint", "O’Neill", "Zoë", "José", "J.", "张三", "प्रिया", "Mary Jane"):
            self.assertTrue(valid_name(name), name)
        for name in ("", " ", "123", "Chris123", "A_B", "<script>", "John\nDoe", "😀", "Unknown", "A--B"):
            self.assertFalse(valid_name(name), name)

    def test_domain_and_email_format(self):
        from excelas.validation import domain, email
        for value in ("example.co.uk", "HTTPS://WWW.EXAMPLE.CO.UK/"):
            self.assertEqual(domain(value), "example.co.uk")
        for value in ("", "localhost", "bad_domain.com", "-bad.com", "bad-.com", "127.0.0.1", "example.com:443", "example.com:", "a..com", "https://example.com/staff", "user@example.com", "a b.com"):
            self.assertEqual(domain(value), "", value)
        self.assertEqual(email("Jane.O'Neil+qa@Example.CO.UK"), "jane.o'neil+qa@example.co.uk")
        for value in ("", "a@@b.com", "a..b@example.com", ".a@example.com", "a.@example.com", "a b@example.com", "a@bad_domain.com", "a@https://example.com", "a@example.com/path", "a@localhost"):
            self.assertEqual(email(value), "", value)

    def test_published_employer_email_alias_is_accepted(self):
        from excelas.research import Enriched, qualify, queue_row
        from excelas.validation import validation_errors
        raw = json.loads((EXAMPLES / "synthetic_evidence.json").read_text())["records"][0]
        raw["candidate"]["company_domain"] = "corporate.example"
        record = qualify(Enriched.model_validate(raw))
        _, _, errors = validation_errors(record, queue_row(record, 1))
        self.assertEqual(errors, [])

    def test_file_dedup_uses_email_and_name_with_domain_independently(self):
        from excelas.research import Enriched, qualify, queue_row
        from excelas.handoff import approved_rows, plan
        originals = json.loads((EXAMPLES / "synthetic_evidence.json").read_text())["records"][:2]
        for mode in ("email", "name_domain"):
            raw = json.loads(json.dumps(originals))
            if mode == "email":
                raw[1]["candidate"]["contacts"] = raw[0]["candidate"]["contacts"]
            else:
                raw[1]["candidate"].update(first_name="Alice", last_name="Example", company_domain="example.com")
            records = [qualify(Enriched.model_validate(item)) for item in raw]
            reviews = [{**{field: str(value).strip() for field, value in queue_row(record, rank).items()},
                        "review_decision": "Approved", "reviewer": "Fixture", "review_notes": "Synthetic check"}
                       for rank, record in enumerate(records, 1)]
            result = plan(approved_rows({"records": records}, reviews), [])
            self.assertEqual([row[0] for row in result], ["CREATE", "SKIP"])
            self.assertIn(mode, result[1][1])


if __name__ == "__main__":
    unittest.main()
