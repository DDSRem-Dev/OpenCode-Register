import { FormEvent, useEffect, useRef, useState } from "react";
import { Download, Upload } from "lucide-react";
import { exportAccounts, importAccounts } from "../services/api";

type AccountTransferProps = {
  onImported: () => void;
};

type TransferMessage = { kind: "success" | "error"; text: string } | null;

const maxBundleSize = 10 * 1024 * 1024;

export function AccountTransfer({ onImported }: AccountTransferProps) {
  const [exportPassword, setExportPassword] = useState("");
  const [exportConfirmation, setExportConfirmation] = useState("");
  const [importPassword, setImportPassword] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [activeOperation, setActiveOperation] = useState<"export" | "import" | null>(null);
  const [message, setMessage] = useState<TransferMessage>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const operationControllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => operationControllerRef.current?.abort(), []);

  const handleExport = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (exportPassword !== exportConfirmation) {
      setMessage({ kind: "error", text: "两次输入的导出包密码不一致" });
      return;
    }
    operationControllerRef.current?.abort();
    const controller = new AbortController();
    operationControllerRef.current = controller;
    setActiveOperation("export");
    setMessage(null);
    try {
      const bundle = await exportAccounts(exportPassword, controller.signal);
      if (controller.signal.aborted) return;
      downloadBundle(bundle);
      setMessage({ kind: "success", text: "加密账号包已导出" });
    } catch (reason) {
      if (!controller.signal.aborted) {
        setMessage({ kind: "error", text: errorMessage(reason, "无法导出账号包") });
      }
    } finally {
      if (operationControllerRef.current === controller) {
        operationControllerRef.current = null;
        setExportPassword("");
        setExportConfirmation("");
        setActiveOperation(null);
      }
    }
  };

  const handleImport = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedFile) return;
    if (selectedFile.size > maxBundleSize) {
      setMessage({ kind: "error", text: "导入包不能超过 10 MB" });
      return;
    }
    operationControllerRef.current?.abort();
    const controller = new AbortController();
    operationControllerRef.current = controller;
    setActiveOperation("import");
    setMessage(null);
    try {
      const bundle = await selectedFile.arrayBuffer();
      if (controller.signal.aborted) return;
      const result = await importAccounts(bundle, importPassword, controller.signal);
      if (controller.signal.aborted) return;
      setMessage({ kind: "success", text: `已导入 ${result.importedCount} 个账号` });
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      onImported();
    } catch (reason) {
      if (!controller.signal.aborted) {
        setMessage({ kind: "error", text: errorMessage(reason, "无法导入账号包") });
      }
    } finally {
      if (operationControllerRef.current === controller) {
        operationControllerRef.current = null;
        setImportPassword("");
        setActiveOperation(null);
      }
    }
  };

  return (
    <div className="account-transfer" aria-label="账号导入导出">
      <form className="transfer-form" onSubmit={(event) => void handleExport(event)}>
        <div className="transfer-title"><Download size={17} /><strong>导出加密包</strong></div>
        <label>
          <span>导出包密码</span>
          <input
            type="password"
            value={exportPassword}
            onChange={(event) => setExportPassword(event.target.value)}
            minLength={12}
            maxLength={256}
            autoComplete="new-password"
            required
          />
        </label>
        <label>
          <span>确认密码</span>
          <input
            type="password"
            value={exportConfirmation}
            onChange={(event) => setExportConfirmation(event.target.value)}
            minLength={12}
            maxLength={256}
            autoComplete="new-password"
            required
          />
        </label>
        <button className="secondary-button" type="submit" disabled={activeOperation !== null}>
          <Download size={15} />
          {activeOperation === "export" ? "正在导出" : "导出"}
        </button>
      </form>

      <form className="transfer-form" onSubmit={(event) => void handleImport(event)}>
        <div className="transfer-title"><Upload size={17} /><strong>导入加密包</strong></div>
        <label>
          <span>账号包文件</span>
          <input
            ref={fileInputRef}
            type="file"
            accept=".ocrbundle,application/vnd.opencode-register.bundle"
            onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
            required
          />
        </label>
        <label>
          <span>账号包密码</span>
          <input
            type="password"
            value={importPassword}
            onChange={(event) => setImportPassword(event.target.value)}
            minLength={12}
            maxLength={256}
            autoComplete="current-password"
            required
          />
        </label>
        <button className="secondary-button" type="submit" disabled={activeOperation !== null || !selectedFile}>
          <Upload size={15} />
          {activeOperation === "import" ? "正在导入" : "导入"}
        </button>
      </form>

      {message && (
        <div className={`transfer-message ${message.kind}`} role={message.kind === "error" ? "alert" : "status"}>
          {message.text}
        </div>
      )}
    </div>
  );
}

function downloadBundle(bundle: Blob): void {
  const url = URL.createObjectURL(bundle);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "opencode-accounts.ocrbundle";
  anchor.click();
  URL.revokeObjectURL(url);
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}
