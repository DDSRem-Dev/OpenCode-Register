import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountRow } from "./AccountRow";
import type { AccountSummary } from "../services/api";

const account: AccountSummary = {
  uuid: "00000000-0000-4000-8000-000000000007",
  githubUsername: "quota-user",
  githubEmailMasked: "p***@example.test",
  opencodeProviderName: "opencode-go",
  opencodeWorkspaceId: "wrk_quota",
  status: "active",
  opencodeConfigured: true,
  omoConfigured: true,
  quotaTotal: 100,
  quotaUsed: 64,
  quotaUpdatedAt: "2026-07-26T02:00:00Z",
  quotaCheckedAt: "2026-07-26T02:00:00Z",
  quotaInvalidReason: null,
  createdAt: "2026-07-25T12:00:00Z",
  updatedAt: "2026-07-26T02:00:00Z",
  notes: null,
};

const manualCleanup = {
  account_id: account.uuid,
  github_username: account.githubUsername,
  status: "manual_required",
  manual_intervention: {
    reason: "captcha",
    title: "GitHub 需要人工验证",
    instruction: "请在可见浏览器中完成 CAPTCHA 或风险验证。",
  },
  promoted_account_id: null,
  error_code: null,
  error_message: null,
};

describe("AccountRow", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("refreshes quota and renders the safe result message", async () => {
    const onChanged = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(response({
      account_id: account.uuid,
      status: "updated",
      quota_total: 100,
      quota_used: 72,
      quota_updated_at: "2026-07-26T03:00:00Z",
      message: "OpenCode Go 月度用量已更新",
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AccountRow account={account} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole("button", { name: `刷新 ${account.githubUsername} 月度用量` }));

    expect(await screen.findByRole("status")).toHaveTextContent("OpenCode Go 月度用量已更新");
    expect(fetchMock).toHaveBeenCalledWith(
      `http://127.0.0.1:17891/api/accounts/${account.uuid}/quota/refresh`,
      expect.objectContaining({ method: "POST", signal: expect.any(AbortSignal) }),
    );
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("copies an API key without rendering the secret", async () => {
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");
    const fakeApiKey = `sk-${"c".repeat(64)}`;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      account_id: account.uuid,
      api_key: fakeApiKey,
    })));
    render(<AccountRow account={account} onChanged={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: `复制 ${account.githubUsername} API Key` }));

    expect(await screen.findByRole("status")).toHaveTextContent("API Key 已复制到剪贴板");
    expect(writeText).toHaveBeenCalledWith(fakeApiKey);
    expect(document.body).not.toHaveTextContent(fakeApiKey);

    await waitFor(() => expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), 4000));
    const dismissMessage = setTimeoutSpy.mock.calls.find(([, delay]) => delay === 4000)?.[0];
    act(() => {
      if (typeof dismissMessage === "function") dismissMessage();
    });
    expect(screen.queryByText("API Key 已复制到剪贴板")).not.toBeInTheDocument();
  });

  it("provides visible tooltip labels for every account action", () => {
    render(<AccountRow account={account} onChanged={vi.fn()} />);

    expect(screen.getByRole("button", { name: `复制 ${account.githubUsername} API Key` }))
      .toHaveAttribute("data-tooltip", "复制 API Key");
    expect(screen.getByRole("button", { name: `刷新 ${account.githubUsername} 月度用量` }))
      .toHaveAttribute("data-tooltip", "刷新月度用量");
    expect(screen.getByRole("button", { name: `标记 ${account.githubUsername} 额度已用尽` }))
      .toHaveAttribute("data-tooltip", "标记额度已用尽");
    expect(screen.getByRole("button", { name: `删除 ${account.githubUsername}` }))
      .toHaveAttribute("data-tooltip", "删除账号");
  });

  it("shows which local configuration still needs to be applied", () => {
    const { rerender } = render(
      <AccountRow account={{ ...account, opencodeConfigured: false, omoConfigured: false }} onChanged={vi.fn()} />,
    );

    expect(screen.getByText("OpenCode 配置待应用")).toBeInTheDocument();
    rerender(<AccountRow account={{ ...account, opencodeConfigured: true, omoConfigured: false }} onChanged={vi.fn()} />);
    expect(screen.getByText("Oh My OpenCode 配置待应用")).toBeInTheDocument();
  });

  it("shows the check date and reason for an invalid account without a monthly progress label", () => {
    render(<AccountRow
      account={{
        ...account,
        status: "invalid",
        quotaTotal: null,
        quotaUsed: null,
        quotaUpdatedAt: null,
        quotaCheckedAt: "2026-07-26T03:00:00Z",
        quotaInvalidReason: "subscription_required",
      }}
      onChanged={vi.fn()}
    />);

    expect(screen.getByText("检查日期")).toBeInTheDocument();
    expect(screen.getByText("OpenCode Go 未订阅或订阅已到期")).toBeInTheDocument();
    expect(screen.queryByText("月度用量")).not.toBeInTheDocument();
    expect(screen.queryByText("尚未检查")).not.toBeInTheDocument();
  });

  it("renders a pending account without enabling OpenCode quota actions", () => {
    const pendingAccount: AccountSummary = {
      ...account,
      opencodeProviderName: null,
      opencodeWorkspaceId: null,
      status: "pending_setup",
      quotaTotal: null,
      quotaUsed: null,
      quotaUpdatedAt: null,
    };

    render(<AccountRow account={pendingAccount} onChanged={vi.fn()} />);

    expect(screen.getByText("待完成配置")).toBeInTheDocument();
    expect(screen.getByText("尚未配置")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `刷新 ${account.githubUsername} 月度用量` })).toBeDisabled();
    expect(screen.getByRole("button", { name: `标记 ${account.githubUsername} 额度已用尽` })).toBeDisabled();
    expect(screen.getByRole("button", { name: `删除 ${account.githubUsername}` })).toBeEnabled();
  });

  it("shows a safe message when the background quota check is unavailable", async () => {
    const onChanged = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(response({
      account_id: account.uuid,
      status: "unavailable",
      quota_total: null,
      quota_used: null,
      quota_updated_at: null,
      message: "后台额度检查遇到安全验证，已停止且未更新额度",
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AccountRow account={account} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole("button", { name: `刷新 ${account.githubUsername} 月度用量` }));

    expect(await screen.findByRole("status")).toHaveTextContent("后台额度检查遇到安全验证");
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("requires the exact username before starting destructive cleanup", async () => {
    const onChanged = vi.fn();
    const fetchMock = vi.fn().mockResolvedValueOnce(response({
      ...manualCleanup,
      status: "done",
      manual_intervention: null,
      promoted_account_id: "next-account",
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AccountRow account={account} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole("button", { name: `删除 ${account.githubUsername}` }));
    const startButton = screen.getByRole("button", { name: "确认并删除 GitHub 账号" });
    const usernameInput = screen.getByLabelText("GitHub 用户名");
    expect(startButton).toBeDisabled();

    fireEvent.change(usernameInput, { target: { value: "different-user" } });
    expect(startButton).toBeDisabled();
    fireEvent.change(usernameInput, { target: { value: account.githubUsername } });
    fireEvent.click(startButton);

    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      `http://127.0.0.1:17891/api/accounts/${account.uuid}`,
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({ confirmed_username: account.githubUsername }),
      }),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("restarts an errored cleanup instead of confirming a terminal flow", async () => {
    const onChanged = vi.fn();
    const fakePassword = "Fake-GitHub-Password-Must-Not-Render!";
    const fakeApiKey = `sk-${"x".repeat(64)}`;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        ...manualCleanup,
        status: "error",
        manual_intervention: null,
        error_code: "account_config_cleanup_failed",
        error_message: "GitHub 已删除，但本地号池配置清理失败，可重试",
        github_password: fakePassword,
        opencode_api_key: fakeApiKey,
      }))
      .mockResolvedValueOnce(response({
        ...manualCleanup,
        status: "done",
        manual_intervention: null,
      }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AccountRow account={account} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole("button", { name: `删除 ${account.githubUsername}` }));
    fireEvent.change(screen.getByLabelText("GitHub 用户名"), { target: { value: account.githubUsername } });
    fireEvent.click(screen.getByRole("button", { name: "确认并删除 GitHub 账号" }));
    fireEvent.click(await screen.findByRole("button", { name: "重试账号清理" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe(`http://127.0.0.1:17891/api/accounts/${account.uuid}`);
    expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
    expect(document.body).not.toHaveTextContent(fakePassword);
    expect(document.body).not.toHaveTextContent(fakeApiKey);
  });

  it("cancels an active cleanup before closing the dialog", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(manualCleanup))
      .mockResolvedValueOnce(response({ ...manualCleanup, status: "cancelled", manual_intervention: null }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AccountRow account={account} onChanged={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: `删除 ${account.githubUsername}` }));
    fireEvent.change(screen.getByLabelText("GitHub 用户名"), { target: { value: account.githubUsername } });
    fireEvent.click(screen.getByRole("button", { name: "确认并删除 GitHub 账号" }));
    await screen.findByText("GitHub 需要人工验证");
    fireEvent.click(screen.getByRole("button", { name: "关闭删除账号对话框" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `http://127.0.0.1:17891/api/accounts/${account.uuid}/cleanup/cancel`,
      expect.objectContaining({ method: "POST" }),
    );
  });
});

function response(payload: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => payload };
}
