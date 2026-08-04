import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import LandingPage from "./pages/LandingPage";
import MemberLogin from "./pages/MemberLogin";
import MemberDashboard from "./pages/MemberDashboard";
import MemberPolicies from "./pages/MemberPolicies";
import CoverageCheck from "./pages/CoverageCheck";
import ClaimCreation from "./pages/ClaimCreation";
import ClaimSubmission from "./pages/ClaimSubmission";
import AssessorLogin from "./pages/AssessorLogin";
import AssessorDashboard from "./pages/AssessorDashboard";
import AssessorClaimDetails from "./pages/AssessorClaimDetails";
import StartInspection from "./pages/StartInspection";
import RepairShopSubmission from "./pages/RepairShopSubmission";
import AdminLogin from "./pages/AdminLogin";
import AdminDashboard from "./pages/AdminDashboard";
import AdminClaimReport from "./pages/AdminClaimReport";
import NotFound from "./pages/NotFound";
import ClaimChatbot from "./pages/ClaimChatbot";
import ScheduleInspectionChatbot from "./pages/ScheduleInspection";
import StartInspectionChatbot from "./pages/StartInspection";
import RepairShopChatbot from "./pages/RepairShopSubmission";

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/member/login" element={<MemberLogin />} />
          <Route path="/member/dashboard" element={<MemberDashboard />} />
          <Route path="/member/policies" element={<MemberPolicies />} />
          <Route path="/member/coverage-check" element={<ClaimChatbot />} />
          <Route path="/member/claim-creation" element={<ClaimCreation />} />
          <Route path="/member/claim-submission" element={<ClaimSubmission />} />
          <Route path="/assessor/login" element={<AssessorLogin />} />
          <Route path="/assessor/dashboard" element={<AssessorDashboard />} />
          <Route path="/assessor/claim/:claimId" element={<AssessorClaimDetails />} />
          <Route path="/assessor/schedule-inspection/:claimId" element={<ScheduleInspectionChatbot />} />
          <Route path="/assessor/start-inspection/:claimId" element={<StartInspectionChatbot />} />
          <Route path="/repair-shop/:claimId" element={<RepairShopChatbot />} />
          <Route path="/admin/login" element={<AdminLogin />} />
          <Route path="/admin/dashboard" element={<AdminDashboard />} />
          <Route path="/admin/claim/:claimId" element={<AdminClaimReport />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
