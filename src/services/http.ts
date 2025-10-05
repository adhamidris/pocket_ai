const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const IS_DEV = Boolean(import.meta.env?.DEV);

export class ApiError extends Error {
  status: number;
  code: string;
  details?: Record<string, unknown> | null;

  constructor(status: number, code: string, message?: string, details?: Record<string, unknown> | null) {
    super(message || code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export type JsonFetchOptions = {
  method?: string;
  body?: unknown;
  token?: string | null;
  idempotencyKey?: string | null;
  captchaToken?: string | null;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  requestId?: string;
};

const fallbackRequestId = () => {
  const rnd = Math.random().toString(16).slice(2);
  const time = Date.now().toString(16);
  return `${time}-${rnd}`;
};

const buildUrl = (path: string) => {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  if (!API_BASE_URL) return path;
  return `${API_BASE_URL}${path.startsWith("/") ? "" : "/"}${path}`;
};

export async function jsonFetch<T>(path: string, options: JsonFetchOptions = {}): Promise<T> {
  const {
    method = "GET",
    body,
    token,
    idempotencyKey,
    captchaToken,
    headers = {},
    signal,
    requestId,
  } = options;

  const finalHeaders = new Headers(headers);
  finalHeaders.set("Accept", "application/json");
  if (body !== undefined && body !== null && !finalHeaders.has("Content-Type")) {
    finalHeaders.set("Content-Type", "application/json");
  }
  const generatedId = requestId || (typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : fallbackRequestId());
  finalHeaders.set("X-Request-ID", generatedId);
  if (token) finalHeaders.set("Authorization", token.startsWith("Bearer ") ? token : `Bearer ${token}`);
  if (idempotencyKey) finalHeaders.set("Idempotency-Key", idempotencyKey);
  if (captchaToken) finalHeaders.set("X-Captcha-Token", captchaToken);

  const init: RequestInit = {
    method,
    headers: finalHeaders,
    signal,
  };

  if (body !== undefined && body !== null) {
    init.body = typeof body === "string" ? body : JSON.stringify(body);
  }

  const url = buildUrl(path);
  
  // ADDED: Better URL validation and debugging
  if (IS_DEV) {
    console.debug(`[api] Preparing request: ${method} ${url}`, {
      path,
      builtUrl: url,
      apiBaseUrl: API_BASE_URL,
      hasBody: body !== undefined && body !== null
    });
  }

  // ADDED: Validate URL before making request
  if (!url || url === path) {
    const errorMessage = `Invalid API URL configuration. Path: "${path}", Built URL: "${url}", API_BASE_URL: "${API_BASE_URL}"`;
    console.error('[api] URL Configuration Error:', errorMessage);
    throw new ApiError(0, "configuration_error", errorMessage);
  }

  const started = typeof performance !== "undefined" ? performance.now() : Date.now();
  
  try {
    const response = await fetch(url, init);
    
    // ADDED: Handle network errors that don't get proper response
    if (response.status === 0 || response.type === 'error') {
      const networkError = new ApiError(0, "network_error", 
        `Cannot connect to backend server at ${url}. Please ensure the backend is running on port 8000.`);
      if (IS_DEV) {
        console.error('[api] Network connection failed:', {
          url,
          status: response.status,
          type: response.type,
          statusText: response.statusText
        });
      }
      throw networkError;
    }
    
    const text = await response.text();
    const parseJson = () => {
      if (!text) return null;
      try {
        return JSON.parse(text) as Record<string, unknown>;
      } catch {
        return null;
      }
    };

    const durationMs = (typeof performance !== "undefined" ? performance.now() : Date.now()) - started;
    
    if (!response.ok) {
      const payload = parseJson();
      const code = typeof payload?.code === "string" ? payload.code : `http_${response.status}`;
      const message = typeof payload?.message === "string" ? payload.message : response.statusText;
      const details = (payload?.details && typeof payload.details === "object") ? (payload.details as Record<string, unknown>) : null;
      
      if (IS_DEV) {
        console.warn(
          `[api] ${method} ${path} -> ${response.status} (${Math.round(durationMs)}ms)`,
          { requestId: generatedId, code, message, details, url }
        );
      }
      throw new ApiError(response.status, code, message, details);
    }

    if (!text) {
      if (IS_DEV) {
        console.debug(`[api] ${method} ${path} -> ${response.status} (${Math.round(durationMs)}ms)`, {
          requestId: generatedId,
        });
      }
      return undefined as T;
    }

    const json = parseJson();
    if (json === null) {
      if (IS_DEV) {
        console.debug(`[api] ${method} ${path} -> ${response.status} (${Math.round(durationMs)}ms)`, {
          requestId: generatedId,
          raw: text,
        });
      }
      return text as unknown as T;
    }
    
    if (IS_DEV) {
      console.debug(`[api] ${method} ${path} -> ${response.status} (${Math.round(durationMs)}ms)`, {
        requestId: generatedId,
        body: json,
      });
    }
    return json as T;
    
  } catch (error) {
    // ADDED: Catch network errors that occur before response
    if (error instanceof TypeError) {
      if (error.message.includes('Failed to fetch') || error.message.includes('NetworkError')) {
        const networkError = new ApiError(0, "network_error", 
          `Cannot connect to backend server at ${url}. Please ensure:\n1. Backend is running on port 8000\n2. No firewall is blocking the connection\n3. The server address is correct`);
        if (IS_DEV) {
          console.error('[api] Fetch failed completely:', {
            url,
            error: error.message,
            apiBaseUrl: API_BASE_URL
          });
        }
        throw networkError;
      }
    }
    
    // Re-throw if it's already an ApiError
    if (error instanceof ApiError) {
      throw error;
    }
    
    // Wrap any other errors
    throw new ApiError(0, "unknown_error", `Unexpected error: ${error instanceof Error ? error.message : String(error)}`);
  }
}

export const apiBaseUrl = API_BASE_URL;
