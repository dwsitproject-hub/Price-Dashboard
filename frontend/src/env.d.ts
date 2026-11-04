/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_HUB_SDK_URL?: string;
  readonly VITE_HUB_SDK_KEY?: string;
  readonly VITE_GIT_SHA?: string;
}

declare module 'plotly.js-basic-dist-min' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Plotly: any;
  export default Plotly;
}
