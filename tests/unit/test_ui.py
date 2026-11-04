"""Check dashboard sections, role visibility, and escaped account text."""

from html.parser import HTMLParser

import pytest

from app.api.ui import dashboard


class Sections(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag == "section" and fields.get("id"):
            self.ids.add(fields["id"])
        if tag == "a" and fields.get("href", "").startswith("#"):
            self.links.add(fields["href"][1:])


@pytest.mark.parametrize("role,can_backup,can_audit,can_users", [
    ("admin", True, True, True),
    ("operator", False, False, False),
    ("auditor", False, True, False),
])
def test_dashboard_sections_respect_roles(role: str, can_backup: bool,
                                         can_audit: bool, can_users: bool) -> None:
    html = dashboard("<operator>", role)
    parsed = Sections()
    parsed.feed(html)
    assert {"authorization", "ecology-dashboard", "pii-metrics", "duplicates",
            "recommendations", "about"} <= parsed.ids
    assert ("backups" in parsed.ids) is can_backup
    assert ("journal" in parsed.links) is can_audit
    assert ("users-section" in parsed.links) is can_users
    assert parsed.links <= parsed.ids
    assert "<operator>" not in html and "&lt;operator&gt;" in html
    assert "format-chart" in html and "volume-chart" in html
