import { ApiError, jsonFetch } from "./http";

export type SessionProgress = {
  id: string;
  currentStep: string;
  stepsCompleted: number;
  totalSteps: number;
};

export type StartRegistrationPayload = {
  firstName: string;
  email: string;
  password?: string | null;
};

export type StartRegistrationResponse = {
  registrationId: string;
  user: {
    id: string;
    email: string;
    firstName: string;
  };
  nextStep: string;
};

export type BusinessProfilePayload = {
  businessName: string;
  industry: string;
  specifyIndustry?: string;
  lineOfBusiness: string[];
  lineOfBusinessCustom: string[];
  country?: string;
  website?: string;
};

export type BusinessProfileResponse = {
  business: {
    id: string;
    name: string;
    industryCode: string;
  };
  niches: string[];
  session: SessionProgress;
};

export type AgentConfigPayload = {
  agentName?: string | null;
  agentTitle?: string | null;
  agentTone?: string | null;
  agentTraits: string[];
  agentEscalation?: string | null;
};

export type AgentConfigResponse = {
  agent?: {
    id: string;
    name: string;
    role: string;
    tone: string;
    traits: string[];
    escalationRule: string;
  } | null;
  session: SessionProgress;
};

export type UploadLinksPayload = {
  links: Record<string, string[]>;
  language?: string | null;
};

export type UploadLinksResponse = {
  created: Record<string, number>;
  duplicates: number;
  session: SessionProgress;
};

export type CompletionResponse = {
  session: SessionProgress;
  progress: Record<string, unknown>;
};

export type RequestOptions = {
  token?: string | null;
  idempotencyKey?: string | null;
  captchaToken?: string | null;
  signal?: AbortSignal;
};

export async function startRegistration(
  payload: StartRegistrationPayload,
  options: RequestOptions = {}
): Promise<StartRegistrationResponse> {
  return jsonFetch<StartRegistrationResponse>("/v1/registration/sessions", {
    method: "POST",
    body: payload,
    idempotencyKey: options.idempotencyKey || null,
    captchaToken: options.captchaToken || null,
  });
}

export async function upsertBusiness(
  registrationId: string,
  payload: BusinessProfilePayload,
  options: RequestOptions = {}
): Promise<BusinessProfileResponse> {
  return jsonFetch<BusinessProfileResponse>(`/v1/registration/sessions/${registrationId}/business`, {
    method: "PUT",
    body: payload,
    idempotencyKey: options.idempotencyKey || null,
    token: options.token || null,
  });
}

export async function configureAgent(
  businessId: string,
  payload: AgentConfigPayload,
  options: RequestOptions = {}
): Promise<AgentConfigResponse> {
  return jsonFetch<AgentConfigResponse>(`/v1/registration/businesses/${businessId}/agent`, {
    method: "PUT",
    body: payload,
    idempotencyKey: options.idempotencyKey || null,
    token: options.token || null,
  });
}

export async function attachUploadLinks(
  businessId: string,
  payload: UploadLinksPayload,
  options: RequestOptions = {}
): Promise<UploadLinksResponse> {
  return jsonFetch<UploadLinksResponse>(`/v1/registration/businesses/${businessId}/uploads`, {
    method: "POST",
    body: payload,
    idempotencyKey: options.idempotencyKey || null,
    token: options.token || null,
  });
}

export async function completeRegistration(
  registrationId: string,
  options: RequestOptions = {}
): Promise<CompletionResponse> {
  return jsonFetch<CompletionResponse>(`/v1/registration/sessions/${registrationId}/complete`, {
    method: "POST",
    token: options.token || null,
  });
}

export type { ApiError };
