import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyAutomaticConfiguration,
  configureBackendPort,
  copyAccountApiKey,
  exportAccounts,
  fetchAutomaticConfiguration,
  fetchFlow,
  fetchHealth,
  importAccounts,
  markAccountExhausted,
  refreshAccountQuota,
  refreshAllQuotas,
  repairConfiguration,
  resumeFlow,
  startAccountCleanup,
  startAccountFlow,
  subscribeFlow,
  updateAutomaticConfiguration,
} from "./api";

const manualSession = {
  flow_id: "flow-test-1",
  status: "manual_verify",
  email_provider: "temp_mail",
  temp_email: "flow@example.test",
  github_username: "river-notes42",
  account_id: null,
  opencode_workspace_id: null,
  api_key_captured: false,
  manual_intervention: {
    reason: "captcha",
    title: "需要完成安全验证",
    instruction: "请在浏览器中完成验证。",
  },
  screenshot_id: null,
  pause_requested: false,
  error_code: null,
  error_message: null,
};

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;

  constructor(public readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.onclose?.(new CloseEvent("close"));
  }

  emitMessage(payload: unknown): void {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }
}

class ThrowingWebSocket {
  constructor() {
    throw new Error("blocked by content security policy");
  }
}

describe("fetchHealth", () => {
  afterEach(() => {
    configureBackendPort(17891);
    vi.restoreAllMocks();
    FakeWebSocket.instances = [];
  });

  it("returns the backend health payload", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        service: "opencode-register-backend",
        version: "0.0.6",
        storage_mode: "sandbox",
      }),
    }));

    await expect(fetchHealth()).resolves.toMatchObject({
      status: "ok",
      version: "0.0.6",
      storage_mode: "sandbox",
    });
  });

  it("uses the configured sidecar port for HTTP and WebSocket calls", async () => {
    configureBackendPort(43123);
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => manualSession });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("WebSocket", FakeWebSocket);

    await startAccountFlow();
    const unsubscribe = subscribeFlow("flow-test-1", vi.fn(), vi.fn());

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:43123/api/accounts",
      expect.objectContaining({ method: "POST" }),
    );
    expect(FakeWebSocket.instances[0].url).toBe("ws://127.0.0.1:43123/ws/flow/flow-test-1");
    unsubscribe();
  });

  it("rejects an invalid sidecar port", () => {
    expect(() => configureBackendPort(0)).toThrow("无效端口");
    expect(() => configureBackendPort(65536)).toThrow("无效端口");
  });

  it("rejects non-success responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    await expect(fetchHealth()).rejects.toThrow("HTTP 503");
  });

  it("rejects an unrelated service on the selected local port", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "ok", service: "unrelated-local-service", version: "1.0.0" }),
    }));

    await expect(fetchHealth()).rejects.toThrow("无效健康状态");
  });

  it("starts an account flow with the default provider", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => manualSession });
    vi.stubGlobal("fetch", fetchMock);

    await expect(startAccountFlow()).resolves.toMatchObject({ status: "manual_verify" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:17891/api/accounts",
      expect.objectContaining({
        method: "POST",
      }),
    );
    expect(fetchMock.mock.calls[0][1]).not.toHaveProperty("body");
  });

  it("validates flow payloads from the local service", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "manual_verify" }) }));

    await expect(fetchFlow("flow-test-1")).rejects.toThrow("无效流程数据");
  });

  it("uses the stable API error message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ code: "flow_state_conflict", message: "当前流程状态不可恢复", details: null }),
    }));

    await expect(resumeFlow("flow-test-1")).rejects.toThrow("当前流程状态不可恢复");
  });

  it("sends a manually copied API key only in the resume request body", async () => {
    const apiKey = `sk-${"z".repeat(64)}`;
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => manualSession });
    vi.stubGlobal("fetch", fetchMock);

    await resumeFlow("flow-test-1", apiKey);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:17891/api/flow/flow-test-1/manual-input",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ confirmed: true, api_key: apiKey }),
      }),
    );
  });

  it("returns the encrypted export response as a blob", async () => {
    const bundle = new Blob(["encrypted-bundle"]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: async () => bundle }));

    await expect(exportAccounts("independent bundle password")).resolves.toBe(bundle);
  });

  it("encodes an imported bundle without changing its bytes", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ imported_count: 2 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      importAccounts(new Uint8Array([1, 2, 3]).buffer, "independent bundle password"),
    ).resolves.toEqual({ importedCount: 2 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:17891/api/import",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          bundle_password: "independent bundle password",
          bundle_base64: "AQID",
        }),
      }),
    );
  });

  it("parses single and bulk quota refresh results", async () => {
    const quotaResult = {
      account_id: "quota-account",
      status: "updated",
      quota_total: 100,
      quota_used: 54,
      quota_updated_at: "2026-07-26T02:30:00Z",
      message: "OpenCode Go 月度用量已更新",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => quotaResult })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ results: [quotaResult] }) });
    vi.stubGlobal("fetch", fetchMock);

    await expect(refreshAccountQuota("quota-account")).resolves.toMatchObject({ quotaUsed: 54 });
    await expect(refreshAllQuotas()).resolves.toEqual([
      expect.objectContaining({ accountId: "quota-account", status: "updated" }),
    ]);
  });

  it("reads, updates, and applies automatic configuration settings", async () => {
    const settingsPayload = {
      auto_configure_opencode: true,
      auto_configure_omo: false,
      opencode_pending_count: 2,
      omo_pending_count: 1,
      applied_count: 0,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => settingsPayload })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ...settingsPayload, auto_configure_omo: true }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ...settingsPayload, opencode_pending_count: 0, omo_pending_count: 0, applied_count: 2 }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ updated_target_count: 4, added_fallback_count: 4, removed_fallback_count: 1 }),
      });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchAutomaticConfiguration()).resolves.toMatchObject({ opencodePendingCount: 2 });
    await expect(updateAutomaticConfiguration(true, true)).resolves.toMatchObject({ autoConfigureOmo: true });
    await expect(applyAutomaticConfiguration()).resolves.toMatchObject({ appliedCount: 2 });
    await expect(repairConfiguration()).resolves.toEqual({
      updatedTargetCount: 4,
      addedFallbackCount: 4,
      removedFallbackCount: 1,
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:17891/api/settings",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ auto_configure_opencode: true, auto_configure_omo: true }),
      }),
    );
  });

  it("copies a validated account API key directly to the clipboard", async () => {
    const apiKey = `sk-${"k".repeat(64)}`;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ account_id: "target-account", api_key: apiKey }),
    }));

    await copyAccountApiKey("target-account");

    expect(writeText).toHaveBeenCalledWith(apiKey);
  });

  it("sends explicit account status and cleanup targets", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ account_id: "target-account", status: "exhausted" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          account_id: "target-account",
          github_username: "target-user",
          status: "done",
          manual_intervention: null,
          promoted_account_id: null,
          error_code: null,
          error_message: null,
        }),
      });
    vi.stubGlobal("fetch", fetchMock);

    await expect(markAccountExhausted("target-account")).resolves.toBeUndefined();
    await expect(startAccountCleanup("target-account", "target-user")).resolves.toMatchObject({
      status: "done",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:17891/api/accounts/target-account",
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({ confirmed_username: "target-user" }),
      }),
    );
  });

  it("subscribes to validated flow events and closes cleanly", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onSession = vi.fn();
    const onError = vi.fn();
    const unsubscribe = subscribeFlow("flow-test-1", onSession, onError);
    const socket = FakeWebSocket.instances[0];

    socket.emitMessage({
      event: "manual_intervention_required",
      version: 1,
      timestamp: "2026-07-25T05:00:00Z",
      flow_id: "flow-test-1",
      payload: manualSession,
    });

    expect(socket.url).toBe("ws://127.0.0.1:17891/ws/flow/flow-test-1");
    expect(onSession).toHaveBeenCalledWith(expect.objectContaining({ status: "manual_verify" }));
    expect(onError).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("rejects a flow event with a mismatched identifier", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onError = vi.fn();
    const unsubscribe = subscribeFlow("flow-test-1", vi.fn(), onError);

    FakeWebSocket.instances[0].emitMessage({
      event: "flow_snapshot",
      version: 1,
      timestamp: "2026-07-25T05:00:00Z",
      flow_id: "different-flow",
      payload: manualSession,
    });

    expect(onError).toHaveBeenCalledWith("流程事件标识不一致");
    unsubscribe();
  });

  it("reports a synchronous WebSocket connection failure without throwing", () => {
    vi.stubGlobal("WebSocket", ThrowingWebSocket);
    const onError = vi.fn();

    const unsubscribe = subscribeFlow("flow-test-1", vi.fn(), onError);

    expect(onError).toHaveBeenCalledWith("无法连接流程事件");
    unsubscribe();
  });
});
