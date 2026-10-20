"use client";

import { useCallback, useEffect, useState } from "react";
import { api, describeError, Job } from "@/lib/api";
import { useSession } from "@/components/session";
import { ErrorNote, Pill } from "@/components/shell";

interface Member { user_id: string; email: string; full_name: string; role: string; is_active: boolean }
interface Provider { id: string; name: string; purpose: string; status: string; missing_configuration: string[]; licence_notes: string; last_check: { status: string; detail: string; checked_at: string } | null }
interface Source { id: string; name: string; status: string; reason: string }
interface AuditItem { id: string; action: string; entity_type: string | null; entity_id: string | null; occurred_at: string; actor_id: string | null }

export default function AdminPage() {
  const { can } = useSession();
  const [members, setMembers] = useState<Member[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [unconnected, setUnconnected] = useState<Source[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, p, j] = await Promise.all([api<Member[]>("/org/members"), api<{ providers: Provider[]; unconnected_sources: Source[] }>("/providers"), api<Job[]>("/jobs?limit=25")]);
      setMembers(m); setProviders(p.providers); setUnconnected(p.unconnected_sources); setJobs(j);
      if (can("admin")) setAudit((await api<{ items: AuditItem[] }>("/audit-events?limit=40")).items);
    } catch (e) {
      setError(describeError(e));
    }
  }, [can]);
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<unknown>) => { setError(null); try { await fn(); await load(); } catch (e) { setError(describeError(e)); } };
  const nameOf = (id: string | null) => members.find((m) => m.user_id === id)?.full_name ?? (id ? "system" : "system");

  return (
    <>
      <div className="page-head"><div><h1>Organisation</h1><p>People, external connections, background work and the audit trail.</p></div></div>
      <ErrorNote error={error} />

      <section className="panel">
        <h2>People</h2>
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th></th></tr></thead>
          <tbody>{members.map((m) => (
            <tr key={m.user_id} style={m.is_active ? undefined : { opacity: 0.5 }}>
              <td>{m.full_name}</td><td>{m.email}</td>
              <td>{can("admin") && m.is_active && m.role !== "owner" ? (
                <select aria-label={`Role for ${m.full_name}`} value={m.role} onChange={(e) => act(() => api(`/org/members/${m.user_id}`, { method: "PATCH", body: { role: e.target.value } }))}>
                  {["admin", "analyst", "reviewer", "viewer"].map((r) => <option key={r}>{r}</option>)}
                </select>) : m.is_active ? m.role : "access revoked"}</td>
              <td>{can("admin") && m.is_active && m.role !== "owner" && <button className="quiet small" onClick={() => window.confirm(`Revoke ${m.full_name}'s access now?`) && act(() => api(`/org/members/${m.user_id}`, { method: "DELETE" }))}>Revoke access</button>}</td>
            </tr>
          ))}</tbody>
        </table>
        {can("admin") && (
          <form style={{ marginTop: 14 }} onSubmit={(e) => {
            e.preventDefault(); const f = new FormData(e.currentTarget); const form = e.currentTarget;
            act(() => api("/org/members", { body: { email: f.get("email"), full_name: f.get("name"), role: f.get("role"), initial_password: f.get("pw") } }).then(() => form.reset()));
          }}>
            <h3>Add a person</h3>
            <div className="row">
              <label>Name<input name="name" required /></label>
              <label>Email<input name="email" type="email" required /></label>
              <label>Role<select name="role" defaultValue="analyst">{["analyst", "reviewer", "viewer", "admin"].map((r) => <option key={r}>{r}</option>)}</select></label>
              <label>Initial password<input name="pw" type="password" required minLength={12} autoComplete="new-password" /></label>
              <button className="quiet">Add person</button>
            </div>
            <p className="small muted">Share the initial password through a separate channel. Email invitations arrive once an email service is configured.</p>
          </form>
        )}
      </section>

      <section className="panel">
        <h2>External connections</h2>
        <table>
          <thead><tr><th>Service</th><th>Status</th><th>Last check</th><th></th></tr></thead>
          <tbody>{providers.map((p) => (
            <tr key={p.id}>
              <td>{p.name}<div className="small muted">{p.purpose}</div>
                {p.missing_configuration.length > 0 && <div className="small">Needs: {p.missing_configuration.join(", ")}</div>}
                <div className="small muted">{p.licence_notes}</div></td>
              <td><Pill value={p.status} /></td>
              <td className="small">{p.last_check ? <>{new Date(p.last_check.checked_at).toLocaleString()}<div className="muted">{p.last_check.detail}</div></> : "never"}</td>
              <td>{can("admin") && <button className="quiet small" disabled={checking === p.id} onClick={() => { setChecking(p.id); act(() => api(`/providers/${p.id}/check`, { method: "POST" })).finally(() => setChecking(null)); }}>{checking === p.id ? "Checking…" : "Check now"}</button>}</td>
            </tr>
          ))}</tbody>
        </table>
        <h3 style={{ marginTop: 16 }}>Data sources without a connection</h3>
        <table><tbody>{unconnected.map((s) => <tr key={s.id}><td>{s.name}</td><td><Pill value={s.status} /></td><td className="small">{s.reason}</td></tr>)}</tbody></table>
      </section>

      <section className="panel">
        <h2>Background work</h2>
        {jobs.length === 0 ? <div className="empty">No background jobs yet.</div> : (
          <table>
            <thead><tr><th>Job</th><th>Status</th><th className="num">Attempts</th><th>Error</th><th></th></tr></thead>
            <tbody>{jobs.map((j) => (
              <tr key={j.id}>
                <td className="small">{j.kind}<div className="muted">{new Date(j.created_at).toLocaleString()}</div></td>
                <td><Pill value={j.status} /></td><td className="num">{j.attempts}/{j.max_attempts}</td>
                <td className="small">{j.last_error?.split("\n")[0] ?? "–"}</td>
                <td>{can("admin") && (j.status === "dead" || j.status === "failed") && <button className="quiet small" onClick={() => act(() => api(`/jobs/${j.id}/retry`, { method: "POST" }))}>Retry</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>

      {can("admin") && (
        <section className="panel">
          <h2>Audit trail</h2>
          <table>
            <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th></tr></thead>
            <tbody>{audit.map((a) => (
              <tr key={a.id}><td className="small">{new Date(a.occurred_at).toLocaleString()}</td><td className="small">{nameOf(a.actor_id)}</td>
                <td>{a.action}</td><td className="small muted">{a.entity_type} {a.entity_id?.slice(0, 8)}</td></tr>
            ))}</tbody>
          </table>
        </section>
      )}
    </>
  );
}
