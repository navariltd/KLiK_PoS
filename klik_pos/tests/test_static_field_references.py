"""Every literal doctype/field reference in klik_pos must actually resolve.

Three bugs found in one day shared a shape: code referencing something that does not exist,
wrapped in `except Exception: log_error; carry on`. The closing entry appended to a
`custom_sales_invoice` field nobody had created; it raised on every shift close, was swallowed,
and the feature silently never worked on any site.

klik_pos has 213 bare `except Exception` handlers. That is a deliberate design - one missing
permission or one bad link should not take a till offline - but it also means a reference that
can never resolve produces no visible symptom, just a log line nobody reads. These tests close
the statically-detectable half of that gap so the class cannot come back silently.
"""

import ast
import glob
import os

import frappe
from frappe.tests.utils import FrappeTestCase

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERY_FNS = {"get_all", "get_list", "get_value", "set_value", "count", "exists"}

# Fields belonging to apps klik_pos does not depend on. Each is guarded at its call site by
# frappe.db.has_column / frappe.db.exists before use, so a site without the app degrades
# instead of erroring. Add here only with that guard in place.
OPTIONAL_CROSS_APP_FIELDS = {
	("Company", "custom_enable_zatca_e_invoicing"),
}


def _source_files():
	for path in glob.glob(os.path.join(APP_ROOT, "**", "*.py"), recursive=True):
		base = os.path.basename(path)
		if os.sep + "tests" + os.sep in path or base.startswith("test_"):
			continue
		yield path


def _rel(path):
	return path.split("apps" + os.sep + "klik_pos" + os.sep, 1)[-1]


def _literal(node):
	if isinstance(node, ast.Constant) and isinstance(node.value, str):
		return node.value
	return None


def _literal_list(node):
	if isinstance(node, (ast.List, ast.Tuple)):
		return [v for v in (_literal(e) for e in node.elts) if v]
	return []


def _filter_keys(node):
	keys = []
	if isinstance(node, ast.Dict):
		keys = [k for k in (_literal(k) for k in node.keys) if k]
	elif isinstance(node, (ast.List, ast.Tuple)):
		for element in node.elts:
			if isinstance(element, (ast.List, ast.Tuple)) and len(element.elts) >= 3:
				key = _literal(element.elts[0])
				if key:
					keys.append(key)
	return keys


def _valid_columns(doctype):
	"""Own columns plus child-table fields.

	Frappe resolves a child field in a parent's filters by joining the child table - filtering
	Contact by `link_name` (which lives on Dynamic Link) is legitimate - so the child fields
	have to count as valid or the check reports working code.
	"""
	meta = frappe.get_meta(doctype)
	valid = set(meta.get_valid_columns())
	for field in meta.get_table_fields():
		try:
			valid |= set(frappe.get_meta(field.options).get_valid_columns())
		except Exception:
			continue
	return valid


def _field_tokens(raw):
	"""Split an order_by or field expression into bare column names."""
	for chunk in raw.replace(",", " ").split():
		token = chunk.split(".")[-1].strip("`")
		if not token or token in {"*", "asc", "desc"} or "(" in token:
			continue
		yield token


class TestQueryFieldReferences(FrappeTestCase):
	def test_every_literal_query_reference_resolves(self):
		examined, problems = 0, []

		for path in _source_files():
			try:
				tree = ast.parse(open(path).read())
			except SyntaxError:
				continue

			for node in ast.walk(tree):
				if not isinstance(node, ast.Call):
					continue
				if getattr(node.func, "attr", None) not in QUERY_FNS or not node.args:
					continue
				doctype = _literal(node.args[0])
				if not doctype:
					continue

				fn = node.func.attr
				names = []
				for kw in node.keywords:
					if kw.arg in ("fields", "order_by"):
						names += _literal_list(kw.value)
						single = _literal(kw.value)
						if single:
							names.append(single)
					elif kw.arg == "pluck":
						single = _literal(kw.value)
						if single:
							names.append(single)
					elif kw.arg == "filters":
						names += _filter_keys(kw.value)
				if len(node.args) >= 2:
					names += _filter_keys(node.args[1])
				if fn in ("get_value", "set_value") and len(node.args) >= 3:
					single = _literal(node.args[2])
					if single:
						names.append(single)
					names += _literal_list(node.args[2]) + _filter_keys(node.args[2])

				if not names:
					continue
				examined += 1

				if not frappe.db.exists("DocType", doctype):
					problems.append(f"{_rel(path)}:{node.lineno} unknown doctype {doctype!r}")
					continue

				valid = _valid_columns(doctype)
				for raw in names:
					for token in _field_tokens(raw):
						if token in valid or (doctype, token) in OPTIONAL_CROSS_APP_FIELDS:
							continue
						problems.append(f"{_rel(path)}:{node.lineno} {doctype} has no field {token!r}")

		self.assertGreater(examined, 100, "the walker found almost nothing - it is broken")
		self.assertEqual(sorted(set(problems)), [], "unresolvable query references")


class TestChildTableAppendTargets(FrappeTestCase):
	def test_every_literal_append_target_exists(self):
		"""The exact shape of the closing-entry bug: appending to a field nobody created."""
		# `in` rather than LIKE 'Table%': frappe.db.sql treats % as a format placeholder.
		table_types = ("Table", "Table MultiSelect")
		table_fields = set(
			frappe.get_all("DocField", filters={"fieldtype": ("in", table_types)}, pluck="fieldname")
		) | set(frappe.get_all("Custom Field", filters={"fieldtype": ("in", table_types)}, pluck="fieldname"))

		examined, problems = 0, []
		for path in _source_files():
			try:
				tree = ast.parse(open(path).read())
			except SyntaxError:
				continue
			for node in ast.walk(tree):
				if not isinstance(node, ast.Call):
					continue
				if getattr(node.func, "attr", None) != "append" or len(node.args) < 2:
					continue
				target = _literal(node.args[0])
				if not target:
					continue
				examined += 1
				if target not in table_fields:
					problems.append(f"{_rel(path)}:{node.lineno} no child table field {target!r}")

		self.assertGreater(examined, 20, "the walker found almost nothing - it is broken")
		self.assertEqual(sorted(set(problems)), [], "appends to fields that do not exist")
