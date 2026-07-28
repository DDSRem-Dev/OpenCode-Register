import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Toast } from "./Toast";

describe("Toast", () => {
  afterEach(() => cleanup());

  it("renders a dismissible completion message in the document body", () => {
    const onDismiss = vi.fn();
    render(<Toast message="自动配置设置已保存" onDismiss={onDismiss} />);

    const toast = screen.getByRole("status");
    expect(toast).toHaveClass("toast");
    expect(toast.parentElement).toBe(document.body);
    expect(toast).toHaveTextContent("自动配置设置已保存");

    fireEvent.click(screen.getByRole("button", { name: "关闭提示" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
