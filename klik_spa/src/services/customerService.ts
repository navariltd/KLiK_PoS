import { extractErrorMessage } from "../utils/errorExtraction";

interface CustomerAddress {
  addressType?: string;
  street: string;
  buildingNumber?: string;
  city: string;
  state?: string;
  zipCode?: string;
  country: string;
}

interface CustomerData {
  name: string;
  customer_type: string;
  email: string;
  phone: string;
  taxId?: string;
  name_arabic?: string;
  address: CustomerAddress;
  preferredPaymentMethod?: string;
  contactName?: string;
  vatNumber?: string;
  registrationScheme?: string;
  registrationNumber?: string;
  customer_group?: string;
  territory?: string;
}

export const useCustomerActions = () => {
  const csrfToken = window.csrf_token;

  const createCustomer = async (customerData: CustomerData) => {
    try {
      const response = await fetch('/api/method/klik_pos.api.customer.create_or_update_customer', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
           "X-Frappe-CSRF-Token": csrfToken,

        },
        body: JSON.stringify({ customer_data: customerData }),
        credentials: 'include'
      });

      const result = await response.json();

      if (!result.message || !result.message.success) {
        throw new Error(result.message?.error || "Customer creation failed");
      }

      return result.message;
    } catch (error) {
      console.error("❌ Error creating customer:", error);
      throw error;
    }
  };

  const updateCustomer = async (customerId: string, customerData: Partial<CustomerData>) => {
    try {
      const response = await fetch('/api/method/klik_pos.api.customer.update_customer', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
             "X-Frappe-CSRF-Token": csrfToken,

        },
        body: JSON.stringify({
          customer_id: customerId,
          customer_data: customerData
        }),
        credentials: 'include'

      });

      const result = await response.json();

      if (!result.message || !result.message.success) {
        throw new Error(result.message?.error || "Customer update failed");
      }

      return result.message;
    } catch (error) {
      console.error("❌ Error updating customer:", error);
      throw error;
    }
  };

  const getCustomerGroups = async () => {
    try {
      const response = await fetch('/api/method/klik_pos.api.customer.get_customer_groups', {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include'
      });

      const result = await response.json();

      if (!result.message || !result.message.success) {
        throw new Error(result.message?.error || "Failed to fetch customer groups");
      }

      return result.message.data;
    } catch (error) {
      console.error("❌ Error fetching customer groups:", error);
      throw error;
    }
  };

  const getTerritories = async () => {
    try {
      const response = await fetch('/api/method/klik_pos.api.customer.get_territories', {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include'
      });

      const result = await response.json();

      if (!result.message || !result.message.success) {
        throw new Error(result.message?.error || "Failed to fetch territories");
      }

      return result.message.data;
    } catch (error) {
      console.error("❌ Error fetching territories:", error);
      throw error;
    }
  };

  return {
    createCustomer,
    updateCustomer,
    getCustomerGroups,
    getTerritories
  };
};

/**
 * The four headline figures for the customer detail page cards, computed server-side from
 * every submitted Sales Invoice (not just POS till history) so they agree with the AR report.
 *
 * `outstanding` is `null` when the AR path could not determine it (permission failure, broken
 * report) — that is distinct from `0`, which means the customer genuinely owes nothing.
 * `currency` is `null` when no company resolved for the session. Callers must render both as a
 * dash, never as zero.
 */
export interface CustomerAccountSummary {
  invoice_count: number;
  net_revenue: number;
  avg_order_value: number;
  outstanding: number | null;
  currency: string | null;
}

export async function getCustomerAccountSummary(customer: string): Promise<CustomerAccountSummary> {
  const response = await fetch(
    `/api/method/klik_pos.api.customer_summary.get_customer_account_summary?customer=${encodeURIComponent(customer)}`,
    {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
    }
  );

  const result = await response.json();

  if (!response.ok || !result.message) {
    throw new Error(extractErrorMessage(result, "Failed to fetch customer account summary"));
  }

  return result.message;
}
