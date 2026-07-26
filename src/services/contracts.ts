export type HealthResponse = {
  status: "ok";
  service: string;
  version: string;
  storage_mode: "system" | "sandbox";
};

export type BackendProcessStatus = {
  running: boolean;
  pid: number | null;
};

export type VaultStatus = {
  unlocked: boolean;
  initialized: boolean;
};

export type AccountStatus = "active" | "exhausted" | "invalid" | "pending_setup" | "pending_payment" | "cancelled";
export type QuotaInvalidReason = "github_credentials_invalid" | "subscription_required" | "unknown";

export type AccountSummary = {
  uuid: string;
  githubUsername: string;
  githubEmailMasked: string;
  opencodeProviderName: string | null;
  opencodeWorkspaceId: string | null;
  status: AccountStatus;
  opencodeConfigured: boolean;
  omoConfigured: boolean;
  quotaTotal: number | null;
  quotaUsed: number | null;
  quotaUpdatedAt: string | null;
  quotaCheckedAt: string | null;
  quotaInvalidReason: QuotaInvalidReason | null;
  createdAt: string;
  updatedAt: string;
  notes: string | null;
};

export type ImportAccountsResult = {
  importedCount: number;
};

export type AutomaticConfiguration = {
  autoConfigureOpencode: boolean;
  autoConfigureOmo: boolean;
  opencodePendingCount: number;
  omoPendingCount: number;
  appliedCount: number;
};

export type ConfigurationRepairResult = {
  updatedTargetCount: number;
  addedFallbackCount: number;
  removedFallbackCount: number;
};

export type QuotaRefreshStatus = "updated" | "exhausted" | "invalid" | "unavailable";

export type QuotaRefreshResult = {
  accountId: string;
  status: QuotaRefreshStatus;
  quotaTotal: number | null;
  quotaUsed: number | null;
  quotaUpdatedAt: string | null;
  message: string;
};

export type AccountCleanupStatus =
  | "starting"
  | "manual_required"
  | "local_cleanup"
  | "done"
  | "error"
  | "cancelled";

export type AccountCleanupSession = {
  accountId: string;
  githubUsername: string;
  status: AccountCleanupStatus;
  manualIntervention: ManualIntervention | null;
  promotedAccountId: string | null;
  errorCode: string | null;
  errorMessage: string | null;
};

export type FlowStatus =
  | "idle"
  | "creating_email"
  | "github_register"
  | "manual_verify"
  | "github_email_verify"
  | "opencode_login"
  | "pending_payment"
  | "fetch_api_key"
  | "done"
  | "error"
  | "cancelled";

export type ManualIntervention = {
  reason:
    | "captcha"
    | "phone_verification"
    | "unknown_block"
    | "timeout"
    | "user_paused"
    | "payment"
    | "api_key_input";
  title: string;
  instruction: string;
};

export type FlowSession = {
  flow_id: string;
  status: FlowStatus;
  email_provider: string | null;
  temp_email: string | null;
  github_username: string | null;
  account_id: string | null;
  opencode_workspace_id: string | null;
  opencode_provider_name: string | null;
  api_key_captured: boolean;
  manual_intervention: ManualIntervention | null;
  screenshot_id: string | null;
  pause_requested: boolean;
  error_code: string | null;
  error_message: string | null;
};

export type FlowEvent = {
  event: "flow_snapshot" | "manual_intervention_required" | "flow_completed" | "flow_failed" | "flow_cancelled";
  version: 1;
  timestamp: string;
  flow_id: string;
  payload: FlowSession;
};

export type ErrorResponse = {
  code: string;
  message: string;
};
