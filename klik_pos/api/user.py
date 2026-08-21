import frappe
from frappe import _

from klik_pos.roles import ADMIN_ROLES, DASHBOARD_ROLES


@frappe.whitelist()
def get_user_roles():
	"""
	Get the current user's roles and determine if they have administrative privileges.
	"""
	try:
		user = frappe.session.user
		user_roles = frappe.get_roles(user)

		# Check if user has administrative privileges
		admin_roles = ADMIN_ROLES
		is_admin_user = any(role in admin_roles for role in user_roles)
		can_view_sales_dashboard = any(role in DASHBOARD_ROLES for role in user_roles)

		return {
			"success": True,
			"data": {
				"user": user,
				"roles": user_roles,
				"is_admin_user": is_admin_user,
				"can_view_sales_dashboard": can_view_sales_dashboard,
				"admin_roles": admin_roles,
			},
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Error getting user roles")
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def get_current_user_info():
	"""
	Get comprehensive current user information including roles and POS profile.
	"""
	try:
		import time

		start_time = time.time()

		user = frappe.session.user
		user_roles = frappe.get_roles(user)

		# Check if user has administrative privileges
		admin_roles = ADMIN_ROLES
		is_admin_user = any(role in admin_roles for role in user_roles)
		can_view_sales_dashboard = any(role in DASHBOARD_ROLES for role in user_roles)

		# Get user details
		user_doc = frappe.get_doc("User", user)

		# Get current POS profile
		from klik_pos.klik_pos.utils import get_current_pos_profile

		pos_profile = get_current_pos_profile()

		_total_time = time.time() - start_time

		return {
			"success": True,
			"data": {
				"user": user,
				"full_name": user_doc.full_name or user,
				"email": user_doc.email,
				"roles": user_roles,
				"is_admin_user": is_admin_user,
				"can_view_sales_dashboard": can_view_sales_dashboard,
				"admin_roles": admin_roles,
				"pos_profile": pos_profile.name if pos_profile else None,
				"pos_profile_name": pos_profile.name if pos_profile else None,
			},
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Error getting current user info")
		return {"success": False, "error": str(e)}
