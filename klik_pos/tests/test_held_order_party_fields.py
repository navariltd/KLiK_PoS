"""A held order keeps who it is for and how it was checked out.

The cart's Hold button sent only the items: the walk-in buyer, the till's extra fields, and
checkout's delivery charge, delivery person and tax template never reached the order, and
resuming restored none of the checkout's. The client half is covered by
klik_spa/src/utils/heldOrderPayload.test.ts; this pins that the server keeps what that
payload carries - on the order where a field exists, and in its cart_meta for the rest.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.sales_order import _get_active_pos_profile, create_held_order, get_held_order_details
from klik_pos.tests.test_held_order_replace import _payload


class TestHeldOrderKeepsTheCheckout(FrappeTestCase):
	def setUp(self):
		so_meta = frappe.get_meta("Sales Order")
		for field in ("custom_walkin_customer_name", "custom_walkin_phone"):
			if not so_meta.has_field(field):
				self.skipTest(f"{field} is not on Sales Order on this site")
		# A Project must belong to the order's company, which comes from the active till.
		company = _get_active_pos_profile().company
		self.project = frappe.db.get_value("Project", {"company": company}, "name")
		self.personnel = frappe.db.get_value("Delivery Personnel", {}, "name")

	def _hold(self):
		# Two configured fields where the site has a Project, so this is about every field in
		# the till's list, not about po_no.
		# ERPNext refuses a second Sales Order on the same customer PO, so each hold gets its own.
		extra = {"po_no": f"PO-{frappe.generate_hash(length=6)}"}
		allowed = {"po_no"}
		if self.project:
			extra["project"] = self.project
			allowed.add("project")
		payload = _payload(qty=1)
		payload.update(
			{
				"walkin_name": "Jane Wanjiku",
				"walkin_phone": "0712345678",
				"tax_id": "P051234567X",
				"extra_fields": extra,
				"deliveryPersonnel": self.personnel,
			}
		)
		with patch("klik_pos.api.pos_profile.get_configured_extra_fieldnames", return_value=allowed):
			result = create_held_order(payload)
		self.assertTrue(result["success"], msg=result.get("message"))
		return result["order_name"], extra

	def test_buyer_and_every_extra_field_land_on_the_order(self):
		name, extra = self._hold()
		so = frappe.get_doc("Sales Order", name)
		self.assertEqual(so.custom_walkin_customer_name, "Jane Wanjiku")
		self.assertEqual(so.custom_walkin_phone, "0712345678")
		self.assertEqual(so.tax_id, "P051234567X")
		for field, value in extra.items():
			self.assertEqual(so.get(field), value, field)

	def test_resume_details_carry_the_checkout_state(self):
		name, extra = self._hold()
		details = get_held_order_details(name)
		self.assertTrue(details["success"], msg=details.get("message"))
		self.assertEqual(details["walkin_name"], "Jane Wanjiku")
		self.assertEqual(details["extra_fields"], extra)
		self.assertEqual(details["cart_meta"]["deliveryPersonnel"], self.personnel)
		self.assertIn("deliveryCharge", details["cart_meta"])
		self.assertIn("SalesTaxCharges", details["cart_meta"])


class TestHeldOrderKeepsLineDescription(FrappeTestCase):
	def test_line_description_lands_on_the_order(self):
		payload = _payload(qty=1)
		payload["items"][0]["description"] = "Cut to 2.4m, blue"
		result = create_held_order(payload)
		self.assertTrue(result["success"], msg=result.get("message"))
		self.assertEqual(frappe.get_doc("Sales Order", result["order_name"]).items[0].description, "Cut to 2.4m, blue")
