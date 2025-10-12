import { ApiError, jsonFetch } from "./http";
import { getStoredToken } from "./auth";

type UUID = string;

type RequestOptions = {
  token?: string | null;
  signal?: AbortSignal;
};

type CursorResponse = {
  total: number;
  hasNext: boolean;
  nextCursor: string | null;
};

export type CustomerListItem = {
  id: UUID;
  fullName: string;
  primaryEmail: string | null;
  conversationsCount: number;
  satisfactionScore: number | null;
  lastContactAt: string | null;
  lifecycleStage: string;
};

export type CustomersListResponse = CursorResponse & {
  items: CustomerListItem[];
};

export type CustomerStats = {
  conversationsTotal: number;
  conversationsLast30Days: number;
  csatAverage: number | null;
  csatTrend: number | null;
  expansionOpportunities: number;
};

export type CustomerContactMethod = {
  type: "email" | "phone" | "social" | "messenger" | "other";
  value: string;
  isPrimary: boolean;
};

export type CustomerTag = {
  id: UUID;
  label: string;
  color: string | null;
};

export type CustomerNote = {
  id: UUID;
  customerId: UUID;
  authorUserId: UUID | null;
  authorAgentId: UUID | null;
  visibility: "internal" | "shared";
  body: string;
  pinned: boolean;
  createdAt: string;
  updatedAt: string;
};

export type CustomerActivityEvent = {
  id: UUID;
  customerId: UUID;
  eventType: string;
  occurredAt: string;
  actorUserId: UUID | null;
  actorAgentId: UUID | null;
  actorCustomerId: UUID | null;
  caseId: UUID | null;
  conversationId: UUID | null;
  details: Record<string, unknown> | null;
};

export type CustomerCaseLink = {
  id: UUID;
  title: string;
  status: string;
  priority: string;
  openedAt: string;
};

export type CustomerDetail = {
  id: UUID;
  businessId: UUID;
  fullName: string;
  primaryEmail: string | null;
  primaryPhone: string | null;
  country: string | null;
  lifecycleStage: string;
  satisfactionScore: number | null;
  personaTags: string[];
  lastContactAt: string | null;
  createdAt: string;
  updatedAt: string;
  stats: CustomerStats;
  contacts: CustomerContactMethod[];
  tags: CustomerTag[];
  casesOpen: CustomerCaseLink[];
  casesResolved: CustomerCaseLink[];
  notes: CustomerNote[];
  activity: CustomerActivityEvent[];
};

export type CustomerDetailResponse = {
  customer: CustomerDetail;
};

export type CustomerDetailInclude = Array<"notes" | "activity" | "cases">;

export type CustomersListParams = {
  search?: string;
  lifecycleStage?: string;
  tags?: string[];
  dateFrom?: string;
  dateTo?: string;
  limit?: number;
  cursor?: string | null;
};

export type CustomerCreateRequest = {
  fullName: string;
  primaryEmail?: string | null;
  primaryPhone?: string | null;
  country?: string | null;
  lifecycleStage: string;
  satisfactionScore?: number | null;
  personaTags?: string[];
  contactMethods?: CustomerContactMethod[];
  tagIds?: UUID[];
};

export type CustomerUpdateRequest = Partial<CustomerCreateRequest>;

export type CustomerNoteCreateRequest = {
  body: string;
  visibility: "internal" | "shared";
  pinned?: boolean;
};

export type CustomerNoteUpdateRequest = {
  body?: string;
  visibility?: "internal" | "shared";
  pinned?: boolean;
};

export type CustomerImportRow = {
  fullName: string;
  email?: string | null;
  phone?: string | null;
  country?: string | null;
  lifecycleStage?: string | null;
  tags?: string[];
};

export type CustomerImportRequest = {
  rows: CustomerImportRow[];
  skipDuplicates?: boolean;
};

export type CustomerImportResult = {
  importedCount: number;
  skippedCount: number;
  errors: string[];
};

export type CustomerNotesResponse = CursorResponse & {
  items: CustomerNote[];
};

export type CustomerActivityResponse = CursorResponse & {
  items: CustomerActivityEvent[];
};

const buildQuery = (params: CustomersListParams | undefined) => {
  if (!params) return "";
  const searchParams = new URLSearchParams();
  if (params.search) searchParams.set("search", params.search);
  if (params.lifecycleStage) searchParams.set("lifecycle_stage", params.lifecycleStage);
  if (params.dateFrom) searchParams.set("date_from", params.dateFrom);
  if (params.dateTo) searchParams.set("date_to", params.dateTo);
  if (typeof params.limit === "number") searchParams.set("limit", String(params.limit));
  if (params.cursor) searchParams.set("cursor", params.cursor);
  if (Array.isArray(params.tags)) {
    params.tags.filter(Boolean).forEach((tag) => searchParams.append("tags", tag));
  }
  const query = searchParams.toString();
  return query ? `?${query}` : "";
};

