import { createContext, useContext } from "react";

import type { AgentApi } from "./agent";

const AgentApiContext = createContext<AgentApi | null>(null);

export function AgentApiProvider({
  api,
  children,
}: {
  api: AgentApi;
  children: React.ReactNode;
}) {
  return (
    <AgentApiContext.Provider value={api}>
      {children}
    </AgentApiContext.Provider>
  );
}

export function useAgentApi(): AgentApi {
  const api = useContext(AgentApiContext);
  if (!api) throw new Error("AgentApiProvider is required");
  return api;
}
