import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { CircleAlert, Gauge, KeyRound, RefreshCw, UsersRound } from "lucide-react";
import { AccountRow } from "../components/AccountRow";
import { AccountTransfer } from "../components/AccountTransfer";
import {
  fetchAccounts,
  fetchVaultStatus,
  refreshAllQuotas,
  unlockVault,
  type AccountSummary,
} from "../services/api";

type DashboardProps = {
  isBackendConnected: boolean;
  onVaultStatusChange: (unlocked: boolean) => void;
  view?: "accounts" | "transfer";
  refreshToken?: number;
};

type DashboardState =
  | { status: "checking" }
  | { status: "locked"; isInitialized: boolean }
  | { status: "loading" }
  | { status: "ready"; accounts: AccountSummary[] }
  | { status: "error"; message: string };

type QuotaMessage =
  | { status: "success"; text: string }
  | { status: "warning"; text: string }
  | { status: "error"; text: string };

const QUOTA_WARNING_PERCENT = 80;
const QUOTA_SUCCESS_MESSAGE_DURATION_MS = 4000;

export function Dashboard({
  isBackendConnected,
  onVaultStatusChange,
  view = "accounts",
  refreshToken = 0,
}: DashboardProps) {
  const [state, setState] = useState<DashboardState>({ status: "checking" });
  const [masterPassword, setMasterPassword] = useState("");
  const [masterPasswordConfirmation, setMasterPasswordConfirmation] = useState("");
  const [isUnlocking, setIsUnlocking] = useState(false);
  const [isRefreshingQuotas, setIsRefreshingQuotas] = useState(false);
  const [unlockError, setUnlockError] = useState<string | null>(null);
  const [quotaMessage, setQuotaMessage] = useState<QuotaMessage | null>(null);
  const loadGenerationRef = useRef(0);
  const lifecycleGenerationRef = useRef(0);
  const operationControllerRef = useRef<AbortController | null>(null);
  const checkedAccounts = state.status === "ready"
    ? state.accounts.filter((account) => account.quotaTotal !== null
      && account.quotaTotal > 0
      && account.quotaUsed !== null)
    : [];
  const allCheckedAccountsNearLimit = checkedAccounts.length > 0
    && checkedAccounts.every((account) => account.quotaTotal !== null
      && account.quotaTotal > 0
      && account.quotaUsed !== null
      && (account.quotaUsed / account.quotaTotal) * 100 >= QUOTA_WARNING_PERCENT);
  const activeAccountCount = state.status === "ready"
    ? state.accounts.filter((account) => account.status === "active").length
    : 0;

  const loadAccounts = useCallback(async (signal?: AbortSignal) => {
    const loadGeneration = loadGenerationRef.current + 1;
    loadGenerationRef.current = loadGeneration;
    setState({ status: "loading" });
    try {
      const accounts = await fetchAccounts(signal);
      if (signal?.aborted || loadGenerationRef.current !== loadGeneration) return;
      setState({ status: "ready", accounts });
    } catch (reason) {
      if (!signal?.aborted && loadGenerationRef.current === loadGeneration) {
        setState({ status: "error", message: errorMessage(reason, "无法读取账号列表") });
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const lifecycleGeneration = lifecycleGenerationRef.current + 1;
    lifecycleGenerationRef.current = lifecycleGeneration;
    if (!isBackendConnected) {
      onVaultStatusChange(false);
      setState({ status: "checking" });
      return () => {
        controller.abort();
        operationControllerRef.current?.abort();
        loadGenerationRef.current += 1;
      };
    }
    void fetchVaultStatus(controller.signal)
      .then((vault) => {
        if (controller.signal.aborted || lifecycleGenerationRef.current !== lifecycleGeneration) return undefined;
        onVaultStatusChange(vault.unlocked);
        if (vault.unlocked) return loadAccounts(controller.signal);
        setState({ status: "locked", isInitialized: vault.initialized });
        return undefined;
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted && lifecycleGenerationRef.current === lifecycleGeneration) {
          onVaultStatusChange(false);
          setState({ status: "error", message: errorMessage(reason, "无法读取账号库状态") });
        }
      });
    return () => {
      controller.abort();
      operationControllerRef.current?.abort();
      loadGenerationRef.current += 1;
      if (lifecycleGenerationRef.current === lifecycleGeneration) lifecycleGenerationRef.current += 1;
    };
  }, [isBackendConnected, loadAccounts, onVaultStatusChange, refreshToken]);

  useEffect(() => {
    if (quotaMessage?.status !== "success") return undefined;
    const timeout = window.setTimeout(() => {
      setQuotaMessage((currentMessage) => currentMessage === quotaMessage ? null : currentMessage);
    }, QUOTA_SUCCESS_MESSAGE_DURATION_MS);
    return () => window.clearTimeout(timeout);
  }, [quotaMessage]);

  const handleRefreshAllQuotas = useCallback(async () => {
    operationControllerRef.current?.abort();
    const controller = new AbortController();
    operationControllerRef.current = controller;
    setIsRefreshingQuotas(true);
    setQuotaMessage(null);
    try {
      const results = await refreshAllQuotas(controller.signal);
      if (controller.signal.aborted) return;
      const unavailable = results.filter((result) => result.status === "unavailable").length;
      if (unavailable > 0) {
        setQuotaMessage({ status: "warning", text: `${unavailable} 个账号暂时无法取得可信月度用量` });
      } else {
        setQuotaMessage({ status: "success", text: `已刷新 ${results.length} 个账号的月度用量` });
      }
      await loadAccounts(controller.signal);
    } catch (reason) {
      if (!controller.signal.aborted) {
        setQuotaMessage({ status: "error", text: errorMessage(reason, "无法刷新全部月度用量") });
      }
    } finally {
      if (operationControllerRef.current === controller) {
        operationControllerRef.current = null;
        setIsRefreshingQuotas(false);
      }
    }
  }, [loadAccounts]);

  const handleUnlock = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!masterPassword || state.status !== "locked") return;
    operationControllerRef.current?.abort();
    const controller = new AbortController();
    operationControllerRef.current = controller;
    setIsUnlocking(true);
    setUnlockError(null);
    try {
      if (!state.isInitialized && masterPassword !== masterPasswordConfirmation) {
        setUnlockError("两次输入的主密码不一致");
        return;
      }
      await unlockVault(
        masterPassword,
        state.isInitialized ? undefined : masterPasswordConfirmation,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      onVaultStatusChange(true);
      setMasterPassword("");
      setMasterPasswordConfirmation("");
      await loadAccounts(controller.signal);
    } catch (reason) {
      if (!controller.signal.aborted) {
        onVaultStatusChange(false);
        setUnlockError(errorMessage(reason, "无法解锁账号库"));
      }
    } finally {
      if (operationControllerRef.current === controller) {
        operationControllerRef.current = null;
        setMasterPassword("");
        setIsUnlocking(false);
      }
    }
  }, [loadAccounts, masterPassword, masterPasswordConfirmation, onVaultStatusChange, state]);

  return (
    <section className="account-workspace" aria-labelledby="accounts-title">
      <div className="account-heading">
        <div>
          <p className="section-label">{view === "accounts" ? "本地号池" : "加密账号包"}</p>
          <h2 id="accounts-title">{view === "accounts" ? "账号管理" : "数据迁移"}</h2>
          <p className="section-description">
            {view === "accounts" ? "集中查看账号可用性和 OpenCode Go 月度用量。" : "使用独立密码迁移账号，导入内容会按当前号池重新分配。"}
          </p>
        </div>
        {view === "accounts" && (state.status === "ready" || state.status === "error" || state.status === "loading") && (
          <div className="account-heading-actions">
            {state.status === "ready" && state.accounts.length > 0 && (
              <button
                className="secondary-button"
                type="button"
                onClick={() => void handleRefreshAllQuotas()}
                disabled={isRefreshingQuotas}
              >
                <RefreshCw size={15} className={isRefreshingQuotas ? "spin" : ""} />
                刷新全部月度用量
              </button>
            )}
            <button
              className="icon-button"
              type="button"
              onClick={() => void loadAccounts()}
              title="刷新账号列表"
              aria-label="刷新账号列表"
              disabled={state.status === "loading"}
            >
              <RefreshCw size={16} className={state.status === "loading" ? "spin" : ""} />
            </button>
          </div>
        )}
      </div>

      {view === "accounts" && state.status === "ready" && (
        <div className="account-summary" aria-label="账号概览">
          <div><UsersRound size={17} /><span>账号总数<strong>{state.accounts.length}</strong></span></div>
          <div><span className="summary-indicator available" /><span>当前可用<strong>{activeAccountCount}</strong></span></div>
          <div><Gauge size={17} /><span>已检查月度用量<strong>{checkedAccounts.length}</strong></span></div>
        </div>
      )}

      {view === "accounts" && quotaMessage && (
        <div className={`quota-message ${quotaMessage.status}`} role="status">{quotaMessage.text}</div>
      )}

      {view === "accounts" && allCheckedAccountsNearLimit && (
        <div className="quota-warning" role="status">
          <CircleAlert size={17} />
          <span>所有已检查账号月度用量均达到 80% 以上，请准备创建新账号。</span>
        </div>
      )}

      {state.status === "locked" && (
        <form className="unlock-panel" onSubmit={(event) => void handleUnlock(event)}>
          <div className="unlock-icon"><KeyRound size={20} /></div>
          <div className="unlock-copy">
            <strong>{state.isInitialized ? "解锁账号库" : "配置主密码"}</strong>
            <span>
              {state.isInitialized
                ? "输入主密码后即可查看账号，并继续创建或管理账号。"
                : "主密码用于加密保存在本机的账号凭据。请设置至少 8 位并妥善保管，遗忘后无法找回。"}
            </span>
          </div>
          <div className={`unlock-controls${state.isInitialized ? " single" : ""}`}>
            <label className="unlock-field">
              <span>{state.isInitialized ? "主密码" : "设置主密码"}</span>
              <input
                type="password"
                value={masterPassword}
                onChange={(event) => setMasterPassword(event.target.value)}
                minLength={8}
                maxLength={256}
                autoComplete={state.isInitialized ? "current-password" : "new-password"}
                required
              />
            </label>
            {!state.isInitialized && (
              <label className="unlock-field">
                <span>再次输入主密码</span>
                <input
                  type="password"
                  value={masterPasswordConfirmation}
                  onChange={(event) => setMasterPasswordConfirmation(event.target.value)}
                  minLength={8}
                  maxLength={256}
                  autoComplete="new-password"
                  required
                />
              </label>
            )}
            <button className="primary-button" type="submit" disabled={isUnlocking || masterPassword.length < 8}>
              <KeyRound size={16} />
              {state.isInitialized
                ? (isUnlocking ? "正在解锁" : "解锁账号库")
                : (isUnlocking ? "正在配置" : "完成配置")}
            </button>
          </div>
          {unlockError && (
            <div className="error-banner" role="alert">
              <div>
                <strong>{state.isInitialized ? "无法解锁账号库" : "无法配置主密码"}</strong>
                <span>{unlockError}</span>
              </div>
            </div>
          )}
        </form>
      )}

      {(state.status === "checking" || state.status === "loading") && (
        <div className="account-message" role="status">{isBackendConnected ? "正在读取账号列表" : "等待本地服务"}</div>
      )}

      {state.status === "error" && (
        <div className="error-banner" role="alert">
          <div><strong>账号列表不可用</strong><span>{state.message}</span></div>
        </div>
      )}

      {view === "accounts" && state.status === "ready" && state.accounts.length === 0 && (
        <div className="account-message empty" role="status">尚未保存任何账号</div>
      )}

      {view === "accounts" && state.status === "ready" && state.accounts.length > 0 && (
        <div className="account-table">
          <div className="account-table-header" aria-hidden="true">
            <span>账号</span><span>Provider</span><span>月度用量</span><span>状态</span><span>操作</span>
          </div>
          <div className="account-list">
          {state.accounts.map((account) => (
            <AccountRow account={account} key={account.uuid} onChanged={() => void loadAccounts()} />
          ))}
          </div>
        </div>
      )}

      {view === "transfer" && state.status === "ready" && <AccountTransfer onImported={() => void loadAccounts()} />}
    </section>
  );
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}
