/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Absolute base URL of the API, e.g. `https://pulse-api.onrender.com`.
   *
   * Left unset in local dev, where Vite proxies `/api` to `127.0.0.1:8000` and
   * requests stay same-origin. Set at build time for a split deploy.
   */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
