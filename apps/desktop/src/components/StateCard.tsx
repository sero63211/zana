import type { ReactNode } from "react";

import { Icon, type IconName } from "../icons";

interface StateCardProps {
  icon: IconName;
  title: string;
  children: ReactNode;
}

export function StateCard({ icon, title, children }: StateCardProps) {
  return (
    <article className="state-card">
      <div className="state-card__icon" aria-hidden="true">
        <Icon name={icon} size={17} />
      </div>
      <div>
        <h3>{title}</h3>
        <div className="state-card__copy">{children}</div>
      </div>
    </article>
  );
}
