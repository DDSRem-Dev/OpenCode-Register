import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CreateFlow } from "./CreateFlow";

const serviceMocks = vi.hoisted(() => ({
  startAccountFlow: vi.fn(),
  fetchFlow: vi.fn(),
  resumeFlow: vi.fn(),
  pauseFlow: vi.fn(),
  cancelFlow: vi.fn(),
  subscribeFlow: vi.fn(() => vi.fn()),
  flowScreenshotUrl: vi.fn((flowId: string, screenshotId: string) => (
    `http://127.0.0.1:17891/api/flow/${flowId}/screenshot/${screenshotId}`
  )),
}));

vi.mock("../services/api", () => serviceMocks);

const manualSession = {
  flow_id: "flow-test-1",
  status: "manual_verify" as const,
  email_provider: "duckmail",
  temp_email: "flow@example.test",
  github_username: "learner-test",
  account_id: null,
  opencode_workspace_id: null,
  api_key_captured: false,
  manual_intervention: {
    reason: "captcha" as const,
    title: "需要完成安全验证",
    instruction: "请在浏览器中完成验证。",
  },
  screenshot_id: null,
  pause_requested: false,
  error_code: null,
  error_message: null,
};

const completedSession = {
  ...manualSession,
  status: "done" as const,
  opencode_workspace_id: "wrk_test123",
  api_key_captured: true,
  manual_intervention: null,
};

