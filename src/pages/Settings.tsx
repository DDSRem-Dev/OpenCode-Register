import { useCallback, useEffect, useRef, useState } from "react";
import { CircleAlert, RefreshCw, Settings2 } from "lucide-react";
import {
  applyAutomaticConfiguration,
  fetchAutomaticConfiguration,
  updateAutomaticConfiguration,
  type AutomaticConfiguration,
} from "../services/api";

type SettingsProps = {
  isBackendConnected: boolean;
  isVaultUnlocked: boolean;
  onConfigurationApplied: () => void;
};

type SettingsState =
  | { status: "loading" }
  | { status: "ready"; configuration: AutomaticConfiguration }
  | { status: "error"; message: string };

type Feedback = { status: "success" | "error"; text: string };

const SUCCESS_MESSAGE_DURATION_MS = 4000;

export function Settings({ isBackendConnected, isVaultUnlocked, onConfigurationApplied }: SettingsProps) {
  const [state, setState] = useState<SettingsState>({ status: "loading" });
  const [operation, setOperation] = useState<"save" | "apply" | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  const loadSettings = useCallback(async (signal?: AbortSignal) => {
    setState({ status: "loading" });
    try {
      const configuration = await fetchAutomaticConfiguration(signal);
      if (!signal?.aborted) setState({ status: "ready", configuration });
    } catch (reason) {
      if (!signal?.aborted) setState({ status: "error", message: errorMessage(reason, "无法读取自动配置设置") });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    requestRef.current = controller;
    if (isBackendConnected) {
      void loadSettings(controller.signal);
    } else {
      setState({ status: "error", message: "等待本地服务连接" });
    }
    return () => {
      controller.abort();
      if (requestRef.current === controller) requestRef.current = null;
    };
  }, [isBackendConnected, loadSettings]);

  useEffect(() => {
    if (feedback?.status !== "success") return undefined;
    const timeout = window.setTimeout(() => setFeedback(null), SUCCESS_MESSAGE_DURATION_MS);
    return () => window.clearTimeout(timeout);
  }, [feedback]);

  const saveSettings = async (autoConfigureOpencode: boolean, autoConfigureOmo: boolean) => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setOperation("save");
    setFeedback(null);
    try {
      const configuration = await updateAutomaticConfiguration(
        autoConfigureOpencode,
        autoConfigureOmo,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setState({ status: "ready", configuration });
      setFeedback({ status: "success", text: "自动配置设置已保存" });
    } catch (reason) {
      if (!controller.signal.aborted) setFeedback({ status: "error", text: errorMessage(reason, "无法保存自动配置设置") });
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setOperation(null);
      }
    }
  };

  const handleApply = async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setOperation("apply");
    setFeedback(null);
    try {
      const configuration = await applyAutomaticConfiguration(controller.signal);
      if (controller.signal.aborted) return;
      setState({ status: "ready", configuration });
      setFeedback({ status: "success", text: `已为 ${configuration.appliedCount} 个账号应用本地配置` });
      onConfigurationApplied();
    } catch (reason) {
      if (!controller.signal.aborted) setFeedback({ status: "error", text: errorMessage(reason, "无法应用现有账号配置") });
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setOperation(null);
      }
    }
  };

  const configuration = state.status === "ready" ? state.configuration : null;
  const pendingCount = configuration
    ? Math.max(configuration.opencodePendingCount, configuration.omoPendingCount)
    : 0;

  return (
    <section className="settings-workspace" aria-labelledby="settings-title">
      <div className="settings-heading">
        <div>
          <p className="section-label">本地集成</p>
          <h2 id="settings-title">自动配置</h2>
          <p className="section-description">管理新账号写入 OpenCode 与 Oh My OpenCode 的方式。</p>
        </div>
        <button
          className="icon-button"
          type="button"
          onClick={() => void loadSettings()}
          aria-label="刷新自动配置设置"
          title="刷新设置"
          disabled={state.status === "loading" || operation !== null}
        >
          <RefreshCw size={16} className={state.status === "loading" ? "spin" : ""} />
        </button>
      </div>

      {state.status === "loading" && <div className="account-message" role="status">正在读取设置</div>}
      {state.status === "error" && (
        <div className="error-banner" role="alert"><CircleAlert size={17} /><div><strong>设置不可用</strong><span>{state.message}</span></div></div>
      )}

      {configuration && (
        <div className="settings-panel">
          <div className="settings-row">
            <div className="settings-icon"><Settings2 size={18} /></div>
            <div><strong>自动配置 OpenCode</strong><span>写入 auth.json 与 opencode.json</span></div>
            <label className="switch-control">
              <input
                type="checkbox"
                checked={configuration.autoConfigureOpencode}
                onChange={(event) => void saveSettings(event.target.checked, event.target.checked && configuration.autoConfigureOmo)}
                disabled={operation !== null}
              />
              <span aria-hidden="true" />
              <span className="sr-only">自动配置 OpenCode</span>
            </label>
          </div>
          <div className="settings-row">
            <div className="settings-icon omo"><Settings2 size={18} /></div>
            <div><strong>自动配置 Oh My OpenCode</strong><span>维护账号 fallback_models</span></div>
            <label className="switch-control">
              <input
                type="checkbox"
                checked={configuration.autoConfigureOmo}
                onChange={(event) => void saveSettings(configuration.autoConfigureOpencode, event.target.checked)}
                disabled={operation !== null || !configuration.autoConfigureOpencode}
              />
              <span aria-hidden="true" />
              <span className="sr-only">自动配置 Oh My OpenCode</span>
            </label>
          </div>
          <div className="settings-pending">
            <div><span>OpenCode 待应用</span><strong>{configuration.opencodePendingCount}</strong></div>
            <div><span>Oh My OpenCode 待应用</span><strong>{configuration.omoPendingCount}</strong></div>
            <button
              className="primary-button"
              type="button"
              onClick={() => void handleApply()}
              disabled={operation !== null || pendingCount === 0 || !isVaultUnlocked}
            >
              <RefreshCw size={16} className={operation === "apply" ? "spin" : ""} />
              {isVaultUnlocked ? "应用到现有账号" : "解锁后应用"}
            </button>
          </div>
        </div>
      )}

      {feedback && <div className={feedback.status === "error" ? "error-banner" : "success-banner"} role="status">{feedback.text}</div>}
    </section>
  );
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}
