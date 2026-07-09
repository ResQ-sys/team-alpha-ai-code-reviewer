/// <reference types="vite/client" />

interface ImportMetaEnv {
  // Optional API key sent as X-API-Key on state-changing calls. Injected at
  // build time; absent in open-by-default dev.
  readonly VITE_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
