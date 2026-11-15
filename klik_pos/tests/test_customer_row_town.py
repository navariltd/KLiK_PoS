"""The customer picker's row shows a town: the primary address's City/Town, else the town of
any other address linked to the customer (an address that was never set as primary)."""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from klik_pos.api.customer import get_customers
from klik_pos.testing import any_till_currency

PRIMARY = "_Test KLiK Town Primary"
LINKED = "_Test KLiK Town Linked Only"
NONE = "_Test KLiK Town None"


def _customer(name, city=None, primary=False):
	if not frappe.db.exists("Customer", name):
		frappe.get_doc({"doctype": "Customer", "customer_name": name, "customer_type": "Individual"}).insert()
	if city:
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": name,
				"address_type": "Billing",
				"address_line1": "Street 1",
				"city": city,
				"links": [{"link_doctype": "Customer", "link_name": name}],
			}
		).insert()
		frappe.db.set_value("Customer", name, "customer_primary_address", address.name if primary else None)


class TestCustomerRowTown(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		any_till_currency(cls)  # get_customers is patched to an INR company below

	def setUp(self):
		frappe.set_user("Administrator")
		_customer(PRIMARY, "NAKURU", primary=True)
		_customer(LINKED, "NAIROBI")
		_customer(NONE)

	def towns(self):
		with (
			patch("klik_pos.api.customer.get_current_pos_profile", return_value=SimpleNamespace(custom_business_type="B2C")),
			patch("klik_pos.api.customer.get_user_company_and_currency", return_value=("_Test Company", "INR")),
		):
			res = get_customers(search="_Test KLiK Town")
		self.assertTrue(res["success"], res)
		return {c["customer_name"]: c["city"] for c in res["data"]}

	def test_town_falls_back_to_a_linked_address(self):
		towns = self.towns()
		self.assertEqual(towns[PRIMARY], "NAKURU")
		self.assertEqual(towns[LINKED], "NAIROBI")
		self.assertIsNone(towns[NONE])
