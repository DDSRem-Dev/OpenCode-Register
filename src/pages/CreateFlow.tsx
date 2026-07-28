import { useCallback, useEffect, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { Check, Circle, CircleCheck, LoaderCircle, Pause, Play, RefreshCw } from "lucide-react";
import { ManualIntervention } from "../components/ManualIntervention";
import { interfaceMotion, motionDuration } from "../animations";
import {
  cancelFlow,
  fetchFlow,
  flowScreenshotUrl,
  pauseFlow,
  resumeFlow,
  startAccountFlow,
  subscribeFlow,
  type FlowSession,
  type FlowStatus,
} from "../services/api";

type CreateFlowProps = {
  isBackendConnected: boolean;
  isVaultUnlocked: boolean;
  onAccountCreated?: () => void;
};

const statusLabels: Record<FlowStatus, string> = {
  idle: "准备启动",
  creating_email: "正在创建临时邮箱",
  github_register: "正在填写 GitHub 注册信息",
  manual_verify: "等待人工操作",
  github_email_verify: "正在验证注册邮箱",
  opencode_login: "正在登录 OpenCode",
  pending_payment: "等待 OpenCode Go 付款",
  fetch_api_key: "正在读取 Default API Key",
  done: "账号创建已完成",
  error: "流程失败",
  cancelled: "流程已中止",
};

const terminalStatuses: FlowStatus[] = ["done", "error", "cancelled"];
const pausableStatuses: FlowStatus[] = ["idle", "creating_email", "github_register", "github_email_verify"];
const flowSteps = ["创建临时邮箱", "注册 GitHub", "连接 OpenCode", "用户完成支付", "写入本地号池"];
const flowStepByStatus: Record<FlowStatus, number> = {
  idle: 0,
  creating_email: 0,
  github_register: 1,
  manual_verify: 1,
  github_email_verify: 1,
  opencode_login: 2,
  pending_payment: 3,
  fetch_api_key: 4,
  done: 5,
  error: 0,
  cancelled: 0,
};

export function CreateFlow({ isBackendConnected, isVaultUnlocked, onAccountCreated }: CreateFlowProps) {
  const workspaceRef = useRef<HTMLElement>(null);
  const [session, setSession] = useState<FlowSession | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestGenerationRef = useRef(0);
  const isActionPendingRef = useRef(false);
  const notifiedCompletedFlowRef = useRef<string | null>(null);
  const activeFlowId = session && !terminalStatuses.includes(session.status) ? session.flow_id : null;

  useEffect(() => {
    if (!activeFlowId) return undefined;
    const controller = new AbortController();
    const unsubscribe = subscribeFlow(activeFlowId, setSession, setError);
    const timer = window.setInterval(() => {
      if (isActionPendingRef.current) return;
      const requestGeneration = requestGenerationRef.current;
      void fetchFlow(activeFlowId, controller.signal)
        .then((nextSession) => {
          if (requestGenerationRef.current === requestGeneration) setSession(nextSession);
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted && requestGenerationRef.current === requestGeneration) {
            setError(reason instanceof Error ? reason.message : "无法刷新流程状态");
          }
        });
    }, 3000);
    return () => {
      unsubscribe();
      controller.abort();
      window.clearInterval(timer);
    };
  }, [activeFlowId]);

  useEffect(() => {
    if (session?.status !== "done" || notifiedCompletedFlowRef.current === session.flow_id) return;
    notifiedCompletedFlowRef.current = session.flow_id;
    onAccountCreated?.();
  }, [onAccountCreated, session]);

  const handleRefresh = useCallback(async () => {
    if (!session) return;
    const requestGeneration = requestGenerationRef.current + 1;
    requestGenerationRef.current = requestGeneration;
    isActionPendingRef.current = true;
    setIsSubmitting(true);
    setError(null);
    const controller = new AbortController();
    try {
      const nextSession = await fetchFlow(session.flow_id, controller.signal);
      if (requestGenerationRef.current === requestGeneration) setSession(nextSession);
    } catch (reason) {
      if (requestGenerationRef.current === requestGeneration) {
        setError(reason instanceof Error ? reason.message : "无法刷新流程状态");
      }
    } finally {
      controller.abort();
      if (requestGenerationRef.current === requestGeneration) {
        isActionPendingRef.current = false;
        setIsSubmitting(false);
      }
    }
  }, [session]);

  const handleStart = useCallback(async () => {
    const requestGeneration = requestGenerationRef.current + 1;
    requestGenerationRef.current = requestGeneration;
    isActionPendingRef.current = true;
    setIsSubmitting(true);
    setError(null);
    try {
      const nextSession = await startAccountFlow();
      if (requestGenerationRef.current === requestGeneration) setSession(nextSession);
    } catch (reason) {
      if (requestGenerationRef.current === requestGeneration) {
        setError(reason instanceof Error ? reason.message : "无法启动账号流程");
      }
    } finally {
      if (requestGenerationRef.current === requestGeneration) {
        isActionPendingRef.current = false;
        setIsSubmitting(false);
      }
    }
  }, []);

  const handleContinue = useCallback(async (apiKey?: string) => {
    if (!session) return;
    const requestGeneration = requestGenerationRef.current + 1;
    requestGenerationRef.current = requestGeneration;
    isActionPendingRef.current = true;
    setIsSubmitting(true);
    setError(null);
    try {
      const nextSession = await resumeFlow(session.flow_id, apiKey);
      if (requestGenerationRef.current === requestGeneration) setSession(nextSession);
    } catch (reason) {
      if (requestGenerationRef.current === requestGeneration) {
        setError(reason instanceof Error ? reason.message : "无法恢复账号流程");
      }
    } finally {
      if (requestGenerationRef.current === requestGeneration) {
        isActionPendingRef.current = false;
        setIsSubmitting(false);
      }
    }
  }, [session]);

  const handleCancel = useCallback(async () => {
    if (!session) return;
    const requestGeneration = requestGenerationRef.current + 1;
    requestGenerationRef.current = requestGeneration;
    isActionPendingRef.current = true;
    setIsSubmitting(true);
    setError(null);
    try {
      const nextSession = await cancelFlow(session.flow_id);
      if (requestGenerationRef.current === requestGeneration) setSession(nextSession);
    } catch (reason) {
      if (requestGenerationRef.current === requestGeneration) {
        setError(reason instanceof Error ? reason.message : "无法中止账号流程");
      }
    } finally {
      if (requestGenerationRef.current === requestGeneration) {
        isActionPendingRef.current = false;
        setIsSubmitting(false);
      }
    }
  }, [session]);

  const handlePause = useCallback(async () => {
    if (!session) return;
    const requestGeneration = requestGenerationRef.current + 1;
    requestGenerationRef.current = requestGeneration;
    isActionPendingRef.current = true;
    setIsSubmitting(true);
    setError(null);
    try {
      const nextSession = await pauseFlow(session.flow_id);
      if (requestGenerationRef.current === requestGeneration) setSession(nextSession);
    } catch (reason) {
      if (requestGenerationRef.current === requestGeneration) {
        setError(reason instanceof Error ? reason.message : "无法暂停账号流程");
      }
    } finally {
      if (requestGenerationRef.current === requestGeneration) {
        isActionPendingRef.current = false;
        setIsSubmitting(false);
      }
    }
  }, [session]);

  const canStart = isBackendConnected && isVaultUnlocked && (!session || terminalStatuses.includes(session.status));
  const isRunning = session && !terminalStatuses.includes(session.status) && !session.manual_intervention;
  const canPause = session && pausableStatuses.includes(session.status);
  const currentStep = session ? flowStepByStatus[session.status] : 0;

  useGSAP(() => {
    const duration = motionDuration(interfaceMotion.standard);
    const timeline = gsap.timeline({ defaults: { duration, ease: interfaceMotion.ease } });
    const activeSteps = gsap.utils.toArray<HTMLElement>(".flow-steps li.current, .flow-steps li.complete:last-of-type");
    const notices = gsap.utils.toArray<HTMLElement>(".manual-panel, .success-banner, .flow-main > .error-banner");
    timeline.fromTo(
      ".flow-status",
      { autoAlpha: 0, y: duration === 0 ? 0 : 10 },
      { autoAlpha: 1, y: 0, clearProps: "all" },
    );
    if (activeSteps.length > 0) {
      timeline.fromTo(
        activeSteps,
        { scale: duration === 0 ? 1 : 0.96 },
        { scale: 1, duration: motionDuration(interfaceMotion.quick), clearProps: "transform" },
        "<",
      );
    }
    if (notices.length > 0) {
      timeline.fromTo(
        notices,
        { autoAlpha: 0, y: duration === 0 ? 0 : 8 },
        { autoAlpha: 1, y: 0, clearProps: "all" },
        duration === 0 ? "<" : "<0.08",
      );
    }
  }, {
    dependencies: [session?.status, session?.manual_intervention?.reason, error],
    scope: workspaceRef,
    revertOnUpdate: true,
  });

  return (
    <section ref={workspaceRef} className="flow-workspace" aria-labelledby="flow-title">
      <div className="flow-heading">
        <div>
          <p className="section-label">自动化任务</p>
          <h2 id="flow-title">创建 OpenCode 账号</h2>
          <p className="section-description">流程会在安全验证和付款步骤暂停，等待你亲自完成。</p>
        </div>
        <button className="primary-button" type="button" onClick={() => void handleStart()} disabled={!canStart || isSubmitting}>
          <Play size={16} />
          {isSubmitting && !session ? "正在启动" : "新建账号"}
        </button>
      </div>

      <div className="flow-layout">
        <aside className="flow-steps" aria-label="创建步骤">
          <ol>
            {flowSteps.map((step, index) => {
              const isComplete = currentStep > index;
              const isCurrent = currentStep === index && session?.status !== "done";
              return (
                <li className={isComplete ? "complete" : isCurrent ? "current" : ""} key={step}>
                  <span>{isComplete ? <Check size={14} /> : <Circle size={12} />}</span>
                  <div><strong>{step}</strong><small>{isComplete ? "已完成" : isCurrent ? "当前步骤" : "等待"}</small></div>
                </li>
              );
            })}
          </ol>
          <div className="flow-boundary-note"><strong>人工边界</strong><span>验证与支付始终由你完成</span></div>
        </aside>

        <div className="flow-main">
          <div className="flow-status" aria-live="polite">
            <div className={`flow-state-icon ${session?.status ?? "idle"}`}>
              {session?.status === "done" ? <CircleCheck size={22} /> : <LoaderCircle size={22} className={isRunning ? "spin" : ""} />}
            </div>
            <div>
              <span>当前状态</span>
              <strong>
                {session
                  ? statusLabels[session.status]
                  : !isBackendConnected
                    ? "等待本地服务"
                    : isVaultUnlocked
                      ? "尚未创建流程"
                      : "请先解锁本地账号库"}
              </strong>
              {session?.pause_requested && <small>将在当前原子操作完成后暂停</small>}
              {session?.github_username && <small>GitHub 用户名：{session.github_username}</small>}
              {session?.opencode_workspace_id && <small>OpenCode 工作区：{session.opencode_workspace_id}</small>}
              {session?.opencode_provider_name && <small>OpenCode Provider：{session.opencode_provider_name}</small>}
            </div>
            {session && (
              <div className="flow-status-actions">
                <button
                  className="icon-button"
                  type="button"
                  onClick={() => void handleRefresh()}
                  title="刷新流程状态"
                  aria-label="刷新流程状态"
                  disabled={isSubmitting}
                >
                  <RefreshCw size={15} className={isSubmitting ? "spin" : ""} />
                </button>
                {isRunning && canPause && (
                  <button className="icon-button" type="button" onClick={() => void handlePause()} title="暂停流程" aria-label="暂停流程" disabled={isSubmitting || session.pause_requested}>
                    <Pause size={15} />
                  </button>
                )}
              </div>
            )}
          </div>

          {session?.manual_intervention && (
            <ManualIntervention
              request={session.manual_intervention}
              screenshotUrl={session.screenshot_id ? flowScreenshotUrl(session.flow_id, session.screenshot_id) : null}
              isSubmitting={isSubmitting}
              onContinue={(apiKey) => void handleContinue(apiKey)}
              onCancel={() => void handleCancel()}
            />
          )}

          {session?.status === "done" && (
            <div className="success-banner" role="status">
              <CircleCheck size={18} />
              <div><strong>账号创建完成</strong><span>凭据已加密保存，并已写入 OpenCode 号池配置。</span></div>
            </div>
          )}

          {(error || session?.error_message) && (
            <div className="error-banner" role="alert">
              <div><strong>流程无法继续</strong><span>{error ?? session?.error_message}</span></div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
