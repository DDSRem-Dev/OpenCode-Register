import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings";

const serviceMocks = vi.hoisted(() => ({
  applyAutomaticConfiguration: vi.fn(),
  fetchAutomaticConfiguration: vi.fn(),
  updateAutomaticConfiguration: vi.fn(),
}));

vi.mock("../services/api", () => serviceMocks);

const configuration = {
  autoConfigureOpencode: true,
  autoConfigureOmo: true,
  opencodePendingCount: 2,
  omoPendingCount: 1,
  appliedCount: 0,
};

describe("Settings", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("disables Oh My OpenCode when OpenCode automatic configuration is turned off", async () => {
    serviceMocks.fetchAutomaticConfiguration.mockResolvedValue(configuration);
    serviceMocks.updateAutomaticConfiguration.mockResolvedValue({
      ...configuration,
      autoConfigureOpencode: false,
      autoConfigureOmo: false,
    });
    render(<Settings isBackendConnected isVaultUnlocked onConfigurationApplied={vi.fn()} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "自动配置 OpenCode" }));

    await waitFor(() => expect(serviceMocks.updateAutomaticConfiguration).toHaveBeenCalledWith(
      false,
      false,
      expect.any(AbortSignal),
    ));
    expect(screen.getByRole("checkbox", { name: "自动配置 Oh My OpenCode" })).toBeDisabled();
    expect(await screen.findByRole("status")).toHaveTextContent("自动配置设置已保存");
  });

  it("applies pending configuration after the vault is unlocked", async () => {
    const onConfigurationApplied = vi.fn();
    serviceMocks.fetchAutomaticConfiguration.mockResolvedValue(configuration);
    serviceMocks.applyAutomaticConfiguration.mockResolvedValue({
      ...configuration,
      opencodePendingCount: 0,
      omoPendingCount: 0,
      appliedCount: 2,
    });
    render(<Settings isBackendConnected isVaultUnlocked onConfigurationApplied={onConfigurationApplied} />);

    fireEvent.click(await screen.findByRole("button", { name: "应用到现有账号" }));

    expect(await screen.findByRole("status")).toHaveTextContent("已为 2 个账号应用本地配置");
    expect(onConfigurationApplied).toHaveBeenCalledOnce();
  });

  it("keeps applying disabled while the vault is locked", async () => {
    serviceMocks.fetchAutomaticConfiguration.mockResolvedValue(configuration);
    render(<Settings isBackendConnected isVaultUnlocked={false} onConfigurationApplied={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "解锁后应用" })).toBeDisabled();
  });
});
