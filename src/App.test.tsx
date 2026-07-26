import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { HealthResponse } from "./services/contracts";

const serviceMocks = vi.hoisted(() => ({
  fetchHealth: vi.fn(),
  startBackend: vi.fn(),
}));

vi.mock("./services/api", () => serviceMocks);
vi.mock("./pages/Dashboard", () => ({
  Dashboard: ({ isBackendConnected }: { isBackendConnected: boolean }) => (
    <div>{isBackendConnected ? "Dashboard connected" : "Dashboard offline"}</div>
  ),
}));
vi.mock("./pages/CreateFlow", () => ({
  CreateFlow: ({ isBackendConnected }: { isBackendConnected: boolean }) => (
    <div>{isBackendConnected ? "Flow connected" : "Flow offline"}</div>
  ),
}));
vi.mock("./pages/Settings", () => ({
  Settings: ({ isBackendConnected }: { isBackendConnected: boolean }) => (
    <div>{isBackendConnected ? "Settings connected" : "Settings offline"}</div>
  ),
}));

const health: HealthResponse = {
  status: "ok",
  service: "opencode-register-backend",
  version: "0.0.1",
  storage_mode: "sandbox",
};

describe("App", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("connects the frontend shell to the local backend health contract", async () => {
    serviceMocks.startBackend.mockResolvedValue({ running: false, pid: null });
    serviceMocks.fetchHealth.mockResolvedValue(health);

    render(<App />);

    expect(await screen.findByRole("button", { name: "连接状态：服务正常；重新连接" })).toBeInTheDocument();
    expect(screen.queryByText("本地服务")).not.toBeInTheDocument();
    expect(screen.queryByText("opencode-register-backend")).not.toBeInTheDocument();
    expect(screen.getByText("Dashboard connected")).toBeInTheDocument();
    expect(screen.getByText("Flow connected")).toBeInTheDocument();
  });

  it("retries the health probe without abandoning the current connection attempt", async () => {
    serviceMocks.startBackend.mockResolvedValue({ running: false, pid: null });
    serviceMocks.fetchHealth
      .mockRejectedValueOnce(new Error("backend is still starting"))
      .mockResolvedValueOnce(health);

    render(<App />);

    expect(await screen.findByRole("button", { name: "连接状态：服务正常；重新连接" })).toBeInTheDocument();
    expect(serviceMocks.fetchHealth).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Dashboard connected")).toBeInTheDocument();
  });

  it("does not let an older connection result overwrite a newer retry", async () => {
    let resolveFirstHealth: (value: HealthResponse) => void = () => undefined;
    const firstHealth = new Promise<HealthResponse>((resolve) => {
      resolveFirstHealth = resolve;
    });
    serviceMocks.startBackend.mockResolvedValue({ running: false, pid: null });
    serviceMocks.fetchHealth
      .mockReturnValueOnce(firstHealth)
      .mockResolvedValueOnce(health);

    render(<App />);
    await waitFor(() => expect(serviceMocks.fetchHealth).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "连接状态：正在连接；重新连接" }));

    expect(await screen.findByRole("button", { name: "连接状态：服务正常；重新连接" })).toBeInTheDocument();
    resolveFirstHealth({ ...health, service: "stale-backend" });
    await waitFor(() => expect(serviceMocks.fetchHealth).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "连接状态：服务正常；重新连接" })).toBeInTheDocument();
  });

  it("switches desktop workspaces without unmounting their active state", async () => {
    serviceMocks.startBackend.mockResolvedValue({ running: false, pid: null });
    serviceMocks.fetchHealth.mockResolvedValue(health);
    render(<App />);

    await screen.findByRole("button", { name: "连接状态：服务正常；重新连接" });
    const dashboardView = screen.getByText("Dashboard connected").parentElement;
    const flowView = screen.getByText("Flow connected").parentElement;
    const settingsView = screen.getByText("Settings connected").parentElement;
    expect(dashboardView).not.toHaveAttribute("hidden");
    expect(flowView).toHaveAttribute("hidden");
    expect(settingsView).toHaveAttribute("hidden");

    fireEvent.click(screen.getByRole("button", { name: "创建账号" }));

    expect(screen.getByRole("heading", { name: "创建账号" })).toBeInTheDocument();
    expect(dashboardView).toHaveAttribute("hidden");
    expect(flowView).not.toHaveAttribute("hidden");

    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.getByRole("heading", { name: "设置" })).toBeInTheDocument();
    expect(flowView).toHaveAttribute("hidden");
    expect(settingsView).not.toHaveAttribute("hidden");
  });
});
