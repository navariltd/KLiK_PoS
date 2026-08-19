import React, { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { AuthProvider } from "./hooks/useAuth";
import { ThemeProvider } from "./hooks/useTheme";
import { I18nProvider } from "./hooks/useI18n";
import { ProductProvider } from "./providers/ProductProvider";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastContainer } from "react-toastify";
import "react-toastify/dist/ReactToastify.css";
import { setupGlobalErrorHandling } from "./utils/apiUtils";
import RetailSidebar from "./components/RetailSidebar";
import PermissionHealthBanner from "./components/PermissionHealthBanner";
import UnresolvedSalesBanner from "./components/UnresolvedSalesBanner";
import { useQueueFailureAlerts } from "./hooks/useQueueFailureAlerts";

const queryClient = new QueryClient();

function App() {
  useEffect(() => {
    // Set up global error handling for API calls
    setupGlobalErrorHandling();
  }, []);

  // A queued invoice that fails to submit does so after checkout has returned success,
  // so the alert has to reach the cashier wherever they are by then.
  useQueueFailureAlerts();

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ThemeProvider>
          <I18nProvider>
            <ProductProvider>
              <RetailSidebar />
              {/* Unposted sales first: money the till will not balance outranks a
                  warning about figures the cashier can still work past. */}
              <UnresolvedSalesBanner />
              <PermissionHealthBanner />
              <Outlet />
              <ToastContainer position="top-center" autoClose={3000} aria-label="Notification" />
            </ProductProvider>
          </I18nProvider>
        </ThemeProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
