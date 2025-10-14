import { useQuery, UseQueryOptions } from "@tanstack/react-query";
import { AgentsListParams, AgentsListResponse, listAgents } from "@/services/agents";

type QueryOptions = Omit<
  UseQueryOptions<AgentsListResponse, unknown, AgentsListResponse, readonly unknown[]>,
  "queryKey" | "queryFn"
>;

export const agentKeys = {
  root: ["agents"] as const,
  list: (params?: AgentsListParams) => ["agents", "list", params] as const,
} as const;

export const useAgents = (params?: AgentsListParams, options?: QueryOptions) => {
  const query = useQuery<AgentsListResponse>({
    queryKey: agentKeys.list(params),
    queryFn: () => listAgents(params),
    staleTime: 15_000,
    retry: options?.retry ?? false,
    ...options,
  });
  return query;
};

export type AgentsQueryResult = ReturnType<typeof useAgents>;