const withAuth = (token?: string | null) => token ?? getStoredToken();

export async function listCustomers(
  params?: CustomersListParams,
  options: RequestOptions = {}
): Promise<CustomersListResponse> {
  const query = buildQuery(params);
  try {
    return await jsonFetch<CustomersListResponse>(`/v1/customers${query}`, {
      method: "GET",
      token: withAuth(options.token),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof ApiError && (error.code === "configuration_error" || error.code === "network_error")) {
      if (import.meta.env?.DEV) {
        console.warn("[customers] Falling back to empty list due to API error", error.message);
      }
      return {
        items: [],
        total: 0,
        hasNext: false,
        nextCursor: null,
      };
    }
    throw error;
  }
}

export async function getCustomerDetail(
  customerId: UUID,
  include?: CustomerDetailInclude,
  options: RequestOptions = {}
): Promise<CustomerDetailResponse> {
  const searchParams = new URLSearchParams();
  include?.forEach((value) => searchParams.append("include", value));
  const suffix = searchParams.size ? `?${searchParams.toString()}` : "";
  return jsonFetch<CustomerDetailResponse>(`/v1/customers/${customerId}${suffix}`, {
    method: "GET",
    token: withAuth(options.token),
    signal: options.signal,
  });
}

export async function createCustomer(
  payload: CustomerCreateRequest,
  options: RequestOptions = {}
): Promise<CustomerDetailResponse> {
  return jsonFetch<CustomerDetailResponse>("/v1/customers", {
    method: "POST",
    body: payload,
    token: withAuth(options.token),
    signal: options.signal,
  });
}

export async function updateCustomer(
  customerId: UUID,
  payload: CustomerUpdateRequest,
  options: RequestOptions = {}
): Promise<CustomerDetailResponse> {
  return jsonFetch<CustomerDetailResponse>(`/v1/customers/${customerId}`, {
    method: "PATCH",
    body: payload,
    token: withAuth(options.token),
    signal: options.signal,
  });
}

export async function listCustomerNotes(
  customerId: UUID,
  params?: { limit?: number; cursor?: string | null },
  options: RequestOptions = {}
): Promise<CustomerNotesResponse> {
  const searchParams = new URLSearchParams();
  if (typeof params?.limit === "number") searchParams.set("limit", String(params.limit));
  if (params?.cursor) searchParams.set("cursor", params.cursor);
  const suffix = searchParams.size ? `?${searchParams.toString()}` : "";
  return jsonFetch<CustomerNotesResponse>(`/v1/customers/${customerId}/notes${suffix}`, {
    method: "GET",
    token: withAuth(options.token),
    signal: options.signal,
  });
}

export async function createCustomerNote(
  customerId: UUID,
  payload: CustomerNoteCreateRequest,
  options: RequestOptions = {}
): Promise<CustomerNote> {
  return jsonFetch<CustomerNote>(`/v1/customers/${customerId}/notes`, {
    method: "POST",
    body: payload,
    token: withAuth(options.token),
    signal: options.signal,
  });
}

export async function updateCustomerNote(
  customerId: UUID,
  noteId: UUID,
  payload: CustomerNoteUpdateRequest,
  options: RequestOptions = {}
): Promise<CustomerNote> {
  return jsonFetch<CustomerNote>(`/v1/customers/${customerId}/notes/${noteId}`, {
    method: "PATCH",
    body: payload,
    token: withAuth(options.token),
    signal: options.signal,
  });
}

export async function listCustomerActivity(
  customerId: UUID,
  params?: { limit?: number; cursor?: string | null },
  options: RequestOptions = {}
): Promise<CustomerActivityResponse> {
  const searchParams = new URLSearchParams();
  if (typeof params?.limit === "number") searchParams.set("limit", String(params.limit));
  if (params?.cursor) searchParams.set("cursor", params.cursor);
  const suffix = searchParams.size ? `?${searchParams.toString()}` : "";
  return jsonFetch<CustomerActivityResponse>(`/v1/customers/${customerId}/activity${suffix}`, {
    method: "GET",
    token: withAuth(options.token),
    signal: options.signal,
  });
}

export async function importCustomers(
  payload: CustomerImportRequest,
  options: RequestOptions = {}
): Promise<CustomerImportResult> {
  return jsonFetch<CustomerImportResult>("/v1/customers/import", {
    method: "POST",
    body: payload,
    token: withAuth(options.token),
    signal: options.signal,
  });
}
