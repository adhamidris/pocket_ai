import { jsonFetch } from "./http";

export type IntegrationStartResponse = {
  integrationId: string;
  authorizationUrl: string;
  state: string;
};

export type ColumnPrivacyConfig = {
  sharedColumns: string[];
  internalOnlyColumns: string[];
  excludedColumns: string[];
};

export type GoogleSheetResource = {
  resourceId: string;
  driveFileId: string;
  driveFileName: string;
  sheetGid: string;
  sheetName: string;
  rowCount?: number;
  columnCount?: number;
  modifiedAt?: string;
  owner?: string;
  ownerEmail?: string;
  webViewLink?: string;
  selected: boolean;
  visibility: string;
  syncFrequency: string;
  columnPrivacy: ColumnPrivacyConfig;
  lastSyncedAt?: string;
  lastSyncStatus?: string;
  lastSyncError?: string;
};

export type GoogleResourcesResponse = {
  integration: {
    id: string;
    name: string;
    status: string;
    lastSyncedAt?: string;
  };
  defaultVisibility: string;
  defaultSyncFrequency: string;
  availableResources: GoogleSheetResource[];
  selectedResources: GoogleSheetResource[];
};

export type SaveGoogleResourcesPayload = {
  resources: Array<{
    resourceId?: string;
    driveFileId: string;
    sheetGid: string;
    sheetName: string;
    driveFileName?: string;
    visibility?: string;
    syncFrequency?: string;
    columnPrivacy?: Partial<ColumnPrivacyConfig> & {
      sharedColumns?: string[] | string;
      internalOnlyColumns?: string[] | string;
      excludedColumns?: string[] | string;
    };
    metadata?: Record<string, unknown>;
  }>;
  defaultVisibility?: string;
  defaultSyncFrequency?: string;
};

export type IntegrationSummary = {
  id: string;
  name: string;
  type: string;
  status: string;
  lastSyncedAt?: string;
  nextSyncAt?: string | null;
  resourceCount: number;
  syncError?: string;
  defaultVisibility: string;
  defaultSyncFrequency: string;
  metrics?: {
    resourcesAttempted?: number;
    successCount?: number;
    failureCount?: number;
    bytesWritten?: number;
    rowsIngested?: number;
    durationMs?: number;
    lastRunAt?: string;
  };
  schedule?: {
    frequency?: string;
    nextRunAt?: string | null;
    lastRunAt?: string | null;
    status?: string;
    paused?: boolean;
  };
  staleResourceCount?: number;
  staleResources?: Array<{
    resourceId?: string;
    driveFileName?: string;
    sheetName?: string;
    staleSince?: string;
    reason?: string;
  }>;
  hasCredentials: boolean;
  actions: Record<string, string>;
  account?: {
    email?: string;
    name?: string;
    linkedAt?: string;
  } | null;
};

export type IntegrationProvider = {
  type: string;
  label: string;
  description: string;
  status: "available" | "coming_soon" | string;
  connectUrl: string;
  requiresOAuth: boolean;
  supportsSheets?: boolean;
};

export type IntegrationsResponse = {
  businessId: string;
  integrations: IntegrationSummary[];
  providers: IntegrationProvider[];
  stats: {
    total: number;
    connected: number;
    errors: number;
    syncing: number;
  };
  dashboardUrl: string;
};

export async function startGoogleDriveOAuth(businessId?: string): Promise<IntegrationStartResponse> {
  const body: Record<string, unknown> = {};
  if (businessId) {
    body.businessId = businessId;
  }
  return jsonFetch<IntegrationStartResponse>("/api/integrations/google/start/", {
    method: "POST",
    body,
  });
}

export async function fetchIntegrations(params: { businessId?: string } = {}): Promise<IntegrationsResponse> {
  const query = new URLSearchParams();
  if (params.businessId) {
    query.set("business_id", params.businessId);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return jsonFetch<IntegrationsResponse>(`/api/integrations/${suffix}`);
}

export async function createIntegration(payload: { businessId?: string; name?: string; type: string }) {
  const response = await jsonFetch<{ integration: IntegrationSummary }>("/api/integrations/", {
    method: "POST",
    body: payload,
  });
  return response.integration;
}

export async function fetchIntegrationSheets(params: {
  integrationId: string;
  limit?: number;
  search?: string;
  businessId?: string;
}): Promise<GoogleResourcesResponse> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.search) query.set("q", params.search);
  if (params.businessId) query.set("business_id", params.businessId);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return jsonFetch<GoogleResourcesResponse>(`/api/integrations/${params.integrationId}/sheets/${suffix}`);
}

export async function fetchGoogleDriveResources(params: {
  integrationId: string;
  limit?: number;
  search?: string;
  businessId?: string;
}): Promise<GoogleResourcesResponse> {
  return fetchIntegrationSheets(params);
}

export async function saveGoogleDriveResources(
  integrationId: string,
  payload: SaveGoogleResourcesPayload,
  options: { businessId?: string } = {},
): Promise<GoogleResourcesResponse> {
  const query = new URLSearchParams();
  if (options.businessId) query.set("business_id", options.businessId);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return jsonFetch<GoogleResourcesResponse>(`/api/integrations/${integrationId}/sheets/${suffix}`, {
    method: "POST",
    body: payload,
  });
}

export async function syncGoogleDriveNow(params: { integrationId: string; resourceIds?: string[] }) {
  return jsonFetch<{
    integrationId: string;
    provider: string;
    status: string;
    message?: string;
    nextSyncAt?: string | null;
    rowsIngested: number;
    resources: Array<{
      resourceId: string;
      status: string;
      uploadId?: string;
      bytesWritten: number;
      rowsIngested: number;
      jobId?: string;
      message?: string;
    }>;
  }>("/api/integrations/google/sync/", {
    method: "POST",
    body: params,
  });
}
