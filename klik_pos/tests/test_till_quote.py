"""Quote mode: the till saves a Quotation, which is never limited by stock."""

import json
from unittest.mock import patch

import frappe
from frappe.utils import add_days, add_months, cint, nowdate
from frappe.tests.utils import FrappeTestCase

from klik_pos.api import quotation
from klik_pos.overrides import loss_of_sale
from klik_pos.setup.quote_fields import ensure_quote_fields
from klik_pos.tests.test_held_order_visibility import CUSTOMER, ITEM
from klik_pos.tests.pos_fixtures import open_own_shift
from klik_pos.tests.test_opening_conflict import _profile, _shift


def _payload(item, qty, rate, los_qty=0):
	return {
		"customer": {"id": CUSTOMER},
		"items": [{"id": item, "item_code": item, "quantity": qty, "los_qty": los_qty, "price": rate, "uom": "Nos"}],
		"status": "held",
	}


class TestCreateQuote(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_quote_fields()
		self.addCleanup(frappe.db.rollback)
		_shift(_profile(), "Administrator")

	def test_quote_takes_more_than_stock_and_prices_all_of_it(self):
		res = quotation.create_quote(_payload(ITEM, qty=15, rate=100))
		self.assertTrue(res["success"], res)
		q = frappe.get_doc("Quotation", res["quote_name"])
		self.assertEqual(q.docstatus, 1)
		self.assertEqual(q.status, "Open")
		self.assertEqual(q.items[0].qty, 15)
		# Dev Co taxes are inclusive of the rate, so the quote totals what the cart priced.
		self.assertEqual(q.grand_total, 1500)
		self.assertTrue(q.custom_klik_cart_meta)
		self.assertTrue(q.custom_pos_profile)
		days = cint(frappe.db.get_single_value("CRM Settings", "default_valid_till"))
		expected = add_days(nowdate(), days) if days else add_months(nowdate(), 1)
		self.assertEqual(str(q.valid_till), str(expected))

	def test_line_description_lands_on_the_quote(self):
		payload = _payload(ITEM, qty=1, rate=100)
		payload["items"][0]["description"] = "Cut to 2.4m, blue"
		res = quotation.create_quote(payload)
		self.assertTrue(res["success"], res)
		self.assertEqual(frappe.get_doc("Quotation", res["quote_name"]).items[0].description, "Cut to 2.4m, blue")

	def test_los_qty_in_payload_is_folded_back(self):
		res = quotation.create_quote(_payload(ITEM, qty=10, los_qty=5, rate=100))
		self.assertTrue(res["success"], res)
		self.assertEqual(frappe.db.get_value("Quotation Item", {"parent": res["quote_name"]}, "qty"), 15)
		self.assertEqual(frappe.db.get_value("Quotation", res["quote_name"], "grand_total"), 1500)

	def test_till_with_quotes_switched_off_is_refused(self):
		before = frappe.db.count("Quotation")
		till = frappe.get_doc("POS Profile", _profile())
		till.custom_allow_quotes = 0
		with patch.object(quotation, "_get_active_pos_profile", return_value=till):
			res = quotation.create_quote(_payload(ITEM, qty=1, rate=100))
		self.assertFalse(res["success"])
		self.assertIn("not allowed", res["message"])
		self.assertEqual(frappe.db.count("Quotation"), before)

	def test_breach_is_refused_and_nothing_is_saved(self):
		before = frappe.db.count("Quotation")
		with patch(
			"cecypo_powerpack.min_selling_price.validate_min_selling_price",
			side_effect=frappe.ValidationError("Price approval needed"),
		):
			res = quotation.create_quote(_payload(ITEM, qty=1, rate=1))
		self.assertFalse(res["success"])
		self.assertIn("Price approval needed", res["message"])
		self.assertEqual(frappe.db.count("Quotation"), before)


class TestListAndLoadQuotes(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_quote_fields()
		self.addCleanup(frappe.db.rollback)
		_shift(_profile(), "Administrator")

	def _quote(self, qty=2, rate=100):
		res = quotation.create_quote(_payload(ITEM, qty=qty, rate=rate))
		self.assertTrue(res["success"], res)
		return res["quote_name"]

	def test_listed_for_any_till_of_the_company(self):
		name = self._quote()
		till_b = frappe.get_doc("POS Profile", _profile())
		with patch.object(quotation, "_get_active_pos_profile", return_value=till_b):
			res = quotation.get_quotes(limit=500)
		self.assertTrue(res["success"], res)
		row = next((r for r in res["data"] if r["name"] == name), None)
		self.assertIsNotNone(row)
		self.assertFalse(row["expired"])
		self.assertEqual(row["grand_total"], 200)

	def test_invoiced_quote_is_not_listed_and_wont_load(self):
		name = self._quote()
		si = frappe.new_doc("Sales Invoice")
		si.customer = CUSTOMER
		si.company = frappe.db.get_value("Quotation", name, "company")
		si.append("items", {"item_code": ITEM, "qty": 1, "rate": 1})
		si.custom_klik_quotation = name
		si.insert(ignore_permissions=True, ignore_mandatory=True)
		self.assertNotIn(name, [r["name"] for r in quotation.get_quotes(limit=500)["data"]])
		res = quotation.get_quote_details(name)
		self.assertFalse(res["success"])
		self.assertIn("already been invoiced", res["message"])

	def test_loading_splits_against_the_till_stock(self):
		"""Loaded for sale, a quote meets the stock rules again: 15 asked, 10 in stock at a Loss
		of Sale till opens as 10 + 5 LoS, as a reopened held order does."""
		name = self._quote(qty=15, rate=100)
		till = frappe._dict({"name": "_Test Till", "warehouse": "Stores - DC", "custom_enable_loss_of_sale": 1})
		with (
			patch.object(quotation, "_active_till", return_value=till),
			patch.object(loss_of_sale, "_eligible_item_codes", side_effect=lambda codes: set(codes)),
			patch.object(loss_of_sale, "_available", return_value={(ITEM, "Stores - DC"): 10}),
		):
			res = quotation.get_quote_details(name)
		self.assertTrue(res["success"], res)
		self.assertEqual((res["items"][0]["quantity"], res["items"][0]["los_qty"]), (10, 5))

	def test_details_keep_quoted_price_until_valid_till(self):
		name = self._quote(qty=3, rate=100)
		res = quotation.get_quote_details(name)
		self.assertTrue(res["success"], res)
		self.assertFalse(res["expired"])
		item = res["items"][0]
		self.assertEqual(item["price"], 100)
		self.assertEqual(item["quantity"], 3)
		self.assertEqual(item["los_qty"], 0)
		self.assertEqual(res["customer"], CUSTOMER)

	def test_expired_quote_loads_without_its_prices(self):
		name = self._quote(rate=100)
		q = frappe.get_doc("Quotation", name)
		row = q.items[0]
		# Only this test's price on the list: another run may have left one (rolled back after).
		frappe.db.delete("Item Price", {"item_code": ITEM, "price_list": q.selling_price_list})
		frappe.get_doc({
			"doctype": "Item Price", "item_code": ITEM, "uom": row.uom,
			"price_list": q.selling_price_list, "price_list_rate": 137,
		}).insert(ignore_permissions=True)
		q.db_set("valid_till", add_days(nowdate(), -1))
		res = quotation.get_quote_details(name)
		self.assertTrue(res["success"], res)
		self.assertTrue(res["expired"])
		item = res["items"][0]
		self.assertEqual(item["price"], 137)
		self.assertIsNone(item["customRate"])
		self.assertEqual(item["discountAmount"], 0)
		self.assertEqual(item["discountPercentage"], 0)

	def test_expired_quote_ignores_a_future_dated_price(self):
		name = self._quote(rate=100)
		q = frappe.get_doc("Quotation", name)
		uom = q.items[0].uom
		frappe.db.delete("Item Price", {"item_code": ITEM, "price_list": q.selling_price_list})
		for rate, valid_from in ((137, None), (999, add_days(nowdate(), 1))):
			frappe.get_doc({
				"doctype": "Item Price", "item_code": ITEM, "uom": uom, "price_list": q.selling_price_list,
				"price_list_rate": rate, "valid_from": valid_from,
			}).insert(ignore_permissions=True)
		q.db_set("valid_till", add_days(nowdate(), -1))
		self.assertEqual(quotation.get_quote_details(name)["items"][0]["price"], 137)

	def test_expired_quote_scales_the_stock_uom_price_by_conversion(self):
		name = self._quote(rate=100)
		q = frappe.get_doc("Quotation", name)
		stock_uom = frappe.db.get_value("Item", ITEM, "stock_uom")
		box = frappe.get_doc({"doctype": "UOM", "uom_name": "_Test Quote Box"}).insert(ignore_permissions=True)
		item = frappe.get_doc("Item", ITEM)
		item.append("uoms", {"uom": box.name, "conversion_factor": 12})
		item.flags.ignore_validate = True
		item.save(ignore_permissions=True)
		frappe.db.set_value("Quotation Item", q.items[0].name, "uom", box.name)
		frappe.db.delete("Item Price", {"item_code": ITEM, "price_list": q.selling_price_list})
		frappe.get_doc({
			"doctype": "Item Price", "item_code": ITEM, "uom": stock_uom,
			"price_list": q.selling_price_list, "price_list_rate": 10,
		}).insert(ignore_permissions=True)
		q.db_set("valid_till", add_days(nowdate(), -1))
		self.assertEqual(quotation.get_quote_details(name)["items"][0]["price"], 120)


class TestQuoteStates(FrappeTestCase):
	"""The Quotes tab lists every quote with its state; a converted or closed one copies into a
	new sale at today's prices, never linked to it."""

	def setUp(self):
		frappe.set_user("Administrator")
		ensure_quote_fields()
		self.addCleanup(frappe.db.rollback)
		_shift(_profile(), "Administrator")

	def _quote(self, qty=2, rate=100):
		res = quotation.create_quote(_payload(ITEM, qty=qty, rate=rate))
		self.assertTrue(res["success"], res)
		return res["quote_name"]

	def _invoice(self, quote):
		si = frappe.new_doc("Sales Invoice")
		si.customer = CUSTOMER
		si.company = frappe.db.get_value("Quotation", quote, "company")
		si.append("items", {"item_code": ITEM, "qty": 1, "rate": 1})
		si.custom_klik_quotation = quote
		si.insert(ignore_permissions=True, ignore_mandatory=True)
		return si.name

	def _row(self, name, state):
		res = quotation.get_quotes(limit=500, state=state)
		self.assertTrue(res["success"], res)
		return next((r for r in res["data"] if r["name"] == name), None)

	def test_each_state_lists_only_its_quotes(self):
		open_q, invoiced, lost = self._quote(), self._quote(), self._quote()
		invoice = self._invoice(invoiced)
		frappe.db.set_value("Quotation", lost, "status", "Lost")

		self.assertEqual(self._row(open_q, "open")["state"], "open")
		self.assertIsNone(self._row(invoiced, "open"))
		row = self._row(invoiced, "converted")
		self.assertEqual((row["state"], row["invoice"]), ("converted", invoice))
		self.assertEqual(self._row(lost, "closed")["state"], "closed")
		self.assertIsNone(self._row(lost, "converted"))
		self.assertEqual({self._row(n, "all")["state"] for n in (open_q, invoiced, lost)}, {"open", "converted", "closed"})

	def test_an_ordered_quote_is_converted(self):
		name = self._quote()
		frappe.db.set_value("Quotation", name, "status", "Ordered")
		self.assertEqual(self._row(name, "converted")["state"], "converted")

	def test_the_default_still_lists_open_quotes_only(self):
		invoiced = self._quote()
		self._invoice(invoiced)
		self.assertNotIn(invoiced, [r["name"] for r in quotation.get_quotes(limit=500)["data"]])

	def test_a_converted_quote_copies_at_todays_price_and_still_wont_load_linked(self):
		name = self._quote(qty=3, rate=100)
		self._invoice(name)
		self.assertFalse(quotation.get_quote_details(name)["success"])

		with patch.object(quotation, "_current_list_rate", return_value=120):
			res = quotation.get_quote_details(name, as_copy=1)
		self.assertTrue(res["success"], res)
		self.assertTrue(res["copy"])
		self.assertEqual((res["items"][0]["quantity"], res["items"][0]["price"]), (3, 120))
		self.assertEqual(res["discount_amount"], 0)

	def test_a_copy_takes_lines_and_customer_not_the_old_sales_charges(self):
		"""A delivery charge, delivery person, shipping rule, remarks or PO of the old sale must
		not land quietly on the new one."""
		name = self._quote()
		meta = json.loads(frappe.db.get_value("Quotation", name, "custom_klik_cart_meta") or "{}")
		meta.update({"deliveryCharge": 500, "deliveryPersonnel": "Rider 1", "shipping_rule": "Courier",
			"remarks": "Deliver Tuesday", "extra_fields": {"po_no": "PO-9"}, "walkin_name": "Jane"})
		frappe.db.set_value("Quotation", name, {"custom_klik_cart_meta": json.dumps(meta), "status": "Lost"})

		res = quotation.get_quote_details(name, as_copy=1)

		self.assertTrue(res["success"], res)
		for key in ("deliveryCharge", "deliveryPersonnel", "shipping_rule", "remarks", "extra_fields"):
			self.assertNotIn(key, res["cart_meta"])
		self.assertEqual((res["remarks"], res["shipping_rule"], res["extra_fields"]), ("", None, {}))
		self.assertEqual(res["walkin_name"], "Jane")

	def test_the_quote_link_on_invoices_is_indexed(self):
		"""Every quote row looks its invoice up by this field."""
		ensure_quote_fields()
		self.assertTrue(frappe.db.sql("SHOW INDEX FROM `tabSales Invoice` WHERE Column_name = 'custom_klik_quotation'"))

	def test_a_lost_quote_copies(self):
		name = self._quote()
		frappe.db.set_value("Quotation", name, "status", "Lost")
		self.assertTrue(quotation.get_quote_details(name, as_copy=1)["success"])


class TestQuoteLink(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_quote_fields()
		self.addCleanup(frappe.db.rollback)
		_shift(_profile(), "Administrator")

	def _quote(self):
		res = quotation.create_quote(_payload(ITEM, qty=2, rate=100))
		self.assertTrue(res["success"], res)
		return res["quote_name"]

	def _invoice(self, quote, submit=False):
		si = frappe.new_doc("Sales Invoice")
		si.customer = CUSTOMER
		si.company = frappe.db.get_value("Quotation", quote, "company")
		si.append("items", {"item_code": ITEM, "qty": 1, "rate": 1})
		si.custom_klik_quotation = quote
		si.insert(ignore_permissions=True, ignore_mandatory=True)
		if submit:
			si.flags.ignore_permissions = True
			si.submit()
		return si

	def test_second_invoice_for_same_quote_is_refused(self):
		q = self._quote()
		self._invoice(q)
		with self.assertRaises(frappe.ValidationError):
			self._invoice(q)

	def test_two_drafts_cannot_share_a_quote_but_first_still_saves_and_submits(self):
		q = self._quote()
		first = self._invoice(q)
		with self.assertRaises(frappe.ValidationError):
			self._invoice(q)
		first.save()
		first.submit()
		self.assertEqual(frappe.db.get_value("Quotation", q, "status"), "Ordered")

	def test_cancel_does_not_resurrect_a_lost_quote(self):
		q = self._quote()
		si = self._invoice(q, submit=True)
		frappe.db.set_value("Quotation", q, "status", "Lost")
		si.cancel()
		self.assertEqual(frappe.db.get_value("Quotation", q, "status"), "Lost")

	def test_ordered_quote_without_invoice_is_not_listed_or_loaded(self):
		q = self._quote()
		frappe.db.set_value("Quotation", q, "status", "Ordered")
		self.assertNotIn(q, [r["name"] for r in quotation.get_quotes(limit=500)["data"]])
		self.assertFalse(quotation.get_quote_details(q)["success"])

	def test_submit_marks_ordered_cancel_reopens(self):
		q = self._quote()
		si = self._invoice(q, submit=True)
		self.assertEqual(frappe.db.get_value("Quotation", q, "status"), "Ordered")
		si.cancel()
		self.assertEqual(frappe.db.get_value("Quotation", q, "status"), "Open")
		self._invoice(q)  # a cancelled invoice frees the quote

	def test_used_quote_stays_used_after_expiry_job(self):
		from erpnext.selling.doctype.quotation.quotation import set_expired_status

		q = self._quote()
		si = self._invoice(q, submit=True)
		frappe.db.set_value("Quotation", q, "valid_till", add_days(nowdate(), -1))
		set_expired_status()
		self.assertEqual(quotation._quote_in_use(q), si.name)
		self.assertNotIn(q, [r["name"] for r in quotation.get_quotes(limit=500)["data"]])


class TestQuoteIdOnCheckout(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.ready = bool(open_own_shift(cls, "TEST-QUOTE-LINK-TILL"))

	def setUp(self):
		if not self.ready:
			self.skipTest("no till to copy on this site")
		frappe.set_user("Administrator")
		ensure_quote_fields()
		self.addCleanup(frappe.db.rollback)

	def _quote(self):
		res = quotation.create_quote(_payload(ITEM, qty=2, rate=100))
		self.assertTrue(res["success"], res)
		return res["quote_name"]

	def test_checkout_payload_quotation_id_lands_on_invoice(self):
		from klik_pos.api.sales_invoice import create_draft_invoice, submit_draft_invoice

		q = self._quote()
		data = {**_payload(ITEM, qty=1, rate=100), "quotation_id": q, "amountPaid": 100, "paymentMethods": [{"method": "Cash", "amount": 100}], "businessType": "B2C"}
		data.pop("status")
		draft = create_draft_invoice(data)
		self.assertTrue(draft["success"], draft)
		name = draft["invoice_name"]
		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "custom_klik_quotation"), q)
		# Re-saved from the cart, then finished from it: the link survives each rebuild.
		again = create_draft_invoice({**data, "draft_invoice_id": name})
		self.assertTrue(again["success"], again)
		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "custom_klik_quotation"), q)
		done = submit_draft_invoice(name, data=data)
		self.assertTrue(done["success"], done)
		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "custom_klik_quotation"), q)
		self.assertEqual(frappe.db.get_value("Quotation", q, "status"), "Ordered")


class TestQuoteWhatsapp(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_quote_fields()
		self.addCleanup(frappe.db.rollback)
		_shift(_profile(), "Administrator")

	def _send(self, **kw):
		from klik_pos.api import whatsapp

		with patch("klik_pos.api.whatsapp.send_whatsapp_message", return_value={"success": True}) as m:
			whatsapp.deliver_invoice_via_whatsapp(mobile_no="254700000000", **kw)
		return m

	def test_quotation_is_attached_as_quotation(self):
		q = quotation.create_quote(_payload(ITEM, qty=2, rate=100))["quote_name"]
		m = self._send(invoice_data=q, doctype="Quotation", customer_name="Bob")
		kw = m.call_args.kwargs
		self.assertEqual(kw["reference_doctype"], "Quotation")
		self.assertEqual(kw["message_content"], f"Hello Bob, here is your quotation {q}.")

	def test_invoice_is_still_default(self):
		m = self._send(invoice_data="SINV-X")
		self.assertEqual(m.call_args.kwargs["reference_doctype"], "Sales Invoice")

	def test_other_doctypes_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._send(invoice_data="Administrator", doctype="User")
