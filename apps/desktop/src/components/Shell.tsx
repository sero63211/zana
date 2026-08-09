import type { ReactNode } from "react";

import { Icon } from "../icons";
import { NAV_ITEMS, type NavItem, type ViewId } from "../navigation";

interface ShellProps {
  activeView: ViewId;
  onNavigate: (view: ViewId) => void;
  coreStatus: "pending" | "connected" | "unavailable";
  children: ReactNode;
}

function NavLink({
  item,
  active,
  onNavigate,
}: {
  item: NavItem;
  active: boolean;
  onNavigate: (view: ViewId) => void;
}) {
  return (
    <li>
      <a
        className={active ? "nav-link nav-link--active" : "nav-link"}
        href={item.href}
        aria-current={active ? "page" : undefined}
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          onNavigate(item.id);
        }}
      >
        <Icon name={item.icon} size={17} />
        <span>{item.label}</span>
      </a>
    </li>
  );
}

function CoreIndicator({ status }: { status: ShellProps["coreStatus"] }) {
  const label = status === "connected" ? "Core connected" : status === "pending" ? "Checking Core" : "Core unavailable";
  const icon = status === "connected" ? "check" : status === "pending" ? "refresh" : "alert";
  return (
    <div className="core-indicator" role="status" aria-live="polite">
      <Icon name={icon} size={14} />
      <span>{label}</span>
    </div>
  );
}

export function Shell({ activeView, onNavigate, coreStatus, children }: ShellProps) {
  const activeItem = NAV_ITEMS.find((item) => item.id === activeView) ?? NAV_ITEMS[0];

  return (
    <div className="app-frame">
      <aside className="sidebar" aria-label="ZANA navigation">
        <a className="brand-block" href="#/home" onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          onNavigate("home");
        }}>
          <span className="brand-sigil" aria-hidden="true">Z</span>
          <span className="brand-copy">
            <span className="brand-name">ZANA</span>
            <span className="brand-note">Local capability studio</span>
          </span>
        </a>

        <nav className="nav" aria-label="Primary">
          <ul className="nav-list">
            {NAV_ITEMS.map((item) => (
              <NavLink key={item.id} item={item} active={item.id === activeView} onNavigate={onNavigate} />
            ))}
          </ul>
        </nav>

        <div className="sidebar-footer">
          <CoreIndicator status={coreStatus} />
          <div className="privacy-note">
            <Icon name="lock" size={13} />
            <div>
              <strong>Local mode</strong>
              <span>Telemetry is off</span>
            </div>
          </div>
        </div>
      </aside>

      <main className="workspace" id="main-content" tabIndex={-1}>
        <header className="workspace-header">
          <div>
            <p className="eyebrow">{activeItem.homeState}</p>
            <h1>{activeItem.label}</h1>
          </div>
          <p className="header-copy">{activeItem.description}</p>
        </header>
        {children}
      </main>
    </div>
  );
}
