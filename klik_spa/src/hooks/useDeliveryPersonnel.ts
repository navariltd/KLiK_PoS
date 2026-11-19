import { parseAPIResponse } from "../utils/apiResponse";
import { useState, useEffect } from "react";

export interface DeliveryPersonnel {
  name: string;
  delivery_personnel: string;
}

export function useDeliveryPersonnel() {
  const [personnel, setPersonnel] = useState<DeliveryPersonnel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchDeliveryPersonnel = async () => {
      try {
        setLoading(true);
        const response = await fetch(
          "/api/method/klik_pos.api.delivery_personnel.get_delivery_personnel_list",
          {
            method: "GET",
            headers: {
              "Accept": "application/json",
            },
            credentials: "include",
          }
        );

        const data = await parseAPIResponse(response);
        if (response.ok && data.message && data.message.success) {
          setPersonnel(data.message.data || []);
        } else {
          throw new Error(data.message?.error || "Failed to fetch delivery personnel");
        }
      } catch (err: unknown) {
        console.error("Error loading delivery personnel:", err);
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    };

    fetchDeliveryPersonnel();
  }, []);

  return { personnel, loading, error };
}
