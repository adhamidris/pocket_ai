import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Index from "./pages/Index";
import NotFound from "./pages/NotFound";
import Register from "./pages/Register";
import Privacy from "./pages/Privacy";
import Terms from "./pages/Terms";
import GDPR from "./pages/GDPR";
import Compliance from "./pages/Compliance";
import Cookies from "./pages/Cookies";
import Security from "./pages/Security";
import Integrations from "./pages/Integrations";
import Dashboard from "./pages/Dashboard";
import Home2 from "./pages/Home2";
import Customers from "./pages/Customers";
import Conversations from "./pages/Conversations";
import Cases from "./pages/Cases";
import Agents from "./pages/Agents";
import Knowledge from "./pages/Knowledge";
import Leads from "./pages/Leads";
import ScrollToTop from "@/components/ScrollToTop";

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider delayDuration={120}>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <ScrollToTop />
        <Routes>
          <Route path="/" element={<Index />} />
          <Route path="/register" element={<Register />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route path="/terms" element={<Terms />} />
          <Route path="/gdpr" element={<GDPR />} />
          <Route path="/compliance" element={<Compliance />} />
          <Route path="/cookies" element={<Cookies />} />
          <Route path="/security" element={<Security />} />
          <Route path="/integrations" element={<Integrations />} />
          <Route path="/dashboard" element={<Home2 />} />
          <Route path="/dashboard/customers" element={<Customers />} />
          <Route path="/dashboard/leads" element={<Leads />} />
          <Route path="/dashboard/cases" element={<Cases />} />
          <Route path="/dashboard/conversations" element={<Conversations />} />
          <Route path="/dashboard/agents" element={<Agents />} />
          <Route path="/dashboard/knowledge" element={<Knowledge />} />
          {/* ADD ALL CUSTOM ROUTES ABOVE THE CATCH-ALL "*" ROUTE */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
