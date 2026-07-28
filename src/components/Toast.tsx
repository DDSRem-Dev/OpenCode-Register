import { useRef } from "react";
import { createPortal } from "react-dom";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { CircleCheck, X } from "lucide-react";
import { interfaceMotion, motionDuration } from "../animations";

type ToastProps = {
  message: string;
  onDismiss: () => void;
};

export function Toast({ message, onDismiss }: ToastProps) {
  const toastRef = useRef<HTMLElement>(null);

  useGSAP(() => {
    if (!toastRef.current) return;
    const duration = motionDuration(interfaceMotion.standard);
    gsap.fromTo(
      toastRef.current,
      { autoAlpha: 0, x: duration === 0 ? 0 : 12, y: duration === 0 ? 0 : 10, scale: duration === 0 ? 1 : 0.98 },
      { autoAlpha: 1, x: 0, y: 0, scale: 1, duration, ease: interfaceMotion.ease, clearProps: "all" },
    );
  }, { scope: toastRef });

  return createPortal(
    <aside ref={toastRef} className="toast" role="status" aria-live="polite">
      <div className="toast-icon"><CircleCheck size={18} /></div>
      <div className="toast-content"><strong>操作完成</strong><span>{message}</span></div>
      <button className="toast-close" type="button" onClick={onDismiss} aria-label="关闭提示">
        <X size={15} />
      </button>
    </aside>,
    document.body,
  );
}
