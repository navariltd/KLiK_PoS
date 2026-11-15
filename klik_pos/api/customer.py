import json
import re

import frappe
from erpnext.setup.utils import get_exchange_rate
from erpnext.accounts.party import get_party_details
from frappe import _
from frappe.utils import flt, getdate, today
from frappe.utils.nestedset import get_descendants_of

from klik_pos.klik_pos.utils import get_current_pos_profile, get_user_default_company, till_currency
from klik_pos.api.loyalty import get_customer_loyalty_summary
from klik_pos.api.customer_summary import last_visit_sql, total_orders_sql, total_spent_sql
from .sql_builder import apply_sql_permissions


@frappe.whitelist(allow_guest=True)
def get_customers(limit: int = 100, start: int = 0, search: str = ""):
    try:
        pos_profile = get_current_pos_profile()
        business_type = getattr(pos_profile, "custom_business_type", "B2C")
        company, company_currency = get_user_company_and_currency()
        
        customer_group_names = []
        if hasattr(pos_profile, "customer_groups") and pos_profile.customer_groups:
            customer_group_names = [
                d.customer_group
                for d in pos_profile.customer_groups
                if d.customer_group
            ]

        user_permitted = frappe.permissions.get_user_permissions(frappe.session.user)
        permitted_customer_names = []
        has_customer_permissions = False
        permitted_customer_group_names = []
        has_customer_group_permissions = False

        if user_permitted and "Customer" in user_permitted:
            permitted_customer_names = [
                perm.get("doc") for perm in user_permitted["Customer"]
            ]
            has_customer_permissions = True

        if user_permitted and "Customer Group" in user_permitted:
            permitted_customer_group_names = [
                perm.get("doc")
                for perm in user_permitted["Customer Group"]
                if perm.get("doc")
            ]
            has_customer_group_permissions = True

        if has_customer_permissions and not permitted_customer_names:
            return {"success": True, "data": [], "total_count": 0}

        if has_customer_group_permissions and not permitted_customer_group_names:
            return {"success": True, "data": [], "total_count": 0}

        try:
            limit_val = int(limit) if limit else 100
        except Exception:
            limit_val = 100

        limit_val = min(limit_val, 500)
        offset_val = int(start) or 0

        where_conditions = ["c.disabled = 0"]
        params = []

        # Only customers billed in the till's currency; no billing currency means the company's.
        currency = till_currency()
        if currency:
            where_conditions.append("COALESCE(NULLIF(c.default_currency, ''), %s) = %s")
            params.extend([company_currency, currency])

        if business_type == "B2B":
            where_conditions.append("c.customer_type = 'Company'")
        elif business_type == "B2C":
            where_conditions.append("c.customer_type = 'Individual'")
        
        if customer_group_names:
            placeholders = ",".join(["%s"] * len(customer_group_names))
            where_conditions.append(f"c.customer_group IN ({placeholders})")
            params.extend(customer_group_names)
        
        if permitted_customer_names:
            placeholders = ",".join(["%s"] * len(permitted_customer_names))
            where_conditions.append(f"c.name IN ({placeholders})")
            params.extend(permitted_customer_names)

        if permitted_customer_group_names:
            placeholders = ",".join(["%s"] * len(permitted_customer_group_names))
            where_conditions.append(f"c.customer_group IN ({placeholders})")
            params.extend(permitted_customer_group_names)
        
        if search:
            search_term = f"%{search}%"
            where_conditions.append("(c.customer_name LIKE %s OR c.name LIKE %s OR c.email_id LIKE %s OR c.mobile_no LIKE %s)")
            params.extend([search_term, search_term, search_term, search_term])
        
        where_clause = " AND ".join(where_conditions)
        
        count_query = f"""
            SELECT COUNT(c.name) as total
            FROM `tabCustomer` c
            WHERE {where_clause}
        """
        count_query = apply_sql_permissions(count_query)
        total_count_result = frappe.db.sql(count_query, tuple(params), as_dict=True)
        total_count = total_count_result[0]["total"] if total_count_result else 0
        
        data_query = f"""
            SELECT
                c.name,
                c.customer_name,
                c.customer_type,
                c.customer_group,
                c.territory,
                c.default_currency,
                c.email_id,
                c.mobile_no,
                c.tax_id,
                -- The primary address's town, else any linked address's: an address added
                -- to a customer is often never set as its primary.
                COALESCE(NULLIF(addr.city, ''), (
                    SELECT ad.city FROM `tabDynamic Link` dl
                    JOIN `tabAddress` ad ON ad.name = dl.parent
                    WHERE dl.parenttype = 'Address' AND dl.link_doctype = 'Customer'
                        AND dl.link_name = c.name AND ad.disabled = 0 AND IFNULL(ad.city, '') != ''
                    ORDER BY ad.is_primary_address DESC, ad.modified DESC
                    LIMIT 1
                )) as city,
                c.custom_is_walkin as is_walkin,
                COALESCE(({total_orders_sql("c.name")}), 0) as custom_total_orders,
                COALESCE(({total_spent_sql("c.name")}), 0) as custom_total_spent,
                ({last_visit_sql("c.name")}) as custom_last_visit
            FROM `tabCustomer` c
            LEFT JOIN `tabAddress` addr ON addr.name = c.customer_primary_address
            WHERE {where_clause}
            ORDER BY c.creation DESC
            LIMIT %s OFFSET %s
        """
        
        data_query = apply_sql_permissions(data_query)
        params.extend([limit_val, offset_val])
        
        customers = frappe.db.sql(data_query, tuple(params), as_dict=True)
        
        for cust in customers:
            cust["company_currency"] = company_currency
            cust["contact"] = None
            cust["address"] = None
            cust["default_currency"] = cust.get("default_currency")
            cust["loyalty"] = get_customer_loyalty_summary(
                cust.get("name"),
                company=company,
            )
            cust["loyalty_points"] = cust["loyalty"].get("loyalty_points", 0)
        
        return {
            "success": True,
            "data": customers,
            "total_count": total_count,
            "start": offset_val,
            "limit": limit_val,
        }

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Error fetching customers",
        )
        return {
            "success": False,
            "error": _("Something went wrong while fetching customers."),
        }
    
    
