import { invoke } from "@tauri-apps/api/core";
import type {
  AccountCleanupSession,
  AccountCleanupStatus,
  AccountStatus,
  AccountSummary,
  AutomaticConfiguration,
  BackendProcessStatus,
  ConfigurationRepairResult,
  ErrorResponse,
  FlowEvent,
  FlowSession,
  FlowStatus,
  HealthResponse,
  ImportAccountsResult,
  ManualIntervention,
  QuotaRefreshResult,
  QuotaRefreshStatus,
  QuotaInvalidReason,
  VaultStatus,
} from "./contracts";

export type * from "./contracts";

export const BACKEND_URL = "http://127.0.0.1:17891";
const WEBSOCKET_URL = "ws://127.0.0.1:17891";

export function isTauriRuntime(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

export async function startBackend(): Promise<BackendProcessStatus> {
  if (!isTauriRuntime()) return { running: false, pid: null };
  return invoke<BackendProcessStatus>("start_backend");
}

export async function backendProcessStatus(): Promise<BackendProcessStatus> {
  if (!isTauriRuntime()) return { running: false, pid: null };
  return invoke<BackendProcessStatus>("backend_status");
}

export async function stopBackend(): Promise<void> {
  if (isTauriRuntime()) await invoke("stop_backend");
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${BACKEND_URL}/api/health`, { signal });
  if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
  return response.json() as Promise<HealthResponse>;
}

export async function fetchVaultStatus(signal?: AbortSignal): Promise<VaultStatus> {
  const payload = await requestJson("/api/vault", { signal });
  if (!isRecord(payload) || typeof payload.unlocked !== "boolean" || typeof payload.initialized !== "boolean") {
    throw new Error("本地服务返回了无效账号库状态");
  }
  return { unlocked: payload.unlocked, initialized: payload.initialized };
}

export async function unlockVault(
  masterPassword: string,
  masterPasswordConfirmation?: string,
  signal?: AbortSignal,
): Promise<VaultStatus> {
  const payload = await requestJson("/api/vault/unlock", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      master_password: masterPassword,
      ...(masterPasswordConfirmation ? { master_password_confirmation: masterPasswordConfirmation } : {}),
    }),
    signal,
  });
  if (!isRecord(payload) || payload.unlocked !== true) {
    throw new Error("本地服务未能确认账号库已解锁");
  }
  return { unlocked: true, initialized: true };
}

export async function fetchAccounts(signal?: AbortSignal): Promise<AccountSummary[]> {
  const payload = await requestJson("/api/accounts", { signal });
  if (!isRecord(payload) || !Array.isArray(payload.accounts)) {
    throw new Error("本地服务返回了无效账号列表");
  }
  return payload.accounts.map(parseAccountSummary);
}

export async function fetchAutomaticConfiguration(signal?: AbortSignal): Promise<AutomaticConfiguration> {
  return parseAutomaticConfiguration(await requestJson("/api/settings", { signal }));
}

export async function updateAutomaticConfiguration(
  autoConfigureOpencode: boolean,
  autoConfigureOmo: boolean,
  signal?: AbortSignal,
): Promise<AutomaticConfiguration> {
  return parseAutomaticConfiguration(await requestJson("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      auto_configure_opencode: autoConfigureOpencode,
      auto_configure_omo: autoConfigureOmo,
    }),
    signal,
  }));
}

export async function applyAutomaticConfiguration(signal?: AbortSignal): Promise<AutomaticConfiguration> {
  return parseAutomaticConfiguration(await requestJson("/api/settings/apply", { method: "POST", signal }));
}

export async function repairConfiguration(signal?: AbortSignal): Promise<ConfigurationRepairResult> {
  return parseConfigurationRepairResult(await requestJson("/api/settings/repair", { method: "POST", signal }));
}

export async function copyAccountApiKey(accountId: string, signal?: AbortSignal): Promise<void> {
  const payload = await requestJson(`/api/accounts/${encodeURIComponent(accountId)}/api-key`, {
    cache: "no-store",
    signal,
  });
  if (
    !isRecord(payload)
    || payload.account_id !== accountId
    || typeof payload.api_key !== "string"
    || !/^sk-[A-Za-z0-9]{64}$/.test(payload.api_key)
  ) {
    throw new Error("本地服务返回了无效 API Key");
  }
  if (!navigator.clipboard?.writeText) throw new Error("系统剪贴板当前不可用");
  await navigator.clipboard.writeText(payload.api_key);
}

export async function refreshAccountQuota(accountId: string, signal?: AbortSignal): Promise<QuotaRefreshResult> {
  const payload = await requestJson(`/api/accounts/${encodeURIComponent(accountId)}/quota/refresh`, {
    method: "POST",
    signal,
  });
  return parseQuotaRefreshResult(payload);
}

export async function refreshAllQuotas(signal?: AbortSignal): Promise<QuotaRefreshResult[]> {
  const payload = await requestJson("/api/quota/refresh", { method: "POST", signal });
  if (!isRecord(payload) || !Array.isArray(payload.results)) {
    throw new Error("本地服务返回了无效额度刷新结果");
  }
  return payload.results.map(parseQuotaRefreshResult);
}

export async function markAccountExhausted(accountId: string, signal?: AbortSignal): Promise<void> {
  const payload = await requestJson(`/api/accounts/${encodeURIComponent(accountId)}/mark-exhausted`, {
    method: "POST",
    signal,
  });
  if (!isRecord(payload) || payload.account_id !== accountId || payload.status !== "exhausted") {
    throw new Error("本地服务未能确认账号已标记为耗尽");
  }
}

export async function startAccountCleanup(
  accountId: string,
  confirmedUsername: string,
  signal?: AbortSignal,
): Promise<AccountCleanupSession> {
  const payload = await requestJson(`/api/accounts/${encodeURIComponent(accountId)}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed_username: confirmedUsername }),
    signal,
  });
  return parseAccountCleanupSession(payload);
}