describe("CreateFlow", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    serviceMocks.subscribeFlow.mockReturnValue(vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("resumes after the user completes manual verification", async () => {
    const onAccountCreated = vi.fn();
    serviceMocks.startAccountFlow.mockResolvedValue(manualSession);
    serviceMocks.resumeFlow.mockResolvedValue(completedSession);
    render(<CreateFlow isBackendConnected isVaultUnlocked onAccountCreated={onAccountCreated} />);

    fireEvent.click(screen.getByRole("button", { name: "新建账号" }));
    expect(await screen.findByRole("heading", { name: "需要完成安全验证" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "已完成，继续" }));
    expect(within(await screen.findByRole("status")).getByText("账号创建完成")).toBeInTheDocument();
    expect(serviceMocks.resumeFlow).toHaveBeenCalledWith("flow-test-1", undefined);
    expect(onAccountCreated).toHaveBeenCalledOnce();
  });

  it("manually refreshes a completed flow from the backend", async () => {
    serviceMocks.startAccountFlow.mockResolvedValue(completedSession);
    serviceMocks.fetchFlow.mockResolvedValue({ ...completedSession, opencode_provider_name: "opencode-go-refreshed" });
    render(<CreateFlow isBackendConnected isVaultUnlocked />);

    fireEvent.click(screen.getByRole("button", { name: "新建账号" }));
    fireEvent.click(await screen.findByRole("button", { name: "刷新流程状态" }));

    expect(await screen.findByText("OpenCode Provider：opencode-go-refreshed")).toBeInTheDocument();
    expect(serviceMocks.fetchFlow).toHaveBeenCalledWith("flow-test-1", expect.any(AbortSignal));
  });

  it("renders the backend-owned sanitized screenshot for manual intervention", async () => {
    serviceMocks.startAccountFlow.mockResolvedValue({
      ...manualSession,
      screenshot_id: "00000000-0000-4000-8000-000000000092",
    });
    render(<CreateFlow isBackendConnected isVaultUnlocked />);

    fireEvent.click(screen.getByRole("button", { name: "新建账号" }));

    expect(await screen.findByRole("img", { name: "当前浏览器页面" })).toHaveAttribute(
      "src",
      "http://127.0.0.1:17891/api/flow/flow-test-1/screenshot/00000000-0000-4000-8000-000000000092",
    );
  });

  it("confirms OpenCode Go payment with an explicit user action", async () => {
    const paymentSession = {
      ...manualSession,
      status: "pending_payment" as const,
      opencode_workspace_id: "wrk_test123",
      manual_intervention: {
        reason: "payment" as const,
        title: "等待手动支付",
        instruction: "请在 OpenCode Go 页面完成支付。",
      },
    };
    serviceMocks.startAccountFlow.mockResolvedValue(paymentSession);
    serviceMocks.resumeFlow.mockResolvedValue(completedSession);
    render(<CreateFlow isBackendConnected isVaultUnlocked />);

    fireEvent.click(screen.getByRole("button", { name: "新建账号" }));
    fireEvent.click(await screen.findByRole("button", { name: "已支付，继续" }));

    await waitFor(() => expect(serviceMocks.resumeFlow).toHaveBeenCalledWith("flow-test-1", undefined));
  });

  it("submits a manually copied API key without rendering it after completion", async () => {
    const apiKey = `sk-${"x".repeat(64)}`;
    const apiKeySession = {
      ...manualSession,
      manual_intervention: {
        reason: "api_key_input" as const,
        title: "需要手动复制 API Key",
        instruction: "请提交 Default API Key。",
      },
    };
    serviceMocks.startAccountFlow.mockResolvedValue(apiKeySession);
    serviceMocks.resumeFlow.mockResolvedValue(completedSession);
    render(<CreateFlow isBackendConnected isVaultUnlocked />);

    fireEvent.click(screen.getByRole("button", { name: "新建账号" }));
    fireEvent.change(await screen.findByLabelText("Default API Key"), {
      target: { value: apiKey },
    });
    fireEvent.click(screen.getByRole("button", { name: "已完成，继续" }));

    await waitFor(() => expect(serviceMocks.resumeFlow).toHaveBeenCalledWith("flow-test-1", apiKey));
    expect(screen.queryByDisplayValue(apiKey)).not.toBeInTheDocument();
  });

  it("cancels a flow from the manual intervention panel", async () => {
    serviceMocks.startAccountFlow.mockResolvedValue(manualSession);
    serviceMocks.cancelFlow.mockResolvedValue({ ...manualSession, status: "cancelled", manual_intervention: null });
    render(<CreateFlow isBackendConnected isVaultUnlocked />);

    fireEvent.click(screen.getByRole("button", { name: "新建账号" }));
    fireEvent.click(await screen.findByRole("button", { name: "中止流程" }));

    await waitFor(() => expect(serviceMocks.cancelFlow).toHaveBeenCalledWith("flow-test-1"));
    expect(await screen.findByText("流程已中止")).toBeInTheDocument();
  });

  it("shows an actionable error when starting fails", async () => {
    serviceMocks.startAccountFlow.mockRejectedValue(new Error("邮箱 provider 配置无效"));
    render(<CreateFlow isBackendConnected isVaultUnlocked />);

    fireEvent.click(screen.getByRole("button", { name: "新建账号" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("邮箱 provider 配置无效");
  });

  it("requests a safe pause while a flow is running", async () => {
    const runningSession = {
      ...manualSession,
      status: "github_register" as const,
      manual_intervention: null,
    };
    const pausedSession = {
      ...manualSession,
      manual_intervention: {
        reason: "user_paused" as const,
        title: "流程已暂停",
        instruction: "流程已在安全步骤暂停，可以继续或中止。",
      },
    };
    serviceMocks.startAccountFlow.mockResolvedValue(runningSession);
    serviceMocks.pauseFlow.mockResolvedValue(pausedSession);
    render(<CreateFlow isBackendConnected isVaultUnlocked />);

    fireEvent.click(screen.getByRole("button", { name: "新建账号" }));
    fireEvent.click(await screen.findByRole("button", { name: "暂停流程" }));

    await waitFor(() => expect(serviceMocks.pauseFlow).toHaveBeenCalledWith("flow-test-1"));
    expect(await screen.findByRole("heading", { name: "流程已暂停" })).toBeInTheDocument();
  });

  it("does not let an older poll overwrite a completed pause request", async () => {
    const runningSession = {
      ...manualSession,
      status: "github_register" as const,
      manual_intervention: null,
    };
    const pausedSession = {
      ...manualSession,
      manual_intervention: {
        reason: "user_paused" as const,
        title: "流程已暂停",
        instruction: "流程已在安全步骤暂停，可以继续或中止。",
      },
    };
    let resolvePoll: (value: typeof runningSession) => void = () => undefined;
    let runPoll: () => void = () => undefined;
    serviceMocks.startAccountFlow.mockResolvedValue(runningSession);
    serviceMocks.fetchFlow.mockReturnValue(new Promise((resolve) => {
      resolvePoll = resolve;
    }));
    serviceMocks.pauseFlow.mockResolvedValue(pausedSession);
    vi.spyOn(window, "setInterval").mockImplementation((handler) => {
      runPoll = handler as () => void;
      return 1;
    });
    render(<CreateFlow isBackendConnected isVaultUnlocked />);

    fireEvent.click(screen.getByRole("button", { name: "新建账号" }));
    await screen.findByRole("button", { name: "暂停流程" });
    runPoll();
    await waitFor(() => expect(serviceMocks.fetchFlow).toHaveBeenCalledWith("flow-test-1", expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole("button", { name: "暂停流程" }));
    expect(await screen.findByRole("heading", { name: "流程已暂停" })).toBeInTheDocument();

    resolvePoll(runningSession);
    await waitFor(() => expect(screen.getByRole("heading", { name: "流程已暂停" })).toBeInTheDocument());
  });

  it("disables account creation while the vault is locked", () => {
    render(<CreateFlow isBackendConnected isVaultUnlocked={false} />);

    expect(screen.getByRole("button", { name: "新建账号" })).toBeDisabled();
    expect(screen.getByText("请先解锁本地账号库")).toBeInTheDocument();
    expect(serviceMocks.startAccountFlow).not.toHaveBeenCalled();
  });
});
