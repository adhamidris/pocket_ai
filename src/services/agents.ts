import { ApiError, jsonFetch } from "./http";
import { 
  AgentRole, 
  AgentTone, 
  mapRoleFromBackend, 
  mapToneFromBackend 
} from "../lib/agentOptions";
import { getStoredToken } from "./auth";

type UUID = string;

export type AgentsListParams = {
  qName?: string;
  role?: string | null;
  limit?: number;
  offset?: number;
  sortBy?: string;
  order?: "asc" | "desc";
};

export type AgentListItem = {
  id: UUID;
  name: string;
  role: AgentRole;
  tone: AgentTone;
  status: "active" | "inactive" | "draft";
  publicSlug: string | null;
  avatarUrl: string | null;
  createdAt: string; // ISO
};

export type AgentsListResponse = {
  items: AgentListItem[];
  total: number;
};

const buildQuery = (params?: AgentsListParams) => {
  if (!params) return "";
  const sp = new URLSearchParams();
  if (params.qName) sp.set("q_name", params.qName);
  if (params.role) sp.set("role", params.role);
  if (typeof params.limit === "number") sp.set("limit", String(params.limit));
  if (typeof params.offset === "number") sp.set("offset", String(params.offset));
  if (params.sortBy) sp.set("sort_by", params.sortBy);
  if (params.order) sp.set("order", params.order);
  const q = sp.toString();
  return q ? `?${q}` : "";
};

const withAuth = (token?: string | null) => token ?? getStoredToken();

export async function listAgents(
  params?: AgentsListParams,
  options: { token?: string | null; signal?: AbortSignal } = {}
): Promise<AgentsListResponse> {
  const query = buildQuery(params);
  try {
    const raw = await jsonFetch<{ items: any[]; total: number }>(`/v1/agents${query}`, {
      method: "GET",
      token: withAuth(options.token),
      signal: options.signal,
    });
    // map snake_case -> camelCase
    const items: AgentListItem[] = (raw.items ?? []).map((it) => ({
      id: it.id,
      name: it.name,
      role: mapRoleFromBackend(it.role),
      tone: mapToneFromBackend(it.tone),
      status: it.status,
      publicSlug: it.public_slug ?? null,
      avatarUrl: it.avatar_url ?? null,
      createdAt: it.created_at,
    }));
    return { items, total: raw.total ?? items.length };
  } catch (error) {
    if (error instanceof ApiError && (error.code === "configuration_error" || error.code === "network_error")) {
      if (import.meta.env?.DEV) {
        console.warn("[agents] Falling back to empty list due to API error", error.message);
      }
      return { items: [], total: 0 };
    }
    throw error;
  }
}
