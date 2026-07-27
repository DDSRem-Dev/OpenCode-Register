import { afterEach, describe, expect, it, vi } from "vitest";
import {
  backendHttpUrl,
  backendWebSocketUrl,
  configureBackendPort,
  startBackend,
} from "./backendConnection";

const invokeMock = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

describe("backendConnection", () => {
  afterEach(() => {
    configureBackendPort(17891);
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
    vi.clearAllMocks();
  });

  it("uses the fixed development port outside Tauri", async () => {
    await expect(startBackend()).resolves.toEqual({ running: false, pid: null, port: 17891 });
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("validates the dynamic port returned by Tauri", async () => {
    Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
    invokeMock.mockResolvedValue({ running: true, pid: 42, port: 43123 });

    const status = await startBackend();
    if (status.port === null) throw new Error("测试 sidecar 应返回端口");
    configureBackendPort(status.port);

    expect(status).toEqual({ running: true, pid: 42, port: 43123 });
    expect(backendHttpUrl()).toBe("http://127.0.0.1:43123");
    expect(backendWebSocketUrl()).toBe("ws://127.0.0.1:43123");
  });

  it("rejects missing and out-of-range Tauri ports", async () => {
    Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
    invokeMock.mockResolvedValueOnce({ running: true, pid: 42, port: null });
    await expect(startBackend()).rejects.toThrow("无效本地服务状态");

    invokeMock.mockResolvedValueOnce({ running: true, pid: 42, port: 65536 });
    await expect(startBackend()).rejects.toThrow("无效本地服务状态");
  });
});
