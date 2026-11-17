from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

QUOTE_FIELDS = {
	"Quotation": [
		{
			"fieldname": "custom_klik_cart_meta",
			"label": "KLiK Cart Meta",
			"fieldtype": "Long Text",
			"hidden": 1,
			"read_only": 1,
			"no_copy": 1,
			"module": "KLiK PoS",
		},
		{
			"fieldname": "custom_pos_profile",
			"label": "POS Profile",
			"fieldtype": "Link",
			"options": "POS Profile",
			"read_only": 1,
			"insert_after": "company",
			"module": "KLiK PoS",
		},
	],
	"Sales Invoice": [
		{
			"fieldname": "custom_klik_quotation",
			"label": "Quotation",
			"fieldtype": "Link",
			"options": "Quotation",
			"read_only": 1,
			# The Quotes tab looks each quote's invoice up by it.
			"search_index": 1,
			"no_copy": 1,
			"allow_on_submit": 0,
			"insert_after": "customer",
			"module": "KLiK PoS",
		},
	],
}


def ensure_quote_fields():
	create_custom_fields(QUOTE_FIELDS, update=True)