export async function confirmAccountCleanup(
  accountId: string,
  signal?: AbortSignal,
): Promise<AccountCleanupSession> {
  const payload = await requestJson(`/api/accounts/${encodeURIComponent(accountId)}/cleanup/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed: true }),
    signal,
  });
  return parseAccountCleanupSession(payload);
}

export async function cancelAccountCleanup(
  accountId: string,
  signal?: AbortSignal,
): Promise<AccountCleanupSession> {
  const payload = await requestJson(`/api/accounts/${encodeURIComponent(accountId)}/cleanup/cancel`, {
    method: "POST",
    signal,
  });
  return parseAccountCleanupSession(payload);
}

export async function exportAccounts(bundlePassword: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`${BACKEND_URL}/api/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bundle_password: bundlePassword }),
    signal,
  });
  if (!response.ok) {
    const payload: unknown = await response.json();
    throw new Error(parseErrorMessage(payload, response.status));
  }
  return response.blob();
}

export async function importAccounts(
  bundle: ArrayBuffer,
  bundlePassword: string,
  signal?: AbortSignal,
): Promise<ImportAccountsResult> {
  const payload = await requestJson("/api/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      bundle_password: bundlePassword,
      bundle_base64: bytesToBase64(new Uint8Array(bundle)),
    }),
    signal,
  });
  if (!isRecord(payload) || typeof payload.imported_count !== "number") {
    throw new Error("本地服务返回了无效导入结果");
  }
  return { importedCount: payload.imported_count };
}

export async function startAccountFlow(signal?: AbortSignal): Promise<FlowSession> {
  return requestFlow("/api/accounts", {
    method: "POST",
    signal,
  });
}

export async function fetchFlow(flowId: string, signal?: AbortSignal): Promise<FlowSession> {
  return requestFlow(`/api/flow/${encodeURIComponent(flowId)}`, { signal });
}

