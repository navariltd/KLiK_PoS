import { useEffect, useState } from "react";
import type { PermissionHealth } from "../utils/permissionHealth";

/**
 * Fetch the POS permission preflight once on mount.
 *
 * Fails silent by design: a health check that can itself break the POS is worse than no health
 * check. Any error leaves the result null and the banner renders nothing.
 */
export function usePermissionHealth() {
  const [health, setHealth] = useState<PermissionHealth | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const response = await fetch(
          "/api/method/klik_pos.api.permission_health.get_permission_health",
        );
        if (!response.ok) return;

        const data = await response.json();
        const message = data?.message;
        if (!cancelled && message && Array.isArray(message.missing)) {
          setHealth(message as PermissionHealth);
        }
      } catch {
        // Deliberately silent — see the note above.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return health;
}
