export const interfaceMotion = {
  quick: 0.18,
  standard: 0.34,
  entrance: 0.46,
  ease: "power3.out",
} as const;

export function motionDuration(duration: number): number {
  if (typeof window.matchMedia !== "function") return 0;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : duration;
}
