"""Conservative local format checks; no mailbox or DNS verification."""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

from excelas.contacts import contact_summary

PRIMARY_KEY = re.compile(r"p_[0-9a-f]{16}")
LOCAL_PART = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+")


def domain(value):
    """Accept a hostname or HTTP(S) homepage URL and return its IDNA hostname."""
    value = value.strip()
    if not value or any(char.isspace() for char in value):
        return ""
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
                or ":" in parsed.netloc or parsed.port is not None or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or not parsed.hostname):
            return ""
        host = parsed.hostname.encode("idna").decode("ascii").lower().removeprefix("www.")
        try:
            ipaddress.ip_address(host)
            return ""
        except ValueError:
            pass
        labels = host.split(".")
        if len(host) > 253 or len(labels) < 2 or not re.fullmatch(r"[a-z]{2,63}|xn--[a-z0-9-]{2,59}", labels[-1]):
            return ""
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
            return ""
        return host
    except (ValueError, UnicodeError):
        return ""


def email(value):
    value = value.strip()
    if len(value) > 254 or value.count("@") != 1:
        return ""
    local, host = value.rsplit("@", 1)
    # Host must be bare: never turn an email URL/path/port into a valid address.
    if (not local or len(local) > 64 or not LOCAL_PART.fullmatch(local) or local.startswith(".")
            or local.endswith(".") or ".." in local or any(char in host for char in "/:?#")):
        return ""
    normalised = domain(host)
    return f"{local.casefold()}@{host.lower()}" if normalised else ""


def valid_name(value):
    """Allow Unicode letters/marks, spaces, apostrophes, hyphens and initials."""
    value = unicodedata.normalize("NFC", value.strip())
    if not 1 <= len(value) <= 80 or value.casefold() in {"unknown", "n/a", "none", "null", "test"}:
        return False
    if not any(char.isalpha() for char in value):
        return False
    if not (value[0].isalpha() or value[0] in "'’") or not (unicodedata.category(value[-1])[0] in {"L", "M"} or value[-1] == "."):
        return False
    if any(punctuation in value for punctuation in ("--", "''", "’’", "..")):
        return False
    return all(unicodedata.category(char)[0] in {"L", "M"} or char in " '-’." for char in value)


def validation_errors(record, review):
    person = record["candidate"]
    errors = []
    if not PRIMARY_KEY.fullmatch(review["prospect_id"]):
        errors.append("INVALID_PRIMARY_KEY: expected p_ followed by 16 lowercase hexadecimal characters")
    for field in ("first_name", "last_name"):
        if not valid_name(person[field]):
            errors.append(f"INVALID_NAME: {field} must be 1-80 characters using letters, spaces, apostrophes, hyphens or initials")
    for field in ("company", "job_title"):
        value = person[field]
        if not value or len(value) > 200 or any(unicodedata.category(char)[0] == "C" for char in value):
            errors.append(f"INVALID_FIELD: {field} is required, up to 200 characters, without control characters")
    company_domain = domain(person["company_domain"])
    if not company_domain:
        errors.append("INVALID_DOMAIN: company_domain must be a valid hostname or HTTP(S) homepage, without spaces, paths or IP addresses")
    work_email = email(review["work_email"])
    if not work_email:
        errors.append("INVALID_EMAIL: a valid work email is required")
    elif work_email != email(contact_summary(person)["work_email"]):
        errors.append("UNSUPPORTED_EMAIL: work email has no matching published research evidence")
    if record["research_gaps"]:
        errors.append("RESEARCH_GAPS: " + "; ".join(record["research_gaps"]))
    return work_email, company_domain, errors
