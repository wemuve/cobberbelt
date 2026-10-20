"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, describeError, Doc, Listing } from "@/lib/api";
import { useSession } from "@/components/session";
import { ErrorNote, Pill } from "@/components/shell";

const GeoMap = dynamic(() => import("@/components/geomap").then((m) => m.GeoMap), { ssr: false });

interface DossierFeature {
  id: string; name: string; commodities: string[]; geometry: GeoJSON.Geometry | null; location_precision: string;
  uncertainty_m: number | null; review_status: string; in_areas: string[]; has_conflict: boolean;
  evidence: { claim_id: string; relation: string; statement: string; review_status: string; document_id: string | null; page_number: number | null }[];
}
interface Dossier {
  project: { id: string; name: string; description: string | null; country: string | null; commodities: string[]; stage: string };
  areas_of_interest: { id: string; name: string; geometry: GeoJSON.Geometry; area_km2: number }[];
  features: DossierFeature[];
  documents: { id: string; title: string; publication_year: number | null }[];
  risks: { id: string; title: string; category: string; severity: number; likelihood: number; score: number; band: string; status: string; verification_action: string | null; claim_ids: string[] }[];
  evidence_gaps: { id: string; topic: string; verification_action: string; status: string }[];
  detected_gaps: string[];
  evidence_quality: Record<string, number>;
  not_checked: string[];
  risk_scoring: { formula: string; note: string };
}
interface Assessment { id: string; version: number; title: string; status: string; content_sha256: string; created_at: string; approved_at: string | null; reviewer_conclusion: string | null }