export function flowScreenshotUrl(flowId: string, screenshotId: string): string {
  return `${BACKEND_URL}/api/flow/${encodeURIComponent(flowId)}/screenshot/${encodeURIComponent(screenshotId)}`;
}

export async function resumeFlow(
  flowId: string,
  apiKey?: string,
  signal?: AbortSignal,
): Promise<FlowSession> {
  return requestFlow(`/api/flow/${encodeURIComponent(flowId)}/manual-input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed: true, ...(apiKey ? { api_key: apiKey } : {}) }),
    signal,
  });
}

export async function cancelFlow(flowId: string, signal?: AbortSignal): Promise<FlowSession> {
  return requestFlow(`/api/flow/${encodeURIComponent(flowId)}/cancel`, { method: "POST", signal });
}

export async function pauseFlow(flowId: string, signal?: AbortSignal): Promise<FlowSession> {
  return requestFlow(`/api/flow/${encodeURIComponent(flowId)}/pause`, { method: "POST", signal });
}

export function subscribeFlow(
  flowId: string,
  onSession: (session: FlowSession) => void,
  onError: (message: string) => void,
): () => void {
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let isClosed = false;

  const connect = () => {
    try {
      socket = new WebSocket(`${WEBSOCKET_URL}/ws/flow/${encodeURIComponent(flowId)}`);
    } catch {
      onError("无法连接流程事件");
      if (!isClosed) reconnectTimer = window.setTimeout(connect, 1000);
      return;
    }
    socket.onmessage = (message: MessageEvent<unknown>) => {
      try {
        if (typeof message.data !== "string") throw new Error("本地服务返回了非文本流程事件");
        const event = parseFlowEvent(JSON.parse(message.data) as unknown);
        onSession(event.payload);
      } catch (reason) {
        onError(reason instanceof Error ? reason.message : "无法解析流程事件");
      }
    };
    socket.onerror = () => socket?.close();
    socket.onclose = () => {
      if (!isClosed) reconnectTimer = window.setTimeout(connect, 1000);
    };
  };

  connect();
  return () => {
    isClosed = true;
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    socket?.close();
  };
}

async function requestFlow(path: string, init?: RequestInit): Promise<FlowSession> {
  return parseFlowSession(await requestJson(path, init));
}

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${BACKEND_URL}${path}`, init);
  const payload: unknown = await response.json();
  if (!response.ok) throw new Error(parseErrorMessage(payload, response.status));
  return payload;
}

function parseAccountSummary(payload: unknown): AccountSummary {
  if (
    !isRecord(payload)
    || typeof payload.uuid !== "string"
    || typeof payload.github_username !== "string"
    || typeof payload.github_email_masked !== "string"
    || (payload.opencode_provider_name !== null && typeof payload.opencode_provider_name !== "string")
    || (payload.opencode_workspace_id !== null && typeof payload.opencode_workspace_id !== "string")
    || !isAccountStatus(payload.status)
    || typeof payload.opencode_configured !== "boolean"
    || typeof payload.omo_configured !== "boolean"
    || typeof payload.created_at !== "string"
    || typeof payload.updated_at !== "string"
  ) {
    throw new Error("本地服务返回了无效账号条目");
  }
  return {
    uuid: payload.uuid,
    githubUsername: payload.github_username,
    githubEmailMasked: payload.github_email_masked,
    opencodeProviderName: optionalString(payload.opencode_provider_name),
    opencodeWorkspaceId: optionalString(payload.opencode_workspace_id),
    status: payload.status,
    opencodeConfigured: payload.opencode_configured,
    omoConfigured: payload.omo_configured,
    quotaTotal: optionalNumber(payload.quota_total),
    quotaUsed: optionalNumber(payload.quota_used),
    quotaUpdatedAt: optionalString(payload.quota_updated_at),
    quotaCheckedAt: optionalString(payload.quota_checked_at),
    quotaInvalidReason: parseQuotaInvalidReason(payload.quota_invalid_reason),
    createdAt: payload.created_at,
    updatedAt: payload.updated_at,
    notes: optionalString(payload.notes),
  };
}

