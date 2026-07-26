import { FormEvent, useEffect, useId, useRef, useState } from "react";
import { CircleOff, Copy, RefreshCw, Trash2, UserRound, X } from "lucide-react";
import {
  cancelAccountCleanup,
  confirmAccountCleanup,
  copyAccountApiKey,
  markAccountExhausted,
  refreshAccountQuota,
  startAccountCleanup,
  type AccountCleanupSession,
  type AccountStatus,
  type AccountSummary,
} from "../services/api";

type AccountRowProps = {
  account: AccountSummary;
  onChanged: () => void;
};

type CleanupDialogState =
  | { status: "closed" }
  | { status: "confirm"; confirmedUsername: string; error: string | null }
  | { status: "active"; session: AccountCleanupSession; error: string | null };

type AccountMessage = {
  text: string;
  isTransient: boolean;
};

const statusLabels: Record<AccountStatus, string> = {
  active: "可用",
  exhausted: "额度已用尽",
  invalid: "已失效",
  pending_setup: "待完成配置",
  pending_payment: "等待付款",
  cancelled: "已取消",
};

const invalidReasonLabels = {
  github_credentials_invalid: "GitHub 登录凭据已失效",
  subscription_required: "OpenCode Go 未订阅或订阅已到期",
  unknown: "历史记录未保存详细失效原因",
};
const SUCCESS_MESSAGE_DURATION_MS = 4000;

