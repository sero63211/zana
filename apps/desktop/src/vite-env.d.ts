/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ZANA_API_BASE?: string;
  readonly VITE_ZANA_API_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
