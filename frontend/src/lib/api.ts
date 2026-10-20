/**
 * Thin typed client for the Copperbelt Intelligence API.
 * The access token lives only in memory; the refresh token is an httpOnly cookie
 * the browser sends to /api/v1/auth/refresh.
 */

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

let accessToken: string | null = null;
let orgId: string | null = null;

export function setSession(token: string | null, org: string | null) {
  accessToken = token;
  orgId = org;
}
export function setOrgId(org: string | null) {
  orgId = org;
}
export function currentOrg() {
  return orgId;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public detail?: unknown,
    public requestId?: string,
  ) {
    super(message);
  }
}

async function refresh(): Promise<boolean> {
  const r = await fetch(`${API_URL}/api/v1/auth/refresh`, {
    method: "POST",
    credentials: "include",
    headers: { "X-Requested-With": "copperbelt" },
  });
  if (!r.ok) return false;
  accessToken = (await r.json()).access_token;
  return true;
}

export async function restoreSession(): Promise<boolean> {
  try {
    return await refresh();
  } catch {
    return false;
  }
}

type Opts = { method?: string; body?: unknown; form?: FormData; raw?: boolean };

export async function api<T = unknown>(path: string, opts: Opts = {}, retried = false): Promise<T> {
  const headers: Record<string, string> = {};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (orgId) headers["X-Org-Id"] = orgId;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const r = await fetch(`${API_URL}/api/v1${path}`, {
    method: opts.method ?? (body ? "POST" : "GET"),
    headers,
    body,
    credentials: "include",
  });
  if (r.status === 401 && !retried && !path.startsWith("/auth/")) {
    if (await refresh()) return api<T>(path, opts, true);
  }
  if (!r.ok) {
    let err: { code?: string; message?: string; detail?: unknown; request_id?: string } = {};
    try {
      err = (await r.json()).error ?? {};
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(r.status, err.code ?? "http_error", err.message ?? `Request failed (${r.status})`, err.detail, err.request_id);
  }
  if (opts.raw) return r as unknown as T;
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

/** Fetch an authenticated binary (page image) as an object URL. */
export async function blobUrl(path: string): Promise<string> {
  const r = await api<Response>(path, { raw: true });
  return URL.createObjectURL(await r.blob());
}

export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    const ref = e.requestId ? ` (reference ${e.requestId})` : "";
    if (e.code === "validation_failed" && Array.isArray(e.detail)) {
      const first = e.detail[0] as { loc?: unknown[]; msg?: string };
      return `${first?.loc?.slice(-1)[0] ?? "Input"}: ${first?.msg ?? e.message}`;
    }
    return e.message + ref;
  }
  if (e instanceof TypeError && /fetch|network/i.test(e.message)) return "The server could not be reached. Check that the API is running.";
  if (e instanceof Error) return `Something went wrong in the page: ${e.message}`;
  return String(e);
}

// ---------------------------------------------------------------- types

export interface Membership { org_id: string; org_name: string; role: string }
export interface Me { id: string; email: string; full_name: string; memberships: Membership[] }
export interface Source { id: string; name: string; institution: string | null; access_status: string; licence: string | null; country: string | null }
export interface Doc {
  id: string; title: string; status: string; page_count: number | null; classification: string; publication_year: number | null;
  original_filename: string; content_type: string; size_bytes: number; sha256: string; malware_scan: string;
  processing_error: string | null; processing_version: string | null; created_at: string; source_id: string; language: string | null;
  metadata: Record<string, unknown>;
}
export interface Page { page_number: number; text: string; extraction_method: string; ocr_confidence: number | null; detected_language: string | null; processing_version: string }
export interface Claim {
  id: string; document_id: string | null; page_number: number | null; char_start: number | null; char_end: number | null;
  claim_type: string; statement: string; commodities: string[]; attributes: Record<string, unknown>;
  evidence_basis: string; extraction_method: string; extractor_version: string; confidence: number | null; review_status: string;
}
export interface Feature {
  id: string; name: string; feature_type: string; commodities: string[]; geometry: GeoJSON.Geometry | null;
  location_precision: string; uncertainty_m: number | null; review_status: string; warnings: string[];
  evidence_counts: Record<string, number>; original_coordinates: string | null; source_crs: string | null;
}
export interface Job { id: string; kind: string; status: string; attempts: number; max_attempts: number; progress: number; progress_message: string | null; last_error: string | null; created_at: string }
export interface Project { id: string; name: string; description: string | null; country: string | null; commodities: string[]; stage: string; my_role: string | null }
export interface Listing<T> { items: T[]; total: number; limit: number; offset: number }
