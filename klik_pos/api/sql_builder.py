import re

import frappe
from frappe.desk.reportview import build_match_conditions

_CLAUSE_ORDER = [
    "WHERE",
    "GROUP BY",
    "HAVING",
    "ORDER BY",
    "LIMIT",
    "OFFSET",
]

_RESERVED_ALIAS_TOKENS = {
    "WHERE",
    "GROUP",
    "HAVING",
    "ORDER",
    "LIMIT",
    "OFFSET",
    "INNER",
    "LEFT",
    "RIGHT",
    "FULL",
    "CROSS",
    "JOIN",
    "ON",
}


def _find_clause_positions(sql_upper: str):
    positions = {}
    for clause in _CLAUSE_ORDER:
        match = re.search(rf"\b{clause}\b", sql_upper)
        if match:
            positions[clause] = match.start()
    return positions


def _extract_doctype_aliases(sql: str, primary_only: bool = False):
    """Extract DocTypes and aliases from SQL query. Returns dict {doctype: alias}."""
    aliases = {}

    table_pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+`tab([^`]+)`"
        r"(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?",
        re.IGNORECASE,
    )

    for match in table_pattern.finditer(sql):
        doctype = match.group(1)
        alias = match.group(2)

        if alias and alias.upper() in _RESERVED_ALIAS_TOKENS:
            alias = None

        aliases[doctype] = alias or f"`tab{doctype}`"

        if primary_only:
            break

    return aliases


_DENIED_FLAG = "klik_denied_doctypes"


def _record_denied_doctype(doctype: str):
    """Remember that this request was denied a doctype.

    apply_sql_permissions degrades a permission failure to `0=1` so one missing perm on a
    peripheral doctype cannot take the whole POS down. That is the right call, but it used
    to throw away *which* doctype was denied, leaving callers unable to tell "no rows" from
    "not allowed" — so the POS served confident zeros and empty grids with no way to explain
    itself. Recording the doctype is what lets an endpoint say so.

    frappe.flags is per-request, so there is no bleed between requests.
    """
    denied = frappe.flags.get(_DENIED_FLAG)
    if denied is None:
        denied = set()
        frappe.flags[_DENIED_FLAG] = denied
    denied.add(doctype)


def record_denied_doctype(doctype: str):
    """Public alias — for callers outside this module that degrade a denial themselves."""
    _record_denied_doctype(doctype)


def get_denied_doctypes() -> frozenset:
    """Doctypes this request was denied by apply_sql_permissions."""
    return frozenset(frappe.flags.get(_DENIED_FLAG) or ())


def reset_denied_doctypes():
    """Clear the record. Call at the start of an endpoint so a reused worker context or a
    test does not inherit a previous run's denials."""
    frappe.flags[_DENIED_FLAG] = set()


def describe_denied_doctypes(doctypes) -> str:
    """Human-readable list for a degraded_reason message."""
    names = sorted(doctypes)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def apply_sql_permissions(sql: str):
    """Automatically inject Frappe permission conditions into SQL query."""
    try:
        # Only apply permissions to the primary table, not subquery tables
        doctype_aliases = _extract_doctype_aliases(sql, primary_only=True)

        if not doctype_aliases:
            return sql

        permission_conditions = []

        for doctype, alias in doctype_aliases.items():
            try:
                # build_match_conditions() already escapes literal "%" to "%%"
                # for the subsequent frappe.db.sql() % formatting pass; escaping
                # again here would double-escape and corrupt the condition.
                rule = build_match_conditions(doctype)

                if rule:
                    if alias != f"`tab{doctype}`":
                        rule = rule.replace(f"`tab{doctype}`", alias)

                    permission_conditions.append(f"({rule})")

            except frappe.PermissionError:
                # No read/select access to this doctype at all: the caller's query
                # keeps its original %s placeholders (and arg count), so inject a
                # condition that always evaluates false instead of replacing the
                # whole query text, which would desync placeholders from args and
                # crash frappe.db.sql() with "not all arguments converted".
                permission_conditions.append("0=1")
                _record_denied_doctype(doctype)

        if not permission_conditions:
            return sql

        permission_sql = " AND ".join(permission_conditions)

        sql_clean = sql.strip()
        sql_upper = sql_clean.upper()

        positions = _find_clause_positions(sql_upper)

        if "WHERE" in positions:
            where_pos = positions["WHERE"]
            insert_pos = where_pos + len("WHERE")

            next_clauses = [pos for clause, pos in positions.items() if pos > where_pos]
            end_pos = min(next_clauses) if next_clauses else len(sql_clean)

            return (
                sql_clean[:insert_pos]
                + f" {permission_sql} AND "
                + sql_clean[insert_pos:end_pos]
                + sql_clean[end_pos:]
            )

        insert_pos = len(sql_clean)
        for clause in ["GROUP BY", "HAVING", "ORDER BY", "LIMIT", "OFFSET"]:
            if clause in positions:
                insert_pos = min(insert_pos, positions[clause])

        return sql_clean[:insert_pos] + f" WHERE {permission_sql} " + sql_clean[insert_pos:]

    except Exception as e:
        frappe.log_error(
            f"Error applying SQL permissions: {str(e)}\nSQL: {sql}",
            "SQL Permission Error",
        )
        return sql
