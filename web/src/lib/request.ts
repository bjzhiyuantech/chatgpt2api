import axios, {AxiosError, type AxiosRequestConfig} from "axios";

import webConfig from "@/constants/common-env";
import {clearStoredAuthKey, getStoredAuthKey} from "@/store/auth";

type RequestConfig = AxiosRequestConfig & {
    redirectOnUnauthorized?: boolean;
};

const request = axios.create({
    baseURL: webConfig.apiUrl.replace(/\/$/, ""),
});

// Track whether a 401 redirect is already in progress to prevent multiple redirects
let isRedirecting = false;

request.interceptors.request.use(async (config) => {
    // Block all requests if we're already redirecting to login
    if (isRedirecting) {
        return Promise.reject(new axios.Cancel("redirecting to login"));
    }
    const nextConfig = {...config};
    const authKey = await getStoredAuthKey();
    const headers = {...(nextConfig.headers || {})} as Record<string, string>;
    if (authKey && !headers.Authorization) {
        headers.Authorization = `Bearer ${authKey}`;
    }
    // eslint-disable-next-line @typescript-eslint/ban-ts-comment
    // @ts-expect-error
    nextConfig.headers = headers;
    return nextConfig;
});

request.interceptors.response.use(
    (response) => response,
    async (error: AxiosError<{ detail?: { error?: string }; error?: string; message?: string }>) => {
        // Silently swallow cancelled requests during redirect
        if (axios.isCancel(error)) {
            return new Promise(() => {});
        }

        const status = error.response?.status;
        const shouldRedirect = (error.config as RequestConfig | undefined)?.redirectOnUnauthorized !== false;

        if (status === 401 && shouldRedirect && typeof window !== "undefined") {
            if (!isRedirecting && !window.location.pathname.startsWith("/login")) {
                isRedirecting = true;
                // Fire-and-forget — don't await to avoid race conditions
                clearStoredAuthKey().catch(() => {});
                window.location.replace("/login");
            }
            // Always return a pending promise for 401 — never let it reach catch handlers
            return new Promise(() => {});
        }

        const payload = error.response?.data;
        const message =
            payload?.detail?.error ||
            payload?.error ||
            payload?.message ||
            error.message ||
            `请求失败 (${status || 500})`;
        return Promise.reject(new Error(message));
    },
);

type RequestOptions = {
    method?: string;
    body?: unknown;
    headers?: Record<string, string>;
    redirectOnUnauthorized?: boolean;
};

export async function httpRequest<T>(path: string, options: RequestOptions = {}) {
    const {method = "GET", body, headers, redirectOnUnauthorized = true} = options;
    const config: RequestConfig = {
        url: path,
        method,
        data: body,
        headers,
        redirectOnUnauthorized,
    };
    const response = await request.request<T>(config);
    return response.data;
}
