import { httpRequest } from "@/lib/request";

export type AccountType = "Free" | "Plus" | "Pro" | "Team";
export type AccountStatus = "正常" | "限流" | "异常" | "禁用";
export type ImageModel = "gpt-image-1" | "gpt-image-2";

export type Account = {
  id: string;
  access_token: string;
  type: AccountType;
  status: AccountStatus;
  quota: number;
  email?: string | null;
  user_id?: string | null;
  limits_progress?: Array<{
    feature_name?: string;
    remaining?: number;
    reset_after?: string;
  }>;
  default_model_slug?: string | null;
  restoreAt?: string | null;
  success: number;
  fail: number;
  lastUsedAt: string | null;
};

type AccountListResponse = {
  items: Account[];
};

type AccountMutationResponse = {
  items: Account[];
  added?: number;
  skipped?: number;
  removed?: number;
  refreshed?: number;
  errors?: Array<{ access_token: string; error: string }>;
};

type AccountRefreshResponse = {
  items: Account[];
  refreshed: number;
  errors: Array<{ access_token: string; error: string }>;
};

type AccountUpdateResponse = {
  item: Account;
  items: Account[];
};

export async function login(authKey: string) {
  const normalizedAuthKey = String(authKey || "").trim();
  return httpRequest<{ ok: boolean }>("/auth/login", {
    method: "POST",
    body: {},
    headers: {
      Authorization: `Bearer ${normalizedAuthKey}`,
    },
    redirectOnUnauthorized: false,
  });
}

export async function fetchAccounts() {
  return httpRequest<AccountListResponse>("/api/accounts");
}

export async function createAccounts(tokens: string[]) {
  return httpRequest<AccountMutationResponse>("/api/accounts", {
    method: "POST",
    body: { tokens },
  });
}

export async function deleteAccounts(tokens: string[]) {
  return httpRequest<AccountMutationResponse>("/api/accounts", {
    method: "DELETE",
    body: { tokens },
  });
}

export async function refreshAccounts(accessTokens: string[]) {
  return httpRequest<AccountRefreshResponse>("/api/accounts/refresh", {
    method: "POST",
    body: { access_tokens: accessTokens },
  });
}

export async function updateAccount(
  accessToken: string,
  updates: {
    type?: AccountType;
    status?: AccountStatus;
    quota?: number;
  },
) {
  return httpRequest<AccountUpdateResponse>("/api/accounts/update", {
    method: "POST",
    body: {
      access_token: accessToken,
      ...updates,
    },
  });
}

export type ImageGenerationResponse = {
  created: number;
  data?: Array<{ b64_json: string; revised_prompt?: string }>;
  task_id?: string;
  status?: string;
  message?: string;
};

export async function generateImage(prompt: string, model: ImageModel = "gpt-image-1") {
  return httpRequest<ImageGenerationResponse>(
    "/v1/images/generations",
    {
      method: "POST",
      body: {
        prompt,
        model,
        n: 1,
        response_format: "b64_json",
      },
    },
  );
}

export async function editImage(prompt: string, imageFiles: File[], model: ImageModel = "gpt-image-1") {
  const formData = new FormData();
  for (const file of imageFiles) {
    formData.append("image", file);
  }
  formData.append("prompt", prompt);
  formData.append("model", model);
  formData.append("n", "1");
  formData.append("response_format", "b64_json");

  return httpRequest<ImageGenerationResponse>(
    "/v1/images/edits",
    {
      method: "POST",
      body: formData,
    },
  );
}

export type ImageTaskResponse = {
  task_id: string;
  status: string;
  prompt: string;
  model: string;
  created_at: number;
  updated_at: number;
  result?: { created: number; data: Array<{ b64_json: string; revised_prompt?: string }> };
  error?: string;
};

export async function fetchImageTask(taskId: string) {
  return httpRequest<ImageTaskResponse>(`/v1/images/tasks/${taskId}`);
}

// ── CPA (CLIProxyAPI) ──────────────────────────────────────────────

export type CPAPool = {
  id: string;
  name: string;
  base_url: string;
  secret_key: string;
  enabled: boolean;
};

export type CPAStatus = {
  enabled: boolean;
  pools: number;
  tokens: number;
};

export async function fetchCPAPools() {
  return httpRequest<{ pools: CPAPool[] }>("/api/cpa/pools");
}

export async function createCPAPool(pool: { name: string; base_url: string; secret_key: string; enabled?: boolean }) {
  return httpRequest<{ pool: CPAPool; pools: CPAPool[] }>("/api/cpa/pools", {
    method: "POST",
    body: pool,
  });
}

export async function updateCPAPool(
  poolId: string,
  updates: { name?: string; base_url?: string; secret_key?: string; enabled?: boolean },
) {
  return httpRequest<{ pool: CPAPool; pools: CPAPool[] }>(`/api/cpa/pools/${poolId}`, {
    method: "POST",
    body: updates,
  });
}

export async function deleteCPAPool(poolId: string) {
  return httpRequest<{ pools: CPAPool[] }>(`/api/cpa/pools/${poolId}`, {
    method: "DELETE",
  });
}

export async function fetchCPAPoolStatus(poolId: string) {
  return httpRequest<{ pool_id: string; tokens: number }>(`/api/cpa/pools/${poolId}/status`);
}

export async function syncCPAPool(poolId: string) {
  return httpRequest<{
    added: number;
    skipped: number;
    refreshed: number;
    errors: Array<{ access_token: string; error: string }>;
    items: Account[];
  }>(`/api/cpa/pools/${poolId}/sync`, { method: "POST" });
}

export async function fetchCPAGlobalStatus() {
  return httpRequest<CPAStatus>("/api/cpa/status");
}

// ── Proxy config ───────────────────────────────────────────────────

export type ProxyConfig = {
  proxy_url: string;
  enabled: boolean;
};

export async function fetchProxyConfig() {
  return httpRequest<ProxyConfig>("/api/proxy/config");
}

export async function updateProxyConfig(proxyUrl: string) {
  return httpRequest<ProxyConfig>("/api/proxy/config", {
    method: "POST",
    body: { proxy_url: proxyUrl },
  });
}
