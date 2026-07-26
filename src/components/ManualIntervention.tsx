import { useState } from "react";
import { CircleAlert, Play, Square } from "lucide-react";
import type { ManualIntervention as ManualInterventionRequest } from "../services/api";

type ManualInterventionProps = {
  request: ManualInterventionRequest;
  screenshotUrl: string | null;
  isSubmitting: boolean;
  onContinue: (apiKey?: string) => void;
  onCancel: () => void;
};

export function ManualIntervention({
  request,
  screenshotUrl,
  isSubmitting,
  onContinue,
  onCancel,
}: ManualInterventionProps) {
  const [apiKey, setApiKey] = useState("");
  const requiresApiKey = request.reason === "api_key_input";
  const continueLabel = request.reason === "payment" ? "已支付，继续" : "已完成，继续";

  const handleContinue = () => {
    const submittedApiKey = requiresApiKey ? apiKey : undefined;
    setApiKey("");
    onContinue(submittedApiKey);
  };

  return (
    <section className="manual-panel" aria-labelledby="manual-title">
      <div className="manual-icon"><CircleAlert size={22} /></div>
      <div className="manual-content">
        <p className="section-label">需要你的操作</p>
        <h3 id="manual-title">{request.title}</h3>
        <p>{request.instruction}</p>
        {screenshotUrl && (
          <img className="manual-screenshot" src={screenshotUrl} alt="当前浏览器页面" />
        )}
        {requiresApiKey && (
          <label className="manual-secret-field">
            <span>Default API Key</span>
            <input
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              pattern="sk-[A-Za-z0-9]{64}"
              placeholder="sk-..."
              aria-label="Default API Key"
            />
          </label>
        )}
        <div className="manual-actions">
          <button
            className="primary-button"
            type="button"
            onClick={handleContinue}
            disabled={isSubmitting || (requiresApiKey && !/^sk-[A-Za-z0-9]{64}$/.test(apiKey))}
          >
            <Play size={16} />
            {isSubmitting ? "正在检查" : continueLabel}
          </button>
          <button className="secondary-button" type="button" onClick={onCancel} disabled={isSubmitting}>
            <Square size={15} />
            中止流程
          </button>
        </div>
      </div>
    </section>
  );
}
