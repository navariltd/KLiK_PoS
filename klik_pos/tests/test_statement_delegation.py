from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api import statement_of_accounts as soa


class TestStatementDelegation(FrappeTestCase):
	"""klik_pos does not own the statement logic — it forwards to cecypo_frappe_reports.

	These cover the delegation layer only. The statement behaviour itself is tested upstream,
	and duplicating those tests here would test the wrong app's code.
	"""

	def test_is_available_true_when_upstream_resolves(self):
		self.assertEqual(soa.is_available(), {"available": True})

	def test_is_available_false_when_upstream_missing(self):
		with patch.object(soa, "_upstream", side_effect=ImportError("no module")):
			self.assertEqual(soa.is_available(), {"available": False})

	def test_is_available_never_raises(self):
		# The frontend calls this to decide whether to render a button; it must always answer.
		with patch.object(soa, "_upstream", side_effect=Exception("boom")):
			self.assertEqual(soa.is_available(), {"available": False})

	def test_delegate_forwards_arguments_and_returns_the_result(self):
		captured = {}

		def fake(**kwargs):
			captured.update(kwargs)
			return "rendered"

		with patch.object(soa, "_upstream", return_value=fake):
			result = soa._delegate("render_statement_html", customer="ACME", company="Dev Co")

		self.assertEqual(result, "rendered")
		self.assertEqual(captured, {"customer": "ACME", "company": "Dev Co"})

	def test_delegate_forwards_unknown_kwargs_untouched(self):
		# Upstream gaining a parameter must flow through without a klik_pos change.
		captured = {}

		def fake(**kwargs):
			captured.update(kwargs)
			return True

		with patch.object(soa, "_upstream", return_value=fake):
			soa._delegate("email_statement", customer="ACME", some_new_upstream_arg=42)

		self.assertEqual(captured["some_new_upstream_arg"], 42)

	def test_missing_app_throws_a_readable_message_not_an_import_error(self):
		with patch.object(soa, "_upstream", side_effect=ImportError("no module")):
			with self.assertRaises(frappe.ValidationError) as ctx:
				soa._delegate("render_statement_html", customer="ACME")
		self.assertIn("Cecypo Frappe Reports", str(ctx.exception))

	def test_missing_attribute_is_treated_as_missing_app(self):
		with patch.object(soa, "_upstream", side_effect=AttributeError("gone")):
			with self.assertRaises(frappe.ValidationError):
				soa._delegate("render_statement_html", customer="ACME")

	def test_upstream_errors_propagate_unchanged(self):
		# A "no transactions in the period" throw must reach the user as itself, not be
		# swallowed into a generic failure or a {"success": False} envelope.
		def fake(**kwargs):
			frappe.throw("No transactions for ACME in the selected period.")

		with patch.object(soa, "_upstream", return_value=fake):
			with self.assertRaises(frappe.ValidationError) as ctx:
				soa._delegate("render_statement_html", customer="ACME")
		self.assertIn("No transactions", str(ctx.exception))

	def test_forwarded_kwargs_are_accepted_by_the_real_upstream_signatures(self):
		"""Guard against upstream signature drift — the predictable failure of delegating.

		Every other test here uses a **kwargs fake, which accepts any keyword name and so
		cannot catch klik_pos sending `party` where upstream wants `customer`. The live probe
		only exercises three of the five endpoints and never calls download or email. This
		binds each forwarded argument set against the REAL upstream signature without
		invoking it, so a rename upstream fails here instead of in the browser.
		"""
		import inspect

		forwarded = {
			"get_statement_templates": {"company": "Dev Co"},
			"get_default_recipient": {"party_type": "customer", "party": "ACME"},
			"render_statement_html": {
				"customer": "ACME",
				"company": "Dev Co",
				"template": "AR",
				"as_of_date": None,
			},
			"download_statement": {
				"customer": "ACME",
				"company": "Dev Co",
				"template": "AR",
				"as_of_date": None,
			},
			"email_statement": {
				"customer": "ACME",
				"company": "Dev Co",
				"template": "AR",
				"as_of_date": None,
				"recipient": None,
				"cc": "",
				"bcc": "",
			},
		}

		for method, kwargs in forwarded.items():
			with self.subTest(method=method):
				signature = inspect.signature(soa._upstream(method))
				# Raises TypeError if a name klik_pos forwards is not accepted upstream.
				signature.bind(**kwargs)

	def test_endpoints_delegate_to_their_matching_upstream_name(self):
		seen = []

		def fake(**kwargs):
			return None

		def spy(method):
			seen.append(method)
			return fake

		with patch.object(soa, "_upstream", side_effect=spy):
			soa.get_statement_templates(company="Dev Co")
			soa.get_default_recipient(party_type="customer", party="ACME")
			soa.render_statement_html(customer="ACME", company="Dev Co", template="AR")
			soa.download_statement(customer="ACME", company="Dev Co", template="AR")
			soa.email_statement(customer="ACME", company="Dev Co", template="AR")

		self.assertEqual(
			seen,
			[
				"get_statement_templates",
				"get_default_recipient",
				"render_statement_html",
				"download_statement",
				"email_statement",
			],
		)
