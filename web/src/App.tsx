import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { SessionProvider } from "./lib/session";
import AgentDetail from "./pages/AgentDetail";
import Agents from "./pages/Agents";
import Approvals from "./pages/Approvals";
import Audit from "./pages/Audit";
import Catalog from "./pages/Catalog";
import Dashboard from "./pages/Dashboard";
import Designer from "./pages/Designer";
import EvaluationDetail from "./pages/EvaluationDetail";
import Evaluations from "./pages/Evaluations";
import ExecutionDetail from "./pages/ExecutionDetail";
import Executions from "./pages/Executions";
import Knowledge from "./pages/Knowledge";
import Models from "./pages/Models";
import Settings from "./pages/Settings";

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="agents" element={<Agents />} />
        <Route path="agents/:id" element={<AgentDetail />} />
        <Route path="designer" element={<Designer />} />
        <Route path="designer/:id" element={<Designer />} />
        <Route path="executions" element={<Executions />} />
        <Route path="executions/:id" element={<ExecutionDetail />} />
        <Route path="approvals" element={<Approvals />} />
        <Route path="evaluations" element={<Evaluations />} />
        <Route path="evaluations/:id" element={<EvaluationDetail />} />
        <Route path="models" element={<Models />} />
        <Route path="catalog" element={<Catalog />} />
        <Route path="knowledge" element={<Knowledge />} />
        <Route path="settings" element={<Settings />} />
        <Route path="audit" element={<Audit />} />
        <Route path="*" element={<div className="py-20 text-center text-slate-500">Page not found.</div>} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <SessionProvider>
        <AppRoutes />
      </SessionProvider>
    </BrowserRouter>
  );
}
