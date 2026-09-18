import { parseAPIResponse } from "../utils/apiResponse";
import {  useState} from "react";

// HOOK 2: Create POS Opening Entry
interface OpeningBalance {
  mode_of_payment: string;
  opening_amount: number;
}

interface UseCreateOpeningReturn {
  createOpeningEntry: (openingBalance: OpeningBalance[], posProfile?: string) => Promise<void>;
  isCreating: boolean;
  error: string | null;
  success: boolean;
}

export function useCreatePOSOpeningEntry(): UseCreateOpeningReturn {
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const createOpeningEntry = async (openingBalance: OpeningBalance[], posProfile?: string) => {
    setIsCreating(true);
    setError(null);
    setSuccess(false);
    const csrfToken = window.csrf_token;

    try {
      //eslint-disable-next-line @typescript-eslint/no-explicit-any
      const requestBody: any = { opening_balance: openingBalance };
      if (posProfile) {
        requestBody.pos_profile = posProfile;
      }

      const res = await fetch("/api/method/klik_pos.api.pos_entry.create_opening_entry", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          'X-Frappe-CSRF-Token': csrfToken
        },
        body: JSON.stringify(requestBody),
        credentials: "include"
      });

      const data = await parseAPIResponse(res);

      if (res.ok && data.message) {
        setSuccess(true);
      } else {
        throw new Error(data._server_messages || "Failed to create opening entry");
      }
            //eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (err: any) {
      console.error("Error creating POS Opening Entry:", err);
      setError(err.message || "Unexpected error occurred");
    } finally {
      setIsCreating(false);
    }
  };

  return {
    createOpeningEntry,
    isCreating,
    error,
    success,
  };
}
