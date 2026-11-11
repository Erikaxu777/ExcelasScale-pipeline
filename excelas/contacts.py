"""Select public professional contact routes and preserve their provenance."""
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from excelas.data import Model, url_key

EMAIL = re.compile(r"[^\s@,;]+@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}", re.I)


class ContactRoute(Model):
    kind: Literal["LinkedIn", "Work email", "Company form"]
    value: str
    source_url: str
    identity_status: Literal["Matched", "Conflict", "Unconfirmed"]
    identity_note: str
    publication_excerpt: str = ""
    verification_status: Literal["Search supplied", "Checked against page", "Synthetic fixture"] = "Search supplied"
    checked_at: str = ""


class ContactSearch(Model):
    routes: list[ContactRoute]


def valid_route(route):
    if not url_key(route.source_url) or not route.identity_note:
        return False
    if route.kind == "Work email":
        published = {address.casefold() for address in EMAIL.findall(route.publication_excerpt)}
        return bool(EMAIL.fullmatch(route.value)) and route.value.casefold() in published
    if not url_key(route.value):
        return False
    if route.kind == "LinkedIn":
        url = urlsplit(route.value)
        host = (url.hostname or "").lower()
        return (host == "linkedin.com" or host.endswith(".linkedin.com")) and bool(
            re.fullmatch(r"/in/[^/]+/?", url.path)
        )
    return True


def contact_summary(person):
    """Select work email as primary and keep LinkedIn as a supplement."""
    routes = [ContactRoute.model_validate(item) for item in person.get("contacts", [])]
    routes = [route for route in routes if valid_route(route)]
    matched = [route for route in routes if route.identity_status == "Matched"]
    email = next((route.value for route in matched if route.kind == "Work email"), "")
    linkedin = next((route.value for route in matched if route.kind == "LinkedIn"), "")
    return {
        "work_email": email,
        "linkedin_url": linkedin,
        "contactability_level": 2 if email else int(bool(matched)),
        "contact_conflict": any(route.identity_status == "Conflict" for route in routes),
        "has_supported_route": bool(matched),
    }
