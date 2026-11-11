import { Outlet } from "react-router-dom";

import { useWorkspace } from "../context/useWorkspace";
import { RoleContextBar } from "./layout/RoleContextBar";
import { TopNavBar } from "./layout/TopNavBar";

export function AppLayout() {
  const { revision } = useWorkspace();

  return (
    <div className="app-shell">
      <TopNavBar />
      <RoleContextBar />
      <main className="app-main" key={revision}>
        <Outlet />
      </main>
    </div>
  );
}
