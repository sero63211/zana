import type { ReactNode } from "react";

import { Icon, type IconName } from "../icons";

type StatusTone = "healthy" | "loading" | "error" | "neutral";

interface StatusPanelProps {
  tone: StatusTone;
  eyebrow: string;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
}

const TONE_ICON: Record<StatusTone, IconName> = {
  healthy: "check",
  loading: "refresh",
  error: "alert",
  neutral: "info",
};

export function StatusPanel({ tone, eyebrow, title, children, actions }: StatusPanelProps) {
  return (
    <section className={`status-panel status-panel--${tone}`} aria-live={tone === "error" ? "assertive" : "polite"}>
      <span className="status-mark" aria-hidden="true">
        <Icon name={TONE_ICON[tone]} size={18} />
      </span>
      <div className="status-panel__body">
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
        {children ? <div className="status-panel__copy">{children}</div> : null}
        {actions ? <div className="status-panel__actions">{actions}</div> : null}
      </div>
    </section>
  );
}
