import type { ComponentType } from "react";

import { Shell } from "./components/Shell";
import { useCoreHealth } from "./hooks/useCoreHealth";
import { useHashRoute } from "./hooks/useHashRoute";
import type { ViewId } from "./navigation";
import "./styles/app.css";
import { BuildEvaluationView } from "./views/BuildEvaluationView";
import { CapabilitiesView } from "./views/CapabilitiesView";
import { HomeView } from "./views/HomeView";
import { ImagesView } from "./views/ImagesView";
import { InstancesChatView } from "./views/InstancesChatView";
import { RuntimesModelsView } from "./views/RuntimesModelsView";
import { SettingsDoctorView } from "./views/SettingsDoctorView";

const VIEW_COMPONENTS: Record<ViewId, ComponentType> = {
  home: HomeView,
  "runtimes-models": RuntimesModelsView,
  capabilities: CapabilitiesView,
  "build-evaluation": BuildEvaluationView,
  images: ImagesView,
  "instances-chat": InstancesChatView,
  "settings-doctor": SettingsDoctorView,
};

export function App() {
  const activeView = useHashRoute();
  const healthQuery = useCoreHealth();
  const View = VIEW_COMPONENTS[activeView];
  const coreStatus = healthQuery.isPending ? "pending" : healthQuery.isSuccess ? "connected" : "unavailable";

  function handleNavigate(view: ViewId) {
    const nextHash = `#/${view}`;
    if (window.location.hash === nextHash) {
      window.scrollTo({ top: 0, behavior: "auto" });
      return;
    }
    window.location.hash = nextHash;
  }

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <Shell
        activeView={activeView}
        onNavigate={handleNavigate}
        coreStatus={coreStatus}
      >
        <View />
      </Shell>
    </>
  );
}
