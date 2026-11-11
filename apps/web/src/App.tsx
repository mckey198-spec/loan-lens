import { Navigate, Route, Routes } from "react-router-dom";

import { RequireRole } from "./auth/RequireRole";
import { AppLayout } from "./components/Layout";
import { WorkspaceProvider } from "./context/WorkspaceContext";
import { homePathFor, useWorkspace } from "./context/useWorkspace";
import { AdminWorkspacePage } from "./pages/admin/AdminWorkspacePage";
import { LoginPage } from "./pages/LoginPage";
import { ApplicationIntakePage } from "./pages/officer/ApplicationIntakePage";
import { ApplicationListPage } from "./pages/officer/ApplicationListPage";
import { AssessmentViewPage } from "./pages/officer/AssessmentViewPage";
import { ReviewerWorkspacePage } from "./pages/reviewer/ReviewerWorkspacePage";
import { ReviewQueuePage } from "./pages/reviewer/ReviewQueuePage";
import { UnderwritingDashboardPage } from "./pages/reviewer/UnderwritingDashboardPage";
import { ApplicationDetailPage } from "./pages/shared/ApplicationDetailPage";

function HomeRedirect() {
  const { currentUser, loading } = useWorkspace();
  if (loading) {
    return <div className="page-loading">Loading…</div>;
  }
  if (!currentUser) {
    return <Navigate to="/login" replace />;
  }
  return <Navigate to={homePathFor(currentUser.role)} replace />;
}

export default function App() {
  return (
    <WorkspaceProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<AppLayout />}>
          <Route path="/" element={<HomeRedirect />} />
          <Route
            path="/officer/applications"
            element={
              <RequireRole roles={["loan_officer", "admin"]}>
                <ApplicationListPage />
              </RequireRole>
            }
          />
          <Route
            path="/officer/applications/new"
            element={
              <RequireRole roles={["loan_officer", "admin"]}>
                <ApplicationIntakePage />
              </RequireRole>
            }
          />
          <Route
            path="/officer/applications/:applicationId"
            element={
              <RequireRole roles={["loan_officer", "admin"]}>
                <ApplicationIntakePage />
              </RequireRole>
            }
          />
          <Route
            path="/officer/applications/:applicationId/assessment"
            element={
              <RequireRole roles={["loan_officer", "admin"]}>
                <AssessmentViewPage />
              </RequireRole>
            }
          />
          <Route
            path="/reviewer"
            element={
              <RequireRole roles={["reviewer", "admin"]}>
                <ReviewerWorkspacePage />
              </RequireRole>
            }
          />
          <Route
            path="/reviewer/queue"
            element={
              <RequireRole roles={["reviewer", "admin"]}>
                <ReviewQueuePage />
              </RequireRole>
            }
          />
          <Route
            path="/reviewer/applications/:applicationId"
            element={
              <RequireRole roles={["reviewer", "admin"]}>
                <UnderwritingDashboardPage />
              </RequireRole>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireRole roles={["admin"]}>
                <AdminWorkspacePage />
              </RequireRole>
            }
          />
          <Route
            path="/applications/:ref"
            element={
              <RequireRole roles={["loan_officer", "reviewer", "admin"]}>
                <ApplicationDetailPage />
              </RequireRole>
            }
          />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </WorkspaceProvider>
  );
}