@frappe.whitelist()
def get_walkin_details_by_phone(phone: str):
    """The name and tax ID a walk-in gave on their last sales under this phone number - each
    from the newest sale that has it - so a returning walk-in only needs to give the number.
    The number matches however it was typed: 07..., 2547..., +254 7...
    """
    frappe.has_permission("Sales Invoice", "read", throw=True)
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 9 or not frappe.db.has_column("Sales Invoice", "custom_walkin_phone"):
        return {}
    # Stored as the cashier typed it - spaces, dashes, 07... or +254...: compared on its digits.
    # ponytail: scans the submitted invoices that have a walk-in phone (a few ms for thousands);
    # store a normalised number with an index if this ever shows.
    rows = frappe.db.sql(
        """SELECT custom_walkin_customer_name AS name, tax_id
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND IFNULL(custom_walkin_phone, '') != ''
            AND REGEXP_REPLACE(custom_walkin_phone, '[^0-9]', '') LIKE %(tail)s
        ORDER BY creation DESC
        LIMIT 50""",
        {"tail": f"%{digits[-9:]}"},
        as_dict=True,
    )
    found = {}
    for field in ("name", "tax_id"):
        value = next((r[field] for r in rows if (r[field] or "").strip()), None)
        if value:
            found[field] = value.strip()
    return found


def get_user_company_and_currency():
    default_company = get_user_default_company()
    if not default_company:
        default_company = frappe.db.get_single_value(
            "Global Defaults", "default_company"
        )

    company_currency = frappe.db.get_value(
        "Company", default_company, "default_currency"
    )

    return default_company, company_currency