function parseAutomaticConfiguration(payload: unknown): AutomaticConfiguration {
  if (
    !isRecord(payload)
    || typeof payload.auto_configure_opencode !== "boolean"
    || typeof payload.auto_configure_omo !== "boolean"
    || !isNonNegativeInteger(payload.opencode_pending_count)
    || !isNonNegativeInteger(payload.omo_pending_count)
    || !isNonNegativeInteger(payload.applied_count)
  ) {
    throw new Error("本地服务返回了无效自动配置设置");
  }
  return {
    autoConfigureOpencode: payload.auto_configure_opencode,
    autoConfigureOmo: payload.auto_configure_omo,
    opencodePendingCount: payload.opencode_pending_count,
    omoPendingCount: payload.omo_pending_count,
    appliedCount: payload.applied_count,
  };
}

function parseConfigurationRepairResult(payload: unknown): ConfigurationRepairResult {
  if (
    !isRecord(payload)
    || !isNonNegativeInteger(payload.updated_target_count)
    || !isNonNegativeInteger(payload.added_fallback_count)
    || !isNonNegativeInteger(payload.removed_fallback_count)
  ) {
    throw new Error("本地服务返回了无效配置修复结果");
  }
  return {
    updatedTargetCount: payload.updated_target_count,
    addedFallbackCount: payload.added_fallback_count,
    removedFallbackCount: payload.removed_fallback_count,
  };
}

function parseQuotaRefreshResult(payload: unknown): QuotaRefreshResult {
  if (
    !isRecord(payload)
    || typeof payload.account_id !== "string"
    || !isQuotaRefreshStatus(payload.status)
    || typeof payload.message !== "string"
  ) {
    throw new Error("本地服务返回了无效额度刷新结果");
  }
  return {
    accountId: payload.account_id,
    status: payload.status,
    quotaTotal: optionalNumber(payload.quota_total),
    quotaUsed: optionalNumber(payload.quota_used),
    quotaUpdatedAt: optionalString(payload.quota_updated_at),
    message: payload.message,
  };
}

function parseAccountCleanupSession(payload: unknown): AccountCleanupSession {
  if (
    !isRecord(payload)
    || typeof payload.account_id !== "string"
    || typeof payload.github_username !== "string"
    || !isAccountCleanupStatus(payload.status)
  ) {
    throw new Error("本地服务返回了无效账号清理状态");
  }
  return {
    accountId: payload.account_id,
    githubUsername: payload.github_username,
    status: payload.status,
    manualIntervention: parseManualIntervention(payload.manual_intervention),
    promotedAccountId: optionalString(payload.promoted_account_id),
    errorCode: optionalString(payload.error_code),
    errorMessage: optionalString(payload.error_message),
  };
}

function parseFlowSession(payload: unknown): FlowSession {
  if (!isRecord(payload) || typeof payload.flow_id !== "string" || !isFlowStatus(payload.status)) {
    throw new Error("本地服务返回了无效流程数据");
  }
  return {
    flow_id: payload.flow_id,
    status: payload.status,
    email_provider: optionalString(payload.email_provider),
    temp_email: optionalString(payload.temp_email),
    github_username: optionalString(payload.github_username),
    account_id: optionalString(payload.account_id),
    opencode_workspace_id: optionalString(payload.opencode_workspace_id),
    opencode_provider_name: optionalString(payload.opencode_provider_name),
    api_key_captured: requiredBoolean(payload.api_key_captured),
    manual_intervention: parseManualIntervention(payload.manual_intervention),
    screenshot_id: optionalString(payload.screenshot_id),
    pause_requested: requiredBoolean(payload.pause_requested),
    error_code: optionalString(payload.error_code),
    error_message: optionalString(payload.error_message),
  };
}

