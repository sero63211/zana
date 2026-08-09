import type { IconName } from "./icons";

export const VIEW_IDS = [
  "home",
  "runtimes-models",
  "capabilities",
  "build-evaluation",
  "images",
  "instances-chat",
  "settings-doctor",
] as const;

export type ViewId = (typeof VIEW_IDS)[number];

export interface NavItem {
  id: ViewId;
  label: string;
  href: string;
  icon: IconName;
  description: string;
  homeState: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    id: "home",
    label: "Home",
    href: "#/home",
    icon: "home",
    description: "Live system status and workspace entry points.",
    homeState: "Live system status",
  },
  {
    id: "runtimes-models",
    label: "Runtimes & Models",
    href: "#/runtimes-models",
    icon: "cpu",
    description: "Discover local runtimes and the models they expose.",
    homeState: "Discovery not connected",
  },
  {
    id: "capabilities",
    label: "Capabilities",
    href: "#/capabilities",
    icon: "layers",
    description: "Build and store reusable capability packages.",
    homeState: "Registry not connected",
  },
  {
    id: "build-evaluation",
    label: "Build & Evaluation",
    href: "#/build-evaluation",
    icon: "hammer",
    description: "Run isolated builds and evaluation gates.",
    homeState: "No build service",
  },
  {
    id: "images",
    label: "Images",
    href: "#/images",
    icon: "image",
    description: "Capture portable ZANA Images from local builds.",
    homeState: "No image data",
  },
  {
    id: "instances-chat",
    label: "Instances & Chat",
    href: "#/instances-chat",
    icon: "message",
    description: "Run local instances and talk to them.",
    homeState: "No instances",
  },
  {
    id: "settings-doctor",
    label: "Settings & Doctor",
    href: "#/settings-doctor",
    icon: "settings",
    description: "Inspect Core health and run diagnostics.",
    homeState: "Doctor available",
  },
] as const;