@frappe.whitelist(allow_guest=True)
def get_customer_addresses(customer: str):
    """Get all addresses for a specific customer"""
    try:
        # Get all addresses linked to this customer
        addresses = frappe.get_all(
            "Address",
            filters={
                "name": [
                    "in",
                    frappe.get_all(
                        "Dynamic Link",
                        filters={
                            "link_doctype": "Customer",
                            "link_name": customer,
                            "parenttype": "Address",
                        },
                        pluck="parent",
                    ),
                ]
            },
            fields=[
                "name",
                "address_line1",
                "address_line2",
                "city",
                "state",
                "country",
                "pincode",
            ],
            order_by="creation desc",
        )
        return addresses
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), f"Error fetching addresses for customer {customer}"
        )
        return []


@frappe.whitelist()
def get_currency_exchange_rate(
    from_currency: str, to_currency: str, transaction_date: str | None = None
):
    """
    Get exchange rate from `from_currency` to `to_currency`.
    Optionally pass a transaction_date (YYYY-MM-DD).
    """
    if not transaction_date:
        transaction_date = frappe.utils.nowdate()
    try:
        if not from_currency or not to_currency:
            return {
                "success": False,
                "error": "Both from_currency and to_currency are required",
            }

        rate = get_exchange_rate(from_currency, to_currency, transaction_date)
        return rate

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error fetching exchange rate")
        return {"success": False, "error": str(e)}


