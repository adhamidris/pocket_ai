import { ApiError, jsonFetch } from "./http";

const TOKEN_STORAGE_KEY = "pocket_ai_access_token";

type LoginResponse = {
  accessToken?: string;
  token?: string;
  refreshToken?: string;
  tokenType?: string;
  expiresIn?: number;
  userId?: string;
  email?: string;
  firstName?: string;
};

export const getStoredToken = (): string | null => {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
};

export const storeToken = (token: string) => {
  try {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    /* ignore */
  }
};

export const clearToken = () => {
  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* ignore */
  }
};

const devToken = () => import.meta.env.VITE_DEV_BEARER || null;

export async function loginWithPassword(email: string, password: string): Promise<string | null> {
  if (!email || !password) {
    return devToken();
  }

  try {
    const response = await jsonFetch<LoginResponse>("/v1/auth/login", {
      method: "POST",
      body: { email, password },
    });
    const token = response?.accessToken || response?.token || null;
    if (token) {
      storeToken(token);
      return token;
    }
    return devToken();
  } catch (error) {
    if (error instanceof ApiError) {
      console.warn("Login request failed", error.status, error.code);
    } else {
      console.warn("Login request failed", error);
    }
    const fallback = devToken();
    if (fallback) return fallback;
    throw error;
  }
}
