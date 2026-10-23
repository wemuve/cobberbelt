import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL, ApiError, api, describeError, setSession } from "../api";

type Call = { url: string; init: RequestInit };
let calls: Call[];
let responses: Response[];

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  calls = [];
  responses = [];
  setSession(null, null);
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const next = responses.shift();
    if (!next) throw new Error(`unexpected fetch ${url}`);
    return next;
  });
});
afterEach(() => vi.unstubAllGlobals());

const headers = (c: Call) => c.init.headers as Record<string, string>;

describe("api", () => {
  it("sends the bearer token and organisation on every request", async () => {
    setSession("tok-1", "org-1");
    responses.push(json(200, { ok: true }));
    expect(await api("/documents")).toEqual({ ok: true });
    expect(calls[0].url).toBe(`${API_URL}/api/v1/documents`);
    expect(calls[0].init.method).toBe("GET");
    expect(headers(calls[0])).toEqual({ Authorization: "Bearer tok-1", "X-Org-Id": "org-1" });
    expect(calls[0].init.credentials).toBe("include");
  });

  it("posts JSON bodies and leaves FormData to the browser", async () => {
    responses.push(json(201, { id: "p1" }), json(201, { id: "d1" }));
    await api("/projects", { body: { name: "Kambove" } });
    expect(calls[0].init.method).toBe("POST");
    expect(headers(calls[0])["Content-Type"]).toBe("application/json");
    expect(calls[0].init.body).toBe('{"name":"Kambove"}');

    const form = new FormData();
    form.set("title", "Carte");
    await api("/documents", { form, method: "PUT" });
    expect(calls[1].init.method).toBe("PUT");
    expect(headers(calls[1])["Content-Type"]).toBeUndefined();
    expect(calls[1].init.body).toBe(form);
  });

  it("refreshes an expired access token once and retries with the new one", async () => {
    setSession("expired", "org-1");
    responses.push(json(401, {}), json(200, { access_token: "fresh" }), json(200, { items: [] }));
    expect(await api("/projects")).toEqual({ items: [] });
    expect(calls.map((c) => c.url)).toEqual([
      `${API_URL}/api/v1/projects`,
      `${API_URL}/api/v1/auth/refresh`,
      `${API_URL}/api/v1/projects`,
    ]);
    expect(headers(calls[1])["X-Requested-With"]).toBe("copperbelt");
    expect(headers(calls[2]).Authorization).toBe("Bearer fresh");
  });

  it("does not loop when the retried request is also unauthorised", async () => {
    setSession("expired", null);
    responses.push(json(401, {}), json(200, { access_token: "fresh" }), json(401, { error: { code: "token_revoked", message: "Session revoked" } }));
    await expect(api("/projects")).rejects.toMatchObject({ status: 401, code: "token_revoked" });
    expect(calls).toHaveLength(3);
  });

  it("never tries to refresh for auth endpoints", async () => {
    responses.push(json(401, { error: { code: "invalid_credentials", message: "Email or password is incorrect" } }));
    await expect(api("/auth/login", { body: {} })).rejects.toMatchObject({ code: "invalid_credentials" });
    expect(calls).toHaveLength(1);
  });

  it("maps the API error envelope, including the request id", async () => {
    responses.push(json(409, { error: { code: "conflict", message: "Already exists", detail: { field: "name" }, request_id: "req-9" } }));
    const err = await api("/projects", { body: {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "conflict", message: "Already exists", detail: { field: "name" }, requestId: "req-9" });
  });

  it("copes with non-JSON error bodies", async () => {
    responses.push(new Response("<html>Bad gateway</html>", { status: 502 }));
    await expect(api("/projects")).rejects.toMatchObject({ status: 502, code: "http_error", message: "Request failed (502)" });
  });

  it("returns undefined for 204 and the raw response when asked", async () => {
    responses.push(new Response(null, { status: 204 }), new Response("bytes", { status: 200 }));
    expect(await api("/projects/p1", { method: "DELETE" })).toBeUndefined();
    const raw = await api<Response>("/documents/d1/pages/1/image", { raw: true });
    expect(await raw.text()).toBe("bytes");
  });
});

describe("describeError", () => {
  it("shows the first validation problem by field name", () => {
    const e = new ApiError(422, "validation_failed", "Invalid input", [{ loc: ["body", "email"], msg: "value is not a valid email address" }]);
    expect(describeError(e)).toBe("email: value is not a valid email address");
  });

  it("adds the request id so users can quote it", () => {
    expect(describeError(new ApiError(500, "internal", "Server error", undefined, "req-42"))).toBe("Server error (reference req-42)");
    expect(describeError(new ApiError(404, "not_found", "Not found"))).toBe("Not found");
  });

  it("explains network failures in plain words", () => {
    expect(describeError(new TypeError("Failed to fetch"))).toBe("The server could not be reached. Check that the API is running.");
    expect(describeError(new Error("boom"))).toBe("Something went wrong in the page: boom");
    expect(describeError("plain")).toBe("plain");
  });
});
