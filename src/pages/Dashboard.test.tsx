import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "./Dashboard";

const accountPayload = {
  accounts: [
    {
      uuid: "00000000-0000-4000-8000-000000000006",
      github_username: "account-user",
      github_email_masked: "p***@example.test",
      opencode_provider_name: "opencode-go",
      opencode_workspace_id: "wrk_account",
      status: "active",
      opencode_configured: true,
      omo_configured: true,
      quota_total: null,
      quota_used: null,
      quota_updated_at: null,
      created_at: "2026-07-25T12:00:00Z",
      updated_at: "2026-07-25T12:00:00Z",
      notes: null,
    },
  ],
};

describe("Dashboard", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("unlocks the vault and renders masked account summaries", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ unlocked: false, initialized: true }))
      .mockResolvedValueOnce(response({ unlocked: true, initialized: true }))
      .mockResolvedValueOnce(response(accountPayload));
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard isBackendConnected onVaultStatusChange={vi.fn()} />);

    const passwordInput = await screen.findByLabelText("主密码");
    fireEvent.change(passwordInput, { target: { value: "account vault master password" } });
    fireEvent.click(screen.getByRole("button", { name: "解锁" }));

    expect(await screen.findByText("account-user")).toBeInTheDocument();
    expect(screen.getByText("p***@example.test")).toBeInTheDocument();
    expect(screen.queryByText("account@example.test")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:17891/api/vault/unlock",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ master_password: "account vault master password" }),
      }),
    );
    expect(screen.queryByLabelText("主密码")).not.toBeInTheDocument();
  });

  it("renders an empty state after an unlocked vault loads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(response({ unlocked: true, initialized: true }))
        .mockResolvedValueOnce(response({ accounts: [] })),
    );
    render(<Dashboard isBackendConnected onVaultStatusChange={vi.fn()} />);

    expect(await screen.findByText("尚未保存任何账号")).toBeInTheDocument();
  });

  it("does not render a stale account response after the backend disconnects", async () => {
    let resolveAccounts: (value: ReturnType<typeof response>) => void = () => undefined;
    const pendingAccounts = new Promise<ReturnType<typeof response>>((resolve) => {
      resolveAccounts = resolve;
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ unlocked: true, initialized: true }))
      .mockReturnValueOnce(pendingAccounts);
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(<Dashboard isBackendConnected onVaultStatusChange={vi.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    rerender(<Dashboard isBackendConnected={false} onVaultStatusChange={vi.fn()} />);
    resolveAccounts(response(accountPayload));

    expect(await screen.findByText("等待本地服务")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("account-user")).not.toBeInTheDocument());
  });

  it("ignores unknown quotas when every checked account is near its limit", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(response({ unlocked: true, initialized: true }))
        .mockResolvedValueOnce(response({
          accounts: [
            {
              ...accountPayload.accounts[0],
              quota_total: 100,
              quota_used: 86,
              quota_updated_at: "2026-07-26T02:30:00Z",
            },
            {
              ...accountPayload.accounts[0],
              uuid: "00000000-0000-4000-8000-000000000007",
              github_username: "quota-unchecked",
              opencode_provider_name: "opencode-go2",
            },
          ],
        })),
    );
    render(<Dashboard isBackendConnected onVaultStatusChange={vi.fn()} />);

    expect(await screen.findByText(/所有已检查账号月度用量均达到 80%/)).toBeInTheDocument();
  });

  it("requires confirmation when setting a master password for a new vault", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ unlocked: false, initialized: false }))
      .mockResolvedValueOnce(response({ unlocked: true, initialized: true }))
      .mockResolvedValueOnce(response({ accounts: [] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard isBackendConnected onVaultStatusChange={vi.fn()} />);

    fireEvent.change(await screen.findByLabelText("主密码"), {
      target: { value: "new account vault master password" },
    });
    fireEvent.change(screen.getByLabelText("确认主密码"), {
      target: { value: "new account vault master password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "解锁" }));

    expect(await screen.findByText("尚未保存任何账号")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:17891/api/vault/unlock",
      expect.objectContaining({
        body: JSON.stringify({
          master_password: "new account vault master password",
          master_password_confirmation: "new account vault master password",
        }),
      }),
    );
  });

  it("renders a retryable error when account loading fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(response({ unlocked: true, initialized: true }))
        .mockResolvedValueOnce(response({ code: "storage_failed", message: "账号库暂时不可用" }, false, 500)),
    );
    render(<Dashboard isBackendConnected onVaultStatusChange={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("账号库暂时不可用");
    expect(screen.getByRole("button", { name: "刷新账号列表" })).toBeEnabled();
  });

  it("keeps the unlock form available after a wrong master password", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(response({ unlocked: false, initialized: true }))
        .mockResolvedValueOnce(response({ code: "invalid_master_password", message: "主密码不正确" }, false, 401)),
    );
    const onVaultStatusChange = vi.fn();
    render(<Dashboard isBackendConnected onVaultStatusChange={onVaultStatusChange} />);

    fireEvent.change(await screen.findByLabelText("主密码"), {
      target: { value: "wrong account vault master password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "解锁" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("主密码不正确");
    expect(screen.getByLabelText("主密码")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("主密码"), {
      target: { value: "retry account vault master password" },
    });
    expect(screen.getByRole("button", { name: "解锁" })).toBeEnabled();
    expect(onVaultStatusChange).toHaveBeenLastCalledWith(false);
  });

  it("refreshes all quotas and reloads the account summaries", async () => {
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");
    const refreshedAccounts = {
      accounts: [{
        ...accountPayload.accounts[0],
        quota_total: 100,
        quota_used: 67,
        quota_updated_at: "2026-07-26T02:30:00Z",
      }],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ unlocked: true, initialized: true }))
      .mockResolvedValueOnce(response(accountPayload))
      .mockResolvedValueOnce(response({
        results: [{
          account_id: "00000000-0000-4000-8000-000000000006",
          status: "updated",
          quota_total: 100,
          quota_used: 67,
          quota_updated_at: "2026-07-26T02:30:00Z",
          message: "OpenCode Go 月度用量已更新",
        }],
      }))
      .mockResolvedValueOnce(response(refreshedAccounts));
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard isBackendConnected onVaultStatusChange={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "刷新全部月度用量" }));

    expect(await screen.findByText("67%")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("已刷新 1 个账号的月度用量");
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://127.0.0.1:17891/api/quota/refresh",
      expect.objectContaining({ method: "POST" }),
    );

    await waitFor(() => expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), 4000));
    const dismissMessage = setTimeoutSpy.mock.calls.find(([, delay]) => delay === 4000)?.[0];
    expect(typeof dismissMessage).toBe("function");
    act(() => {
      if (typeof dismissMessage === "function") dismissMessage();
    });
    expect(screen.queryByText("已刷新 1 个账号的月度用量")).not.toBeInTheDocument();
  });

  it("requires an exact username before automatic GitHub deletion", async () => {
    const cleanupSession = {
      account_id: "00000000-0000-4000-8000-000000000006",
      github_username: "account-user",
      status: "done",
      manual_intervention: null,
      promoted_account_id: null,
      error_code: null,
      error_message: null,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ unlocked: true, initialized: true }))
      .mockResolvedValueOnce(response(accountPayload))
      .mockResolvedValueOnce(response(cleanupSession))
      .mockResolvedValueOnce(response({ accounts: [] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<Dashboard isBackendConnected onVaultStatusChange={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "删除 account-user" }));
    const openCleanupButton = screen.getByRole("button", { name: "确认并删除 GitHub 账号" });
    expect(openCleanupButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText("GitHub 用户名"), { target: { value: "account-user" } });
    fireEvent.click(openCleanupButton);

    expect(await screen.findByText("尚未保存任何账号")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://127.0.0.1:17891/api/accounts/00000000-0000-4000-8000-000000000006",
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({ confirmed_username: "account-user" }),
      }),
    );
  });
});

function response(payload: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => payload };
}
