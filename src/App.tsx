import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArchiveRestore,
  CircleAlert,
  FlaskConical,
  Settings as SettingsIcon,
  LayoutList,
  Plus,
} from "lucide-react";
import { fetchHealth, startBackend, type HealthResponse } from "./services/api";
import { CreateFlow } from "./pages/CreateFlow";
import { Dashboard } from "./pages/Dashboard";
import { Settings } from "./pages/Settings";

type ConnectionState = "connecting" | "connected" | "offline";
type WorkspaceView = "accounts" | "create" | "transfer" | "settings";

const viewDetails: Record<WorkspaceView, { title: string; description: string }> = {
  accounts: { title: "账号", description: "查看状态、月度用量与本地号池成员" },
  create: { title: "创建账号", description: "启动并跟踪一个新的账号创建流程" },
  transfer: { title: "数据迁移", description: "导入或导出加密账号包" },
  settings: { title: "设置", description: "管理本地账号配置写入行为" },
};

export default function App() {
  const [activeView, setActiveView] = useState<WorkspaceView>("accounts");
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isVaultUnlocked, setIsVaultUnlocked] = useState(false);
  const [accountRefreshToken, setAccountRefreshToken] = useState(0);
  const connectionAttemptRef = useRef(0);

  const connect = useCallback(async () => {
    const connectionAttempt = connectionAttemptRef.current + 1;
    connectionAttemptRef.current = connectionAttempt;
    setConnection("connecting");
    setError(null);
    setIsVaultUnlocked(false);
    try {
      await startBackend();
      if (connectionAttemptRef.current !== connectionAttempt) return;

      let response: HealthResponse | null = null;
      for (let probeAttempt = 0; probeAttempt < 20; probeAttempt += 1) {
        try {
          response = await fetchHealth();
          break;
        } catch {
          await new Promise((resolve) => window.setTimeout(resolve, 250));
          if (connectionAttemptRef.current !== connectionAttempt) return;
        }
      }
      if (!response) throw new Error("无法连接本地服务");
      if (connectionAttemptRef.current !== connectionAttempt) return;

      setHealth(response);
      setConnection("connected");
    } catch (reason) {
      if (connectionAttemptRef.current !== connectionAttempt) return;
      setHealth(null);
      setConnection("offline");
      setError(reason instanceof Error ? reason.message : "未知连接错误");
    }
  }, []);

  useEffect(() => {
    void connect();
    return () => {
      connectionAttemptRef.current += 1;
    };
  }, [connect]);

  const statusLabel = {
    connecting: "正在连接",
    connected: "服务正常",
    offline: "服务离线",
  }[connection];
  const activeDetails = viewDetails[activeView];

  return (
    <div className="desktop-shell">
      <aside className="sidebar">
        <div className="app-brand">
          <div className="brand-mark">OC</div>
          <div><strong>OpenCode Register</strong><span>账号管理器</span></div>
        </div>

        <nav className="sidebar-nav" aria-label="主导航">
          <button className={activeView === "accounts" ? "active" : ""} type="button" onClick={() => setActiveView("accounts")}>
            <LayoutList size={17} />账号
          </button>
          <button className={activeView === "create" ? "active" : ""} type="button" onClick={() => setActiveView("create")}>
            <Plus size={17} />创建账号
          </button>
          <button className={activeView === "transfer" ? "active" : ""} type="button" onClick={() => setActiveView("transfer")}>
            <ArchiveRestore size={17} />数据迁移
          </button>
          <button className={activeView === "settings" ? "active" : ""} type="button" onClick={() => setActiveView("settings")}>
            <SettingsIcon size={17} />设置
          </button>
        </nav>
      </aside>

      <section className="window-content">
        <header className="workspace-toolbar">
          <div><h1>{activeDetails.title}</h1><p>{activeDetails.description}</p></div>
          <button
            className={`connection-badge ${connection}`}
            type="button"
            onClick={() => void connect()}
            aria-label={`连接状态：${statusLabel}；重新连接`}
            title={`${statusLabel}，点击重新连接`}
          >
            <span className="status-dot" />
          </button>
        </header>

        <main className="workspace-content">
          {error && (
            <div className="error-banner global-banner" role="alert">
              <CircleAlert size={18} />
              <div><strong>本地服务连接失败</strong><span>{error}</span></div>
            </div>
          )}
          {health?.storage_mode === "sandbox" && (
            <div className="sandbox-banner global-banner" role="status">
              <FlaskConical size={17} />
              <div><strong>沙盒模式</strong><span>账号库与 OpenCode 配置使用隔离目录。</span></div>
            </div>
          )}

          <div className="workspace-view" hidden={activeView === "create" || activeView === "settings"}>
            <Dashboard
              isBackendConnected={connection === "connected"}
              onVaultStatusChange={setIsVaultUnlocked}
              view={activeView === "transfer" ? "transfer" : "accounts"}
              refreshToken={accountRefreshToken}
            />
          </div>
          <div className="workspace-view" hidden={activeView !== "create"}>
            <CreateFlow
              isBackendConnected={connection === "connected"}
              isVaultUnlocked={isVaultUnlocked}
              onAccountCreated={() => setAccountRefreshToken((currentToken) => currentToken + 1)}
            />
          </div>
          <div className="workspace-view" hidden={activeView !== "settings"}>
            <Settings
              isBackendConnected={connection === "connected"}
              isVaultUnlocked={isVaultUnlocked}
              onConfigurationApplied={() => setAccountRefreshToken((currentToken) => currentToken + 1)}
            />
          </div>
        </main>
      </section>
    </div>
  );
}
