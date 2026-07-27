import { invoke } from "@tauri-apps/api/core";
import type { BackendProcessStatus } from "./contracts";

const DEFAULT_BACKEND_PORT = 17891;
let activeBackendPort = DEFAULT_BACKEND_PORT;

export function isTauriRuntime(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

export async function startBackend(): Promise<BackendProcessStatus> {
  if (!isTauriRuntime()) return developmentBackendStatus();
  return parseBackendProcessStatus(await invoke<unknown>("start_backend"));
}

export async function backendProcessStatus(): Promise<BackendProcessStatus> {
  if (!isTauriRuntime()) return developmentBackendStatus();
  return parseBackendProcessStatus(await invoke<unknown>("backend_status"));
}

/** Selects the validated loopback port returned by the Tauri sidecar owner. */
export function configureBackendPort(port: number): void {
  if (!isValidPort(port)) throw new Error("本地服务返回了无效端口");
  activeBackendPort = port;
}

export async function stopBackend(): Promise<void> {
  if (isTauriRuntime()) await invoke("stop_backend");
}

export function backendHttpUrl(): string {
  return `http://127.0.0.1:${activeBackendPort}`;
}

export function backendWebSocketUrl(): string {
  return `ws://127.0.0.1:${activeBackendPort}`;
}

function developmentBackendStatus(): BackendProcessStatus {
  return { running: false, pid: null, port: DEFAULT_BACKEND_PORT };
}

function parseBackendProcessStatus(payload: unknown): BackendProcessStatus {
  if (
    !isRecord(payload)
    || typeof payload.running !== "boolean"
    || (payload.pid !== null && !isPositiveInteger(payload.pid))
    || (payload.port !== null && !isValidPort(payload.port))
    || (payload.running && payload.port === null)
  ) {
    throw new Error("桌面宿主返回了无效本地服务状态");
  }
  return { running: payload.running, pid: payload.pid, port: payload.port };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isValidPort(value: unknown): value is number {
  return isPositiveInteger(value) && value <= 65535;
}
