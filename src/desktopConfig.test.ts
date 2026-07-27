import { describe, expect, it } from "vitest";
import desktopCapability from "../src-tauri/capabilities/default.json";

describe("desktop capability", () => {
  it("allows the custom title bar to start window dragging", () => {
    expect(desktopCapability.permissions).toContain("core:window:allow-start-dragging");
  });
});
