import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountTransfer } from "./AccountTransfer";

describe("AccountTransfer", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("rejects mismatched export passwords without sending them", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<AccountTransfer onImported={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("导出包密码"), { target: { value: "first bundle password" } });
    fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "second bundle password" } });
    fireEvent.click(screen.getByRole("button", { name: "导出" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("两次输入的导出包密码不一致");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("downloads a successful encrypted export and clears passwords", async () => {
    const createObjectUrl = vi.fn().mockReturnValue("blob:bundle-test");
    const revokeObjectUrl = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob(["encrypted-bundle"]),
    }));
    vi.stubGlobal("URL", { createObjectURL: createObjectUrl, revokeObjectURL: revokeObjectUrl });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<AccountTransfer onImported={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("导出包密码"), { target: { value: "matching bundle password" } });
    fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "matching bundle password" } });
    fireEvent.click(screen.getByRole("button", { name: "导出" }));

    expect(await screen.findByRole("status")).toHaveTextContent("加密账号包已导出");
    expect(createObjectUrl).toHaveBeenCalledOnce();
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:bundle-test");
    expect(screen.getByLabelText("导出包密码")).toHaveValue("");
    expect(screen.getByLabelText("确认密码")).toHaveValue("");
  });

  it("imports a selected bundle, clears its password, and refreshes accounts", async () => {
    const onImported = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ imported_count: 1 }),
    }));
    render(<AccountTransfer onImported={onImported} />);
    const file = new File([new Uint8Array([1, 2, 3])], "accounts.ocrbundle");
    Object.defineProperty(file, "arrayBuffer", {
      value: async () => new Uint8Array([1, 2, 3]).buffer,
    });

    const fileInput = screen.getByLabelText("账号包文件");
    fireEvent.change(fileInput, { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("账号包密码"), { target: { value: "matching bundle password" } });
    const importForm = fileInput.closest("form");
    if (!importForm) throw new Error("导入表单不存在");
    fireEvent.submit(importForm);

    expect(await screen.findByRole("status")).toHaveTextContent("已导入 1 个账号");
    expect(onImported).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("账号包密码")).toHaveValue("");
  });
});
