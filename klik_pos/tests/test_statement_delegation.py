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
		self.assertEqual(soa.is_available(), {"available": True, "reason": None})

	def test_is_available_false_when_upstream_missing(self):
		with patch.object(soa, "_upstream", side_effect=ImportError("no module")):
			result = soa.is_available()
		self.assertFalse(result["available"])
		self.assertIn("Cecypo Frappe Reports", result["reason"])

	def test_is_available_never_raises(self):
		# The frontend calls this to decide whether to render a button; it must always answer.
		with patch.object(soa, "_upstream", side_effect=Exception("boom")):
			result = soa.is_available()
		self.assertFalse(result["available"])

	def test_delegate_forwards_arguments_and_returns_the_result(self):
		captured = {}

		def fake(**kwargs):
			captured.update(kwargs)
			return "rendered"

		# _upstream_capability is bypassed here: this test is about _delegate's forwarding, not
		# about capability resolution (covered separately), and a bare **kwargs stub returned for
		# every dotted path — including STATEMENT_API_VERSION — is not a real int to check.
		with patch.object(soa, "_upstream_capability", return_value=(True, None)):
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

		with patch.object(soa, "_upstream_capability", return_value=(True, None)):
			with patch.object(soa, "_upstream", return_value=fake):
				soa._delegate("email_statement", customer="ACME", some_new_upstream_arg=42)

		self.assertEqual(captured["some_new_upstream_arg"], 42)

	def test_missing_app_throws_a_readable_message_not_an_import_error(self):
		# frappe.get_attr raises AppNotInstalledError for an app absent from the site, NOT
		# ImportError — mocking ImportError here passed while the real path went untested.
		with patch.object(soa, "_upstream", side_effect=frappe.AppNotInstalledError("not installed")):
			with self.assertRaises(frappe.ValidationError) as ctx:
				soa._delegate("render_statement_html", customer="ACME")
		self.assertIn("Cecypo Frappe Reports", str(ctx.exception))

	def test_a_moved_module_still_throws_readably(self):
		with patch.object(soa, "_upstream", side_effect=ImportError("no module")):
			with self.assertRaises(frappe.ValidationError):
				soa._delegate("render_statement_html", customer="ACME")

	def test_is_available_false_without_statement_permission(self):
		# Resolution is not capability. A user who cannot read the PSOA doctype must not be
		# shown a button that opens onto a permission error.
		with patch.object(frappe, "has_permission", return_value=False):
			result = soa.is_available()
		self.assertFalse(result["available"])
		self.assertIn("permission", result["reason"].lower())

	def test_missing_attribute_is_treated_as_missing_app(self):
		with patch.object(soa, "_upstream", side_effect=AttributeError("gone")):
			with self.assertRaises(frappe.ValidationError):
				soa._delegate("render_statement_html", customer="ACME")

	def test_upstream_errors_propagate_unchanged(self):
		# A "no transactions in the period" throw must reach the user as itself, not be
		# swallowed into a generic failure or a {"success": False} envelope.
		def fake(**kwargs):
			frappe.throw("No transactions for ACME in the selected period.")

		with patch.object(soa, "_upstream_capability", return_value=(True, None)):
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
			"preview_bulk_statements": {
				"company": "Dev Co",
				"template": "AR",
				"as_of_date": None,
			},
			"email_bulk_statements": {
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

		# _upstream_capability is bypassed: this test is about which upstream name each wrapper
		# forwards to, not about capability resolution (covered separately) — without this, the
		# capability probe's own resolutions of all seven names plus the version constant would
		# land in `seen` too, ahead of each real forwarding call.
		with patch.object(soa, "_upstream_capability", return_value=(True, None)):
			with patch.object(soa, "_upstream", side_effect=spy):
				soa.get_statement_templates(company="Dev Co")
				soa.get_default_recipient(party_type="customer", party="ACME")
				soa.render_statement_html(customer="ACME", company="Dev Co", template="AR")
				soa.download_statement(customer="ACME", company="Dev Co", template="AR")
				soa.preview_bulk_statements(company="Dev Co", template="AR")
				soa.email_bulk_statements(company="Dev Co", template="AR")
				soa.email_statement(customer="ACME", company="Dev Co", template="AR")

		self.assertEqual(
			seen,
			[
				"get_statement_templates",
				"get_default_recipient",
				"render_statement_html",
				"download_statement",
				"preview_bulk_statements",
				"email_bulk_statements",
				"email_statement",
			],
		)

	def test_is_available_never_raises_even_if_the_permission_check_explodes(self):
		"""The never-raise contract must be structural, not incidental.

		frappe.has_permission calls get_meta internally, which raises DoesNotExistError when the
		doctype is absent — on a site without ERPNext, say. The frontend calls this to decide
		whether to render a button, so it has to answer rather than 500.
		"""
		with patch.object(frappe, "has_permission", side_effect=frappe.DoesNotExistError("no doctype")):
			result = soa.is_available()
		self.assertFalse(result["available"])

	def test_is_available_false_when_a_required_method_is_missing(self):
		# An older upstream that lacks a newer function must read as unavailable, not as
		# available-then-broken at call time.
		real_upstream = soa._upstream

		def missing_one(method):
			if method == "preview_bulk_statements":
				raise AttributeError(method)
			return real_upstream(method)

		with patch.object(soa, "_upstream", side_effect=missing_one):
			result = soa.is_available()
		self.assertFalse(result["available"])
		self.assertIn("preview_bulk_statements", result["reason"])

	def test_is_available_false_when_upstream_api_version_is_too_old(self):
		with patch.object(soa, "_upstream_api_version", return_value=1):
			result = soa.is_available()
		self.assertFalse(result["available"])
		self.assertIn("1", result["reason"])
		self.assertIn(str(soa.REQUIRED_UPSTREAM_API), result["reason"])

	def test_absent_version_constant_reads_as_the_pre_contract_version(self):
		# An upstream predating the constant must read as 1, not raise and not pass.
		with patch.object(soa, "_upstream", side_effect=AttributeError("STATEMENT_API_VERSION")):
			self.assertEqual(soa._upstream_api_version(), 1)

	def test_is_available_reports_a_reason_when_permission_is_denied(self):
		with patch.object(frappe, "has_permission", return_value=False):
			result = soa.is_available()
		self.assertFalse(result["available"])
		self.assertIn("permission", result["reason"].lower())

	def test_is_available_true_reports_no_reason(self):
		result = soa.is_available()
		self.assertTrue(result["available"])
		self.assertIsNone(result.get("reason"))

	def test_delegate_refuses_when_upstream_is_too_old(self):
		# The endpoint must refuse, not half-work. is_available only hides a button; anything
		# calling the API directly, or a stale SPA bundle, never consults it.
		with patch.object(soa, "_upstream_api_version", return_value=1):
			with self.assertRaises(frappe.ValidationError) as ctx:
				soa._delegate("render_statement_html", customer="ACME")
		self.assertIn("Cecypo Frappe Reports", str(ctx.exception))

	def test_email_bulk_statements_is_post_only(self):
		"""@frappe.whitelist() with no methods accepts GET, and Frappe only validates CSRF for
		unsafe methods — an <img src="...?company=X&template=Y"> on any page a logged-in user
		visits would otherwise fire a customer-wide mail blast through this delegating endpoint.
		The SPA service already calls this via POST, so restricting to POST changes nothing for
		the real caller."""
		self.assertEqual(
			frappe.allowed_http_methods_for_whitelisted_func[soa.email_bulk_statements], ["POST"]
		)

	def test_missing_app_message_names_the_app_readably(self):
		# The message is user-facing; it must not leak the snake_case module id.
		with patch.object(soa, "_upstream", side_effect=frappe.AppNotInstalledError("not installed")):
			with self.assertRaises(frappe.ValidationError) as ctx:
				soa._delegate("render_statement_html", customer="ACME")
		message = str(ctx.exception)
		self.assertIn("Cecypo Frappe Reports", message)
		self.assertNotIn("cecypo_frappe_reports", message)