export function AccountRow({ account, onChanged }: AccountRowProps) {
  const dialogTitleId = useId();
  const requestRef = useRef<AbortController | null>(null);
  const [operation, setOperation] = useState<"copy" | "quota" | "exhausted" | "cleanup" | null>(null);
  const [message, setMessage] = useState<AccountMessage | null>(null);
  const [cleanupDialog, setCleanupDialog] = useState<CleanupDialogState>({ status: "closed" });

  useEffect(() => () => {
    requestRef.current?.abort();
    requestRef.current = null;
  }, []);

  useEffect(() => {
    if (!message?.isTransient) return undefined;
    const timeout = window.setTimeout(() => {
      setMessage((currentMessage) => currentMessage === message ? null : currentMessage);
    }, SUCCESS_MESSAGE_DURATION_MS);
    return () => window.clearTimeout(timeout);
  }, [message]);

  const beginOperation = (nextOperation: "copy" | "quota" | "exhausted" | "cleanup") => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setOperation(nextOperation);
    return controller;
  };

  const finishOperation = (controller: AbortController) => {
    if (requestRef.current === controller) {
      requestRef.current = null;
      setOperation(null);
    }
  };

  const handleQuotaRefresh = async () => {
    const controller = beginOperation("quota");
    setMessage(null);
    try {
      const result = await refreshAccountQuota(account.uuid, controller.signal);
      if (requestRef.current !== controller) return;
      setMessage({
        text: result.message,
        isTransient: result.status === "updated" || result.status === "exhausted",
      });
      onChanged();
    } catch (reason) {
      if (requestRef.current === controller) {
        setMessage({ text: errorMessage(reason, "无法刷新月度用量"), isTransient: false });
      }
    } finally {
      finishOperation(controller);
    }
  };

  const handleCopyApiKey = async () => {
    const controller = beginOperation("copy");
    setMessage(null);
    try {
      await copyAccountApiKey(account.uuid, controller.signal);
      if (requestRef.current === controller) {
        setMessage({ text: "API Key 已复制到剪贴板", isTransient: true });
      }
    } catch (reason) {
      if (requestRef.current === controller) {
        setMessage({ text: errorMessage(reason, "无法复制 API Key"), isTransient: false });
      }
    } finally {
      finishOperation(controller);
    }
  };

  const handleMarkExhausted = async () => {
    const controller = beginOperation("exhausted");
    setMessage(null);
    try {
      await markAccountExhausted(account.uuid, controller.signal);
      if (requestRef.current !== controller) return;
      onChanged();
    } catch (reason) {
      if (requestRef.current === controller) {
        setMessage({ text: errorMessage(reason, "无法标记账号"), isTransient: false });
      }
    } finally {
      finishOperation(controller);
    }
  };

  const handleStartCleanup = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (cleanupDialog.status !== "confirm" || cleanupDialog.confirmedUsername !== account.githubUsername) return;
    const controller = beginOperation("cleanup");
    try {
      const session = await startAccountCleanup(account.uuid, cleanupDialog.confirmedUsername, controller.signal);
      if (requestRef.current === controller) applyCleanupResult(session);
    } catch (reason) {
      if (requestRef.current === controller) {
        setCleanupDialog({ ...cleanupDialog, error: errorMessage(reason, "无法启动账号删除") });
      }
    } finally {
      finishOperation(controller);
    }
  };

  const handleContinueCleanup = async () => {
    if (cleanupDialog.status !== "active") return;
    const controller = beginOperation("cleanup");
    try {
      const session = cleanupDialog.session.status === "error"
        ? await startAccountCleanup(account.uuid, account.githubUsername, controller.signal)
        : await confirmAccountCleanup(account.uuid, controller.signal);
      if (requestRef.current === controller) applyCleanupResult(session);
    } catch (reason) {
      if (requestRef.current === controller) {
        setCleanupDialog({ ...cleanupDialog, error: errorMessage(reason, "无法继续账号删除") });
      }
    } finally {
      finishOperation(controller);
    }
  };

  const applyCleanupResult = (session: AccountCleanupSession) => {
    if (session.status === "done") {
      setCleanupDialog({ status: "closed" });
      onChanged();
      return;
    }
    setCleanupDialog({ status: "active", session, error: session.errorMessage });
  };

  const handleCloseCleanup = async () => {
    if (cleanupDialog.status === "active" && !["done", "error", "cancelled"].includes(cleanupDialog.session.status)) {
      const controller = beginOperation("cleanup");
      try {
        await cancelAccountCleanup(account.uuid, controller.signal);
      } catch (reason) {
        if (requestRef.current === controller) {
          setCleanupDialog({ ...cleanupDialog, error: errorMessage(reason, "无法取消账号删除") });
        }
        finishOperation(controller);
        return;
      }
      if (requestRef.current !== controller) return;
      finishOperation(controller);
    }
    setCleanupDialog({ status: "closed" });
  };

  const quotaPercent = account.quotaTotal && account.quotaUsed !== null
    ? Math.min(100, Math.round((account.quotaUsed / account.quotaTotal) * 100))
    : null;
  const quotaUpdatedAt = account.quotaUpdatedAt ? formatQuotaUpdatedAt(account.quotaUpdatedAt) : "尚未检查";

  return (
    <>
      <article className="account-row">
        <div className="account-avatar"><UserRound size={18} /></div>
        <div className="account-identity">
          <strong>{account.githubUsername}</strong>
          <span>{account.githubEmailMasked}</span>
        </div>
        <div className="account-provider">
          <span>Provider</span>
          <strong>{account.opencodeProviderName ?? "尚未配置"}</strong>
          {!account.opencodeConfigured && <small>OpenCode 配置待应用</small>}
          {account.opencodeConfigured && !account.omoConfigured && <small>Oh My OpenCode 配置待应用</small>}
        </div>
        {account.status === "invalid" ? (
          <div className="account-quota invalid-detail">
            <div><span>检查日期</span><strong>{formatQuotaUpdatedAt(account.quotaCheckedAt ?? account.updatedAt)}</strong></div>
            <small>{invalidReasonLabels[account.quotaInvalidReason ?? "unknown"]}</small>
          </div>
        ) : (
          <div className="account-quota">
            <div><span>月度用量</span><strong>{quotaPercent === null ? "未检查" : `${quotaPercent}%`}</strong></div>
            <progress max={100} value={quotaPercent ?? 0} aria-label={`${account.githubUsername} 月度用量`} />
            <small>{quotaUpdatedAt}</small>
          </div>
        )}
        <span className={`account-status ${account.status}`}>{statusLabels[account.status]}</span>
        <div className="account-actions">
          <button
            className="icon-button"
            type="button"
            onClick={() => void handleCopyApiKey()}
            aria-label={`复制 ${account.githubUsername} API Key`}
            title="复制 API Key"
            data-tooltip="复制 API Key"
            disabled={operation !== null || account.opencodeProviderName === null}
          >
            <Copy size={15} />
          </button>
          <button
            className="icon-button"
            type="button"
            onClick={() => void handleQuotaRefresh()}
            aria-label={`刷新 ${account.githubUsername} 月度用量`}
            title="刷新月度用量"
            data-tooltip="刷新月度用量"
            disabled={operation !== null || account.opencodeProviderName === null}
          >
            <RefreshCw size={15} className={operation === "quota" ? "spin" : ""} />
          </button>
          <button
            className="icon-button"
            type="button"
            onClick={() => void handleMarkExhausted()}
            aria-label={`标记 ${account.githubUsername} 额度已用尽`}
            title="标记额度已用尽"
            data-tooltip="标记额度已用尽"
            disabled={operation !== null || account.status === "exhausted" || account.opencodeProviderName === null}
          >
            <CircleOff size={15} />
          </button>
          <button
            className="icon-button danger"
            type="button"
            onClick={() => setCleanupDialog({ status: "confirm", confirmedUsername: "", error: null })}
            aria-label={`删除 ${account.githubUsername}`}
            title="删除账号"
            data-tooltip="删除账号"
            disabled={operation !== null}
          >
            <Trash2 size={15} />
          </button>
        </div>
        {message && <div className="account-feedback" role="status">{message.text}</div>}
      </article>

      {cleanupDialog.status !== "closed" && (
        <div className="dialog-backdrop" role="presentation">
          <section className="cleanup-dialog" role="dialog" aria-modal="true" aria-labelledby={dialogTitleId}>
            <div className="dialog-heading">
              <h3 id={dialogTitleId}>删除 GitHub 账号</h3>
              <button
                className="icon-button"
                type="button"
                onClick={() => void handleCloseCleanup()}
                aria-label="关闭删除账号对话框"
                disabled={operation === "cleanup"}
              ><X size={17} /></button>
            </div>
            {cleanupDialog.status === "confirm" ? (
              <form className="cleanup-confirm" onSubmit={(event) => void handleStartCleanup(event)}>
                <p>输入完整 GitHub 用户名。提交后程序将自动永久删除该 GitHub 账号并清理本地号池。</p>
                <label>
                  <span>GitHub 用户名</span>
                  <input
                    value={cleanupDialog.confirmedUsername}
                    onChange={(event) => setCleanupDialog({ ...cleanupDialog, confirmedUsername: event.target.value, error: null })}
                    autoComplete="off"
                    autoFocus
                    required
                  />
                </label>
                <button
                  className="danger-button"
                  type="submit"
                  disabled={operation === "cleanup" || cleanupDialog.confirmedUsername !== account.githubUsername}
                >
                  <Trash2 size={16} />
                  确认并删除 GitHub 账号
                </button>
              </form>
            ) : (
              <div className="cleanup-progress">
                <strong>{cleanupDialog.session.manualIntervention?.title ?? "账号清理状态"}</strong>
                <p>{cleanupDialog.session.manualIntervention?.instruction ?? cleanupDialog.session.errorMessage ?? "正在处理"}</p>
                {!["done", "cancelled"].includes(cleanupDialog.session.status) && (
                  <button
                    className="primary-button"
                    type="button"
                    onClick={() => void handleContinueCleanup()}
                    disabled={operation === "cleanup" || cleanupDialog.session.status === "local_cleanup"}
                  >
                    <RefreshCw size={16} className={operation === "cleanup" ? "spin" : ""} />
                    {cleanupDialog.session.status === "error" ? "重试账号清理" : "已完成验证，继续"}
                  </button>
                )}
              </div>
            )}
            {cleanupDialog.error && <div className="error-banner" role="alert">{cleanupDialog.error}</div>}
          </section>
        </div>
      )}
    </>
  );
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

function formatQuotaUpdatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "更新时间无效";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