const QUALITY_LABELS: Record<string, string> = {
  features: "Features in scope", features_without_evidence: "Features with no source evidence", features_with_conflicts: "Features where sources disagree",
  features_uncertainty_unknown: "Features with unknown location uncertainty", evidence_items: "Evidence items", evidence_accepted: "Accepted by a reviewer",
  evidence_unreviewed: "Not yet reviewed", evidence_historical_only: "Historical reports only", evidence_independently_verified: "Field or laboratory verified",
};

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const { can, me } = useSession();
  const [d, setD] = useState<Dossier | null>(null);
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [dd, a, ds] = await Promise.all([
        api<Dossier>(`/projects/${id}/dossier`), api<Assessment[]>(`/projects/${id}/assessments`),
        api<Listing<Doc>>("/documents?status=processed&limit=200"),
      ]);
      setD(dd); setAssessments(a); setDocs(ds.items);
    } catch (e) {
      setError(describeError(e));
    }
  }, [id]);
  useEffect(() => { load(); }, [load]);

  const fc = useMemo<GeoJSON.FeatureCollection>(() => ({
    type: "FeatureCollection",
    features: (d?.features ?? []).filter((f) => f.geometry).map((f) => ({
      type: "Feature", id: f.id, geometry: f.geometry!,
      properties: { name: f.name, review_status: f.review_status, uncertainty_m: f.uncertainty_m, uncertainty_known: f.uncertainty_m != null, has_conflict: f.has_conflict },
    })),
  }), [d]);
  const areas = useMemo<GeoJSON.FeatureCollection>(() => ({
    type: "FeatureCollection",
    features: (d?.areas_of_interest ?? []).map((a) => ({ type: "Feature", id: a.id, geometry: a.geometry, properties: { name: a.name } })),
  }), [d]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setError(null);
    try { await fn(); if (ok) setNotice(ok); await load(); } catch (e) { setError(describeError(e)); }
  };

  function addArea(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    let geometry: unknown;
    const raw = String(f.get("geojson") || "").trim();
    if (raw) {
      try { const g = JSON.parse(raw); geometry = g.type === "Feature" ? g.geometry : g; } catch { setError("The GeoJSON could not be read. Paste a Polygon geometry or Feature."); return; }
    } else {
      const [w, s, ea, n] = ["west", "south", "east", "north"].map((k) => Number(f.get(k)));
      if ([w, s, ea, n].some(Number.isNaN)) { setError("Enter all four bounds, or paste GeoJSON."); return; }
      geometry = { type: "Polygon", coordinates: [[[w, s], [ea, s], [ea, n], [w, n], [w, s]]] };
    }
    act(() => api("/areas-of-interest", { body: { name: f.get("name"), project_id: id, geometry, source_crs: f.get("crs") || null } }), "Area added.");
  }

  if (error && !d) return <ErrorNote error={error} />;
  if (!d) return <p className="muted">Loading…</p>;
  const editable = can("analyst");

  return (
    <>
      <div className="page-head">
        <div>
          <p className="small"><Link href="/projects">Projects</Link></p>
          <h1>{d.project.name}</h1>
          <p>{d.project.description ?? "No purpose recorded."}</p>
        </div>
      </div>
      <ErrorNote error={error} />
      {notice && <div className="notice ok" role="status">{notice}</div>}

      <div className="grid2">
        <section className="panel">
          <h2>Evidence quality</h2>
          <table><tbody>
            {Object.entries(d.evidence_quality).map(([k, v]) => <tr key={k}><td>{QUALITY_LABELS[k] ?? k}</td><td className="num">{v}</td></tr>)}
          </tbody></table>
        </section>
        <section className="panel">
          <h2>Open questions</h2>
          {d.detected_gaps.length === 0 ? <p className="muted">No gaps detected in the evidence held.</p> : <ul>{d.detected_gaps.map((g) => <li key={g}>{g}</li>)}</ul>}
          <h3>Not checked by the platform</h3>
          <ul className="small">{d.not_checked.map((n) => <li key={n}>{n}</li>)}</ul>
        </section>
      </div>

      <section className="panel">
        <h2>Areas and features</h2>
        <div className="mapwrap" style={{ height: 420 }}><GeoMap features={fc} areas={areas} /></div>
        {d.features.length === 0 ? <div className="empty" style={{ marginTop: 12 }}>No features fall inside this project’s areas yet.</div> : (
          <table style={{ marginTop: 12 }}>
            <thead><tr><th>Feature</th><th>Location</th><th>Evidence</th><th>Review</th></tr></thead>
            <tbody>{d.features.map((f) => (
              <tr key={f.id}>
                <td><Link href={`/map?feature=${f.id}`}>{f.name}</Link><div className="small muted">{f.in_areas.join(", ") || "linked directly"}</div></td>
                <td className="small">{f.uncertainty_m != null ? `±${Math.round(f.uncertainty_m)} m` : "uncertainty unknown"}</td>
                <td className="small">{f.evidence.length} item(s){f.has_conflict && <> · <Pill value="contradicts" /></>}</td>
                <td><Pill value={f.review_status} /></td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {editable && (
          <form onSubmit={addArea} style={{ marginTop: 16 }}>
            <h3>Add an area of interest</h3>
            <div className="row">
              <label style={{ flex: "2 1 200px" }}>Name<input name="name" required /></label>
              <label>West (lon)<input name="west" type="number" step="any" /></label>
              <label>South (lat)<input name="south" type="number" step="any" /></label>
              <label>East (lon)<input name="east" type="number" step="any" /></label>
              <label>North (lat)<input name="north" type="number" step="any" /></label>
            </div>
            <div className="row">
              <label style={{ flex: "3 1 300px" }}>…or paste a GeoJSON polygon<textarea name="geojson" style={{ minHeight: 60 }} /></label>
              <label>Coordinate system<input name="crs" placeholder="EPSG:4326 (default) or EPSG:32735" /></label>
              <button className="quiet">Add area</button>
            </div>
          </form>
        )}
      </section>

      <section className="panel">
        <h2>Documents</h2>
        {d.documents.length === 0 ? <p className="muted">No documents linked. Linked documents marked “members of linked projects” become visible to this project’s members.</p> : (
          <ul>{d.documents.map((doc) => <li key={doc.id}><Link href={`/documents/${doc.id}`}>{doc.title}</Link>{doc.publication_year ? ` (${doc.publication_year})` : ""}</li>)}</ul>
        )}
        {editable && docs.length > 0 && (
          <form className="row" onSubmit={(e) => { e.preventDefault(); const v = new FormData(e.currentTarget).get("doc"); if (v) act(() => api(`/projects/${id}/documents`, { body: { ids: [v] } })); }}>
            <label style={{ flex: "3 1 300px" }}>Link a document<select name="doc">{docs.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}</select></label>
            <button className="quiet">Link</button>
          </form>
        )}
      </section>

      <div className="grid2">
        <section className="panel">
          <h2>Risks</h2>
          <p className="small muted">{d.risk_scoring.formula}. {d.risk_scoring.note}</p>
          {d.risks.length === 0 ? <div className="empty">No risks recorded.</div> : (
            <table><thead><tr><th>Risk</th><th className="num">Score</th><th>Resolve by</th></tr></thead>
              <tbody>{d.risks.map((r) => (
                <tr key={r.id}><td>{r.title}<div className="small muted">{r.category.replaceAll("_", " ")} · {r.claim_ids.length} cited claim(s) · {r.status}</div></td>
                  <td className="num">{r.score} <span className="small muted">{r.band}</span></td><td className="small">{r.verification_action ?? "–"}</td></tr>
              ))}</tbody></table>
          )}
          {editable && (
            <form style={{ marginTop: 12 }} onSubmit={(e) => {
              e.preventDefault(); const f = new FormData(e.currentTarget); const form = e.currentTarget;
              act(() => api(`/projects/${id}/risks`, { body: {
                title: f.get("title"), category: f.get("category"), severity: Number(f.get("severity")), likelihood: Number(f.get("likelihood")),
                verification_action: f.get("action") || null, claim_ids: [],
              } }).then(() => form.reset()), "Risk recorded.");
            }}>
              <label>Risk<input name="title" required minLength={3} /></label>
              <div className="row">
                <label>Category<select name="category">{["geological", "title", "environmental", "social", "infrastructure", "financial", "data_quality", "regulatory", "other"].map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}</select></label>
                <label>Severity 1–5<input name="severity" type="number" min={1} max={5} defaultValue={3} required /></label>
                <label>Likelihood 1–5<input name="likelihood" type="number" min={1} max={5} defaultValue={3} required /></label>
              </div>
              <label>What would resolve it<input name="action" required /></label>
              <button className="quiet">Record risk</button>
            </form>
          )}
        </section>
        <section className="panel">
          <h2>Evidence gaps</h2>
          {d.evidence_gaps.length === 0 ? <div className="empty">No gaps recorded.</div> : (
            <table><tbody>{d.evidence_gaps.map((g) => (
              <tr key={g.id}><td>{g.topic}<div className="small muted">{g.verification_action}</div></td><td><Pill value={g.status === "resolved" ? "accepted" : "unreviewed"} /></td></tr>
            ))}</tbody></table>
          )}
          {editable && (
            <form style={{ marginTop: 12 }} onSubmit={(e) => {
              e.preventDefault(); const f = new FormData(e.currentTarget); const form = e.currentTarget;
              act(() => api(`/projects/${id}/gaps`, { body: { topic: f.get("topic"), verification_action: f.get("action") } }).then(() => form.reset()), "Gap recorded.");
            }}>
              <label>What we don’t know<input name="topic" required minLength={3} /></label>
              <label>How to find out<input name="action" required minLength={3} /></label>
              <button className="quiet">Record gap</button>
            </form>
          )}
        </section>
      </div>

      <section className="panel">
        <h2>Assessments</h2>
        <p className="small muted">An assessment freezes this dossier with a SHA-256 seal. It needs a reviewer’s written conclusion and approval by someone other than its author, and cannot be changed afterwards.</p>
        {assessments.length > 0 && (
          <table>
            <thead><tr><th className="num">Version</th><th>Title</th><th>Status</th><th>Seal</th><th></th></tr></thead>
            <tbody>{assessments.map((a) => (
              <tr key={a.id}>
                <td className="num">{a.version}</td>
                <td>{a.title}{a.reviewer_conclusion && <div className="small">{a.reviewer_conclusion}</div>}</td>
                <td><Pill value={a.status} /></td>
                <td className="small muted" title={a.content_sha256}>{a.content_sha256.slice(0, 12)}…</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {a.status === "draft" && can("reviewer") && <button className="quiet" onClick={() => act(() => api(`/projects/${id}/assessments/${a.id}/status`, { body: { status: "in_review" } }), "Sent for review.")}>Send for review</button>}
                  {a.status === "in_review" && can("admin") && (
                    <button onClick={() => {
                      const conclusion = window.prompt("Your professional conclusion (recorded with the approval):", a.reviewer_conclusion ?? "");
                      if (conclusion) act(() => api(`/projects/${id}/assessments/${a.id}/status`, { body: { status: "approved", reviewer_conclusion: conclusion } }), "Approved.");
                    }} title={me ? "The author cannot approve their own assessment" : undefined}>Approve</button>
                  )}
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {can("analyst") && (
          <form className="row" style={{ marginTop: 12 }} onSubmit={(e) => {
            e.preventDefault(); const t = new FormData(e.currentTarget).get("title");
            act(() => api(`/projects/${id}/assessments`, { body: { title: t } }), "Assessment created from the current dossier.");
          }}>
            <label style={{ flex: "3 1 300px" }}>Title<input name="title" required minLength={3} placeholder="Screening assessment" /></label>
            <button>Create assessment</button>
          </form>
        )}
      </section>
    </>
  );
}