function parseFlowEvent(payload: unknown): FlowEvent {
  if (
    !isRecord(payload)
    || !isFlowEventName(payload.event)
    || payload.version !== 1
    || typeof payload.timestamp !== "string"
    || typeof payload.flow_id !== "string"
  ) {
    throw new Error("本地服务返回了无效流程事件");
  }
  const session = parseFlowSession(payload.payload);
  if (session.flow_id !== payload.flow_id) throw new Error("流程事件标识不一致");
  return {
    event: payload.event,
    version: 1,
    timestamp: payload.timestamp,
    flow_id: payload.flow_id,
    payload: session,
  };
}

function parseManualIntervention(payload: unknown): ManualIntervention | null {
  if (payload === null || payload === undefined) return null;
  if (
    !isRecord(payload)
    || !isManualReason(payload.reason)
    || typeof payload.title !== "string"
    || typeof payload.instruction !== "string"
  ) {
    throw new Error("本地服务返回了无效人工介入请求");
  }
  return { reason: payload.reason, title: payload.title, instruction: payload.instruction };
}

function parseErrorMessage(payload: unknown, statusCode: number): string {
  if (isErrorResponse(payload)) return payload.message;
  return `本地服务请求失败（HTTP ${statusCode}）`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function optionalString(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") throw new Error("本地服务返回了无效流程字段");
  return value;
}

function requiredBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") throw new Error("本地服务返回了无效流程字段");
  return value;
}

function optionalNumber(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error("本地服务返回了无效账号字段");
  }
  return value;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isAccountStatus(value: unknown): value is AccountStatus {
  return ["active", "exhausted", "invalid", "pending_setup", "pending_payment", "cancelled"].includes(
    typeof value === "string" ? value : "",
  );
}

function isQuotaRefreshStatus(value: unknown): value is QuotaRefreshStatus {
  return ["updated", "exhausted", "invalid", "unavailable"].includes(
    typeof value === "string" ? value : "",
  );
}

function parseQuotaInvalidReason(value: unknown): QuotaInvalidReason | null {
  if (value === null || value === undefined) return null;
  if (isQuotaInvalidReason(value)) return value;
  throw new Error("本地服务返回了无效额度失效原因");
}

function isQuotaInvalidReason(value: unknown): value is QuotaInvalidReason {
  return ["github_credentials_invalid", "subscription_required", "unknown"].includes(
    typeof value === "string" ? value : "",
  );
}

function isAccountCleanupStatus(value: unknown): value is AccountCleanupStatus {
  return ["starting", "manual_required", "local_cleanup", "done", "error", "cancelled"].includes(
    typeof value === "string" ? value : "",
  );
}

function isFlowStatus(value: unknown): value is FlowStatus {
  return [
    "idle",
    "creating_email",
    "github_register",
    "manual_verify",
    "github_email_verify",
    "opencode_login",
    "pending_payment",
    "fetch_api_key",
    "done",
    "error",
    "cancelled",
  ].includes(typeof value === "string" ? value : "");
}

function isManualReason(value: unknown): value is ManualIntervention["reason"] {
  return [
    "captcha",
    "phone_verification",
    "unknown_block",
    "timeout",
    "user_paused",
    "payment",
    "api_key_input",
  ].includes(
    typeof value === "string" ? value : "",
  );
}

function isFlowEventName(value: unknown): value is FlowEvent["event"] {
  return [
    "flow_snapshot",
    "manual_intervention_required",
    "flow_completed",
    "flow_failed",
    "flow_cancelled",
  ].includes(typeof value === "string" ? value : "");
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  return isRecord(value) && typeof value.code === "string" && typeof value.message === "string";
}

function bytesToBase64(bytes: Uint8Array): string {
  const chunkSize = 8192;
  let binary = "";
  for (let start = 0; start < bytes.length; start += chunkSize) {
    let chunk = "";
    const end = Math.min(start + chunkSize, bytes.length);
    for (let index = start; index < end; index += 1) {
      chunk += String.fromCharCode(bytes[index]);
    }
    binary += chunk;
  }
  return window.btoa(binary);
}
