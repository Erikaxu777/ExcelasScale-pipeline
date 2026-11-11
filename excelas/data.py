"""Shared records, evidence rules and file I/O."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def key(value):
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


def url_key(value):
    try:
        url = urlsplit(value.strip())
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
            return ""
        return urlunsplit(("https", url.netloc.lower(), url.path.rstrip("/") or "/", url.query, ""))
    except ValueError:
        return ""


def read_csv(path, required, *, exact=False):
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)) or not set(required).issubset(headers):
            raise ValueError(f"{path.name} has missing or duplicate headers. Required: {', '.join(required)}")
        if exact and headers != list(required):
            raise ValueError(f"{path.name} must use the current {len(required)}-column format in its original order.")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path.name} has a malformed row at line {reader.line_num}")
            rows.append({name: value.strip() for name, value in row.items()})
        return rows


def csv_value(value):
    value = str(value)
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fields)
        writer.writeheader()
        writer.writerows({field: csv_value(row.get(field, "")) for field in fields} for row in rows)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
