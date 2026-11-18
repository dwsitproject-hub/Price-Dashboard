"""CSV export hardening against formula injection (OWASP "CSV Injection").

Spreadsheet programs run a cell that starts with = + - @ (or TAB / CR) as a formula. Text that reached the database
from users (an e-mail typed at a failed sign-in, a display name, a filename) is prefixed with a single quote so it
opens as text. Numbers are left alone, so negative values stay numeric.
"""
from __future__ import annotations

_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def cell(value):
    if isinstance(value, str) and value.startswith(_LEADERS):
        return "'" + value
    return value


def row(values) -> list:
    return [cell(v) for v in values]