@frappe.whitelist(allow_guest=True)
def get_customer_info(customer_name: str):
    """Fetch comprehensive customer document by customer name or ID."""
    try:
        # URL decode the customer name to handle special characters like +
        import urllib.parse

        customer_name = urllib.parse.unquote(customer_name)
        pos_profile = get_current_pos_profile()
        # The ID first: a duplicate customer keeps the same customer_name under the ID
        # "<name> - 1", so a name-first lookup hands back the duplicate.
        customer_id = frappe.db.exists("Customer", customer_name) or frappe.db.get_value(
            "Customer", {"customer_name": customer_name}, "name"
        )

        if not customer_id:
            # Log the search attempt for debugging
            frappe.logger().info(
                f"Customer not found: '{customer_name}'. Searched by name and customer_name."
            )
            return {"success": False, "error": f"Customer not found: {customer_name}"}

        party_details = get_party_details(party=customer_id, party_type="Customer", pos_profile=pos_profile.name)
        from klik_pos.api.item.item_price import get_price_list_with_customer_priority

        party_details["selling_price_list"] = get_price_list_with_customer_priority(customer_id, pos_profile)
        customer = frappe.get_doc("Customer", customer_id)

        # Get primary contact details
        contact_data = None
        if customer.customer_primary_contact:
            contact_data = frappe.db.get_value(
                "Contact",
                customer.customer_primary_contact,
                ["first_name", "last_name", "email_id", "phone", "mobile_no"],
                as_dict=True,
            )

        # Get primary address details
        address_data = None
        if customer.customer_primary_address:
            address_data = frappe.db.get_value(
                "Address",
                customer.customer_primary_address,
                [
                    "address_line1",
                    "address_line2",
                    "city",
                    "state",
                    "country",
                    "pincode",
                ],
                as_dict=True,
            )

        # Prepare base customer data
        customer_data = {
            "name": customer.name,
            "customer_name": customer.customer_name,
            "customer_group": customer.customer_group,
            "territory": customer.territory,
            "customer_type": customer.customer_type,
            "customer_primary_contact": customer.customer_primary_contact,
            "customer_primary_address": customer.customer_primary_address,
            "email_id": customer.email_id,
            "mobile_no": customer.mobile_no,
            "creation": customer.creation,
            "contact_data": contact_data,
            "address_data": address_data,
            "is_walkin": getattr(customer, "custom_is_walkin", 0),
            "tax_id": customer.tax_id,
            "delivery_personnel": customer.get("custom_delivery_personnel"),
            "loyalty": get_customer_loyalty_summary(
                customer.name,
                company=getattr(pos_profile, "company", None),
            ),
            **party_details,
        }

        # Add ZATCA details for company customers
        if customer.customer_type == "Company":
            customer_data.update(
                {
                    "vat_number": getattr(customer, "custom_vat_number", ""),
                    "registration_scheme": getattr(
                        customer, "custom_registration_scheme", ""
                    ),
                    "registration_number": getattr(
                        customer, "custom_registration_number", ""
                    ),
                    "payment_method": getattr(
                        customer, "custom_payment_method", "Cash"
                    ),
                    "industry": getattr(customer, "custom_industry", ""),
                    "employee_count": getattr(customer, "custom_employee_count", ""),
                    "company_name": customer.customer_name,
                }
            )

        return customer_data
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error fetching customer info")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def create_or_update_customer(customer_data):
    try:
        if isinstance(customer_data, str):
            customer_data = frappe.parse_json(customer_data)
        # Extract main fields
        customer_name = customer_data.get("name")
        email = customer_data.get("email")
        phone = customer_data.get("phone")
        cust_type = customer_data.get(
            "customer_type", customer_data.get("type", "individual")
        ).lower()
        country = customer_data.get("address", {}).get("country", "Kenya")
        name_arabic = customer_data.get("name_arabic", "")
        address = customer_data.get("address", {})
        tax_id = customer_data.get("taxId", "")

        if not customer_name:
            customer_name = phone or email
        if not customer_name:
            frappe.throw("Customer must have at least a name, phone, or email")

        # Create or update Customer
        customer_doc = get_or_create_customer(
            customer_name, email, phone, country, tax_id, name_arabic, customer_data
        )

        contact_doc = None
        addr_doc = None
        # For Individuals → create contact if phone exists, and address if provided
        if cust_type == "individual":
            if phone:
                contact_doc = create_or_update_contact(
                    customer_doc.name, customer_name, email, phone
                )

            # Create address for individual customers if address data is provided
            if address and any(
                address.get(field) for field in ["street", "city", "state", "zipCode"]
            ):
                addr_doc = create_or_update_address(
                    customer_doc.name, customer_name, address, country
                )
                if addr_doc:
                    frappe.db.set_value(
                        "Customer",
                        customer_doc.name,
                        "customer_primary_address",
                        addr_doc.name,
                    )

        # For Companies → create both Contact and Address
        if cust_type == "company":
            contact_name = customer_data.get("contactName", customer_name)
            contact_doc = create_or_update_contact(
                customer_doc.name, contact_name, email, phone
            )
            addr_doc = create_or_update_address(
                customer_doc.name, customer_name, address, country
            )

            if addr_doc:
                frappe.db.set_value(
                    "Customer",
                    customer_doc.name,
                    "customer_primary_address",
                    addr_doc.name,
                )

        return {
            "success": True,
            "customer_name": customer_doc.name,
            "contact_name": contact_doc.name if contact_doc else None,
            "address_name": addr_doc.name if addr_doc else None,
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Customer Creation/Update Error")
        return {"success": False, "error": str(e)}


def get_or_create_customer(name, email, phone, country, tax_id, name_arabic="", data=None):
    """Create or update a Customer (Individual or Company)."""
    try:
        cust_type = (
            "Company"
            if data
            and (
                data.get("customer_type") == "company" or data.get("type") == "company"
            )
            else "Individual"
        )

        # Get customer_group and territory from data, with defaults
        customer_group = (
            data.get("customer_group", "All Customer Groups")
            if data
            else "All Customer Groups"
        )
        territory = data.get("territory", country) if data else country

        handled_fields = [
            'name', 'email', 'phone', 'customer_type', 'type', 'address', 
            'taxId', 'name_arabic', 'contactName', 'vatNumber', 
            'registrationScheme', 'registrationNumber', 'customer_group', 
            'territory', 'preferredPaymentMethod', 'status', 'contact_name',
            'customer_name', 'mobile_no', 'email_id', 'tax_id'
        ]

        existing = frappe.get_all(
            "Customer", filters={"customer_name": name}, fields=["name"]
        )
        if existing:
            doc = frappe.get_doc("Customer", existing[0]["name"])
            doc.email_id = email
            doc.mobile_no = phone
            doc.custom_country = country
            doc.customer_type = cust_type
            doc.customer_name_in_arabic = name_arabic
            doc.customer_group = customer_group
            doc.territory = territory
            doc.tax_id = tax_id

            if cust_type == "Company":
                doc.custom_vat_number = data.get("vatNumber")
                doc.custom_payment_method = data.get("preferredPaymentMethod")
                doc.custom_registration_scheme = data.get("registrationScheme")
                doc.custom_registration_number = data.get("registrationNumber")

            if data:
                for key, value in data.items():
                    if key not in handled_fields:
                        if hasattr(doc, key):
                            setattr(doc, key, value)
                        elif hasattr(doc, f"custom_{key}"):
                            setattr(doc, f"custom_{key}", value)
                        elif key.startswith('custom_') and hasattr(doc, key):
                            setattr(doc, key, value)

            doc.save()
        else:
            # Create new
            doc = frappe.get_doc(
                {
                    "doctype": "Customer",
                    "customer_name": name,
                    "customer_type": cust_type,
                    "customer_name_in_arabic": name_arabic,
                    "email_id": email,
                    "mobile_no": phone,
                    "tax_id": tax_id,
                    "custom_country": country,
                    "customer_group": customer_group,
                    "territory": territory,
                    "status": data.get("status", "Active") if data else "Active",
                    "custom_vat_number": (
                        data.get("vatNumber") if cust_type == "Company" else None
                    ),
                    "custom_payment_method": (
                        data.get("preferredPaymentMethod")
                        if cust_type == "Company"
                        else None
                    ),
                    "custom_registration_scheme": (
                        data.get("registrationScheme")
                        if cust_type == "Company"
                        else None
                    ),
                    "custom_registration_number": (
                        data.get("registrationNumber")
                        if cust_type == "Company"
                        else None
                    ),
                }
            )
            if data:
                for key, value in data.items():
                    if key not in handled_fields:
                        if hasattr(doc, key):
                            setattr(doc, key, value)
                        elif hasattr(doc, f"custom_{key}"):
                            setattr(doc, f"custom_{key}", value)
                        elif key.startswith('custom_') and hasattr(doc, key):
                            setattr(doc, key, value)
            doc.insert()
        return doc
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error in get_or_create_customer")
        raise e


@frappe.whitelist(allow_guest=True)
def get_customer_groups():
    """Fetch customer groups based on POS profile configuration."""
    try:
        pos_profile = get_current_pos_profile()

        # Only leaf groups: ERPNext refuses to save a customer under a Group-type one. A
        # profile that lists a group node offers the leaves beneath it.
        filters = {"is_group": 0}
        if hasattr(pos_profile, "customer_groups") and pos_profile.customer_groups:
            allowed = set()
            for d in pos_profile.customer_groups:
                if d.customer_group:
                    allowed.add(d.customer_group)
                    allowed.update(get_descendants_of("Customer Group", d.customer_group))
            filters["name"] = ["in", list(allowed)]

        customer_groups = frappe.get_all(
            "Customer Group",
            filters=filters,
            fields=["name", "customer_group_name"],
            order_by="customer_group_name asc",
        )

        return {"success": True, "data": customer_groups}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error fetching customer groups")
        return {"success": False, "error": str(e)}


@frappe.whitelist(allow_guest=True)
def get_territories():
    """Fetch all territories for dropdown selection."""
    try:
        territories = frappe.get_all(
            "Territory",
            fields=["name", "territory_name"],
            order_by="territory_name asc",
        )

        return {"success": True, "data": territories}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error fetching territories")
        return {"success": False, "error": str(e)}


def create_or_update_contact(customer, customer_name, email, phone):
    existing_contact = frappe.get_all(
        "Contact", filters={"email_id": email, "link_name": customer}, limit=1
    )

    if existing_contact:
        doc = frappe.get_doc("Contact", existing_contact[0].name)
        doc.first_name = customer_name
        doc.phone = phone
        doc.email_id = email
    else:
        # create new document properly
        doc = frappe.get_doc(
            {
                "doctype": "Contact",
                "first_name": customer_name,
                "email_id": email,
                "phone": phone,
                "links": [{"link_doctype": "Customer", "link_name": customer}],
            }
        )

    doc.save(ignore_permissions=True)
    return doc


def create_or_update_address(customer_id, customer_name, address_data, country):
    """Create or update primary Address for the customer."""
    if not address_data:
        return None

    address_title = f"{customer_name} - Primary"

    address_fields = {
        "address_title": address_title,
        "address_type": address_data.get("addressType", "Billing"),
        "address_line1": address_data.get("street", ""),
        "address_line2": address_data.get("buildingNumber", ""),
        "city": address_data.get("city", ""),
        "state": address_data.get("state", ""),
        "county": address_data.get("city", ""),
        "pincode": address_data.get("zipCode", ""),
        "country": country,
        "is_primary_address": 1 if address_data.get("isPrimary") else 0,
        "is_shipping_address": 0,
    }

    existing = frappe.get_all(
        "Address", filters={"address_title": address_title}, fields=["name"]
    )

    if existing:
        doc = frappe.get_doc("Address", existing[0]["name"])
        for field, value in address_fields.items():
            setattr(doc, field, value)
        doc.links = []
    else:
        doc = frappe.new_doc("Address")
        for field, value in address_fields.items():
            setattr(doc, field, value)

    doc.append(
        "links",
        {
            "link_doctype": "Customer",
            "link_name": customer_id,
            "link_title": customer_name,
        },
    )
    doc.save()
    return doc


@frappe.whitelist()
def update_customer(customer_id, customer_data):
    if isinstance(customer_data, str):
        customer_data = json.loads(customer_data)

    try:
        customer = frappe.get_doc("Customer", customer_id)

        # Extract email and phone for contact update
        email = customer_data.get("email")
        phone = customer_data.get("phone")
        customer_name = customer_data.get("name", customer.customer_name)
        address_data = customer_data.get("address", {})
        country = address_data.get("country")
        customer.tax_id = customer_data.get("taxId", "")

        # Update customer fields
        for key, value in customer_data.items():
            if key not in ["email", "phone", "address", "taxId"]:
                setattr(customer, key, value)

        customer.ignore_version = True
        customer.save()

        # Update email_id and mobile_no directly on Customer doctype for consistency
        if email:
            customer.email_id = email
        if phone:
            customer.mobile_no = phone

        # Update the primary contact's child tables if email or phone changed
        if customer.customer_primary_contact and (email or phone):
            try:
                contact_doc = frappe.get_doc(
                    "Contact", customer.customer_primary_contact
                )

                # Update email in child table
                if email:
                    contact_doc.email_ids = []
                    contact_doc.append(
                        "email_ids", {"email_id": email, "is_primary": 1}
                    )

                # Update phone in child table
                if phone:
                    contact_doc.phone_nos = []
                    contact_doc.append(
                        "phone_nos",
                        {
                            "phone": phone,
                            "is_primary_mobile_no": 1,
                            "is_primary_phone": 1,
                        },
                    )

                contact_doc.save()
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Error updating contact for customer {customer_id}",
                )

        # Update address if address data is provided
        if address_data and any(
            address_data.get(field)
            for field in ["street", "city", "state", "zipCode", "buildingNumber"]
        ):
            try:
                addr_doc = create_or_update_address(
                    customer_id, customer_name, address_data, country
                )
                if addr_doc:
                    frappe.db.set_value(
                        "Customer",
                        customer_id,
                        "customer_primary_address",
                        addr_doc.name,
                    )
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Error updating address for customer {customer_id}",
                )

        return {"success": True, "updated_customer": customer.name}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Update Customer Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def get_customer_statistics(customer_id):
    """Get customer statistics including total orders and total spent.

    total_spent shares total_spent_sql with get_customers's list view and
    get_customer_account_summary's cards, so all three agree.
    """
    try:
        total_orders_result = frappe.db.sql(
            f"SELECT ({total_orders_sql('%s')}) as total_orders",
            (customer_id,),
            as_dict=True,
        )
        total_orders = total_orders_result[0].total_orders if total_orders_result else 0

        # Get total amount spent by the customer
        total_spent_result = frappe.db.sql(
            f"SELECT COALESCE(({total_spent_sql('%s')}), 0) as total_spent",
            (customer_id,),
            as_dict=True,
        )

        total_spent = total_spent_result[0].total_spent if total_spent_result else 0

        # Get last visit date (most recent invoice date)
        last_visit_result = frappe.db.sql(
            f"SELECT ({last_visit_sql('%s')}) as last_visit",
            (customer_id,),
            as_dict=True,
        )

        last_visit = last_visit_result[0].last_visit if last_visit_result else None

        return {
            "success": True,
            "data": {
                "total_orders": total_orders,
                "total_spent": total_spent,
                "last_visit": last_visit,
            },
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error fetching customer statistics")
        return {"success": False, "error": str(e)}


@frappe.whitelist(allow_guest=True)
def get_global_totals():
    """Return global totals for customers and invoices for dashboard cards."""
    try:
        total_customers = frappe.db.count("Customer")
        total_invoices = frappe.db.count(
            "Sales Invoice",
            filters={
                "docstatus": 1,
                "is_return": 0,
                "status": ["!=", "Cancelled"],
                "custom_pos_opening_entry": ["!=", ""],  # Only POS-created invoices
            },
        )
        return {
            "success": True,
            "total_customers": total_customers,
            "total_invoices": total_invoices,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Global Customer Totals Error")
        return {
            "success": False,
            "error": str(e),
            "total_customers": 0,
            "total_invoices": 0,
        }


@frappe.whitelist(allow_guest=False)
def check_customer_permission(customer_name):
    """Check if user has permission to access a specific customer"""
    try:
        # Get POS profile details
        pos_profile = get_current_pos_profile()
        business_type = getattr(pos_profile, "custom_business_type", "B2C")

        # Get customer groups from POS profile if configured
        customer_group_names = []
        if hasattr(pos_profile, "customer_groups") and pos_profile.customer_groups:
            customer_group_names = [
                d.customer_group
                for d in pos_profile.customer_groups
                if d.customer_group
            ]

        # Get user permissions for Customer doctype
        user_permitted = frappe.permissions.get_user_permissions(frappe.session.user)
        permitted_customer_names = []
        has_customer_permissions = False
        if user_permitted and "Customer" in user_permitted:
            permitted_customer_names = [
                perm.get("doc") for perm in user_permitted["Customer"]
            ]
            has_customer_permissions = True

        # Check if user has permission to access this specific customer
        has_permission = True

        # Check user permissions first
        if has_customer_permissions:
            if customer_name not in permitted_customer_names:
                has_permission = False
                frappe.logger().info(
                    f"User does not have permission to access customer: {customer_name}"
                )

        # If user has permission, check business type and customer groups
        if has_permission:
            # Get customer details
            customer = frappe.get_doc("Customer", customer_name)

            # Check business type
            if business_type == "B2C" and customer.customer_type == "Company":
                has_permission = False
                frappe.logger().info(
                    f"B2C business type: Customer {customer_name} is Company type"
                )
            elif business_type == "B2B" and customer.customer_type == "Individual":
                has_permission = False
                frappe.logger().info(
                    f"B2B business type: Customer {customer_name} is Individual type"
                )

            # Check customer groups if configured
            if has_permission and customer_group_names:
                if customer.customer_group not in customer_group_names:
                    has_permission = False
                    frappe.logger().info(
                        f"Customer {customer_name} not in allowed groups: {customer_group_names}"
                    )

        return {
            "success": True,
            "has_permission": has_permission,
            "customer_name": customer_name,
            "business_type": business_type,
            "customer_groups": customer_group_names,
            "user_permissions": (
                len(permitted_customer_names) if has_customer_permissions else 0
            ),
        }

    except Exception as e:
        frappe.logger().error(f"Error checking customer permission: {e!s}")
        return {"success": False, "error": str(e), "has_permission": False}


@frappe.whitelist()
def get_required_customer_fields():
    """
    Retrieves the Customer Doctype meta and returns a list of fields
    where 'reqd' (mandatory) is set to True.
    """
    meta = frappe.get_meta("Customer")

    required_fields = [
        {
            "fieldname": f.fieldname,
            "label": f.label,
            "fieldtype": f.fieldtype,
            "options": f.options
        }
        for f in meta.fields
        if f.reqd and f.fieldtype not in ["Section Break", "Column Break", "Tab Break"]
    ]

    return required_fields


def _till_shows_overdue():
    """Whether the caller's current till has 'Show Overdue Warning' on: the one tick that
    decides whether its cashiers see what a customer owes."""
    from klik_pos.api.sales_invoice import get_current_pos_opening_entry

    entry = get_current_pos_opening_entry()
    profile = entry and frappe.db.get_value("POS Opening Entry", entry, "pos_profile")
    return bool(profile and frappe.db.get_value("POS Profile", profile, "custom_show_overdue_warning"))


@frappe.whitelist()
def get_customer_overdue_invoices(customer, company=""):
    """Overdue Sales Invoices (past due date, still outstanding) for a customer, and the total
    the customer owes across every unpaid invoice, due or not.

    Called from the POS on customer selection. Refused unless the caller's till shows overdue
    warnings or the caller is a shift manager: balances are not for every cashier.
    Walk-in customers are excluded on the frontend; this function has no awareness of that.
    """
    from klik_pos.api.shift import is_shift_manager

    if not (is_shift_manager() or _till_shows_overdue()):
        frappe.throw(_("Customer balances are not shown on this till."), frappe.PermissionError)

    customer = (customer or "").strip()
    if not customer:
        return {"has_overdue": False, "invoices": [], "customer_name": "", "total_outstanding": 0.0}

    company = (company or "").strip()
    company_filter = "AND company = %(company)s" if company else ""
    unpaid = frappe.db.sql(f"""
        SELECT name, due_date, grand_total, outstanding_amount, currency, customer_name
        FROM `tabSales Invoice`
        WHERE customer = %(customer)s
          {company_filter}
          AND docstatus = 1
          AND outstanding_amount > 0.001
        ORDER BY due_date ASC
    """, {"customer": customer, "company": company}, as_dict=True)

    cutoff = getdate(today())
    invoices = [row for row in unpaid if row.due_date and getdate(row.due_date) < cutoff]
    customer_name = frappe.db.get_value("Customer", customer, "customer_name") or customer

    return {
        "has_overdue": len(invoices) > 0,
        "invoices": invoices,
        "customer_name": customer_name,
        # ponytail: sums invoice-currency amounts; a customer billed in two currencies needs base_ amounts.
        "total_outstanding": flt(sum(flt(row.outstanding_amount) for row in unpaid), 2),
    }
