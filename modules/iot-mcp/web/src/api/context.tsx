import { createContext, useContext } from "react";

import type { IoTApi } from "./types";

const ApiContext = createContext<IoTApi | null>(null);

export function ApiProvider({ api, children }: { api: IoTApi; children: React.ReactNode }) {
  return <ApiContext.Provider value={api}>{children}</ApiContext.Provider>;
}

export function useApi(): IoTApi {
  const api = useContext(ApiContext);
  if (!api) throw new Error("ApiProvider is required");
  return api;
}
