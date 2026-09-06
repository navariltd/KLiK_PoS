from frappe.model.document import Document
from frappe.utils import flt


class KlikPOSBackorder(Document):
	# Not submittable -- this is a plain ledger row, updated only from
	# klik_pos.klik_pos.backorder (created at Sales Invoice submit, adjusted as Purchase
	# Receipts fulfill it) or manually by a System Manager who wants to write one off.
	def validate(self):
		self.pending_qty = flt(self.pending_qty)
		self.fulfilled_qty = flt(self.fulfilled_qty)

		if self.status == "Cancelled":
			return
		if self.pending_qty <= 1e-6:
			self.status = "Fulfilled"
		elif self.fulfilled_qty > 1e-6:
			self.status = "Partially Fulfilled"
		else:
			self.status = "Open"
