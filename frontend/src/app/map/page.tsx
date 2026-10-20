"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api, describeError, Feature } from "@/lib/api";
import { ErrorNote, Pill } from "@/components/shell";

const GeoMap = dynamic(() => import("@/components/geomap").then((m) => m.GeoMap), { ssr: false });

interface Layer { id: string; title: string; status: string; count?: number; reason?: string }
interface EvidenceItem {
  relation: string; claim_id: string; statement: string; review_status: string; evidence_basis: string;
  document_id: string | null; document_title: string | null; page_number: number | null; reported_date: string | null;
}
interface Evidence {
  summary: { supporting: number; contradicting: number; accepted: number; unreviewed: number; independently_verified: number; has_conflict: boolean; hidden_by_permissions: number };
  items: EvidenceItem[];
}

function MapView() {
  const params = useSearchParams();
  const [fc, setFc] = useState<GeoJSON.FeatureCollection | null>(null);
  const [areas, setAreas] = useState<GeoJSON.FeatureCollection | undefined>();
  const [layers, setLayers] = useState<Layer[]>([]);
  const [selected, setSelected] = useState<string | null>(params.get("feature"));
  const [feature, setFeature] = useState<Feature | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<GeoJSON.FeatureCollection>("/geo/features"),
      api<GeoJSON.FeatureCollection>("/geo/features?layer=areas_of_interest"),
      api<Layer[]>("/geo/layers"),
    ]).then(([f, a, l]) => { setFc(f); setAreas(a); setLayers(l); }).catch((e) => setError(describeError(e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    Promise.all([api<Feature>(`/geological-features/${selected}`), api<Evidence>(`/geological-features/${selected}/evidence`)])
      .then(([f, ev]) => { setFeature(f); setEvidence(ev); })
      .catch((e) => setError(describeError(e)));
  }, [selected]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Map</h1>
          <p>Rings show each location’s stated uncertainty at true scale. An empty area means no data here, not open ground.</p>
        </div>
      </div>
      <ErrorNote error={error} />
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 360px", gap: 18 }} className="map-grid">
        <div className="mapwrap">
          {fc && <GeoMap features={fc} areas={areas} selected={selected} onSelect={setSelected} />}
          <div className="legend">
            <div><i style={{ background: "var(--malachite)" }} />Feature accepted by a reviewer</div>
            <div><i style={{ background: "var(--limonite)" }} />Feature not yet reviewed</div>
            <div><i style={{ background: "var(--haematite)" }} />Sources disagree</div>
            <div><i style={{ border: "2px solid var(--limonite)" }} />Uncertainty not recorded</div>
            <div><i style={{ background: "var(--azurite)", opacity: .4, borderRadius: 0 }} />Project area</div>
          </div>
        </div>
        <aside>
          {!feature ? (
            <div className="panel">
              <h2>Layers</h2>
              <table><tbody>
                {layers.map((l) => (
                  <tr key={l.id}><td>{l.title}{l.reason && <div className="small muted">{l.reason}</div>}</td>
                    <td>{l.count != null ? <span className="num">{l.count}</span> : <Pill value={l.status} />}</td></tr>
                ))}
              </tbody></table>
              {fc && fc.features.length === 0 && <p className="small muted" style={{ marginTop: 10 }}>No features yet. Accept a coordinate claim in a document and choose “Put on map”.</p>}
              {fc && fc.features.length > 0 && <p className="small muted" style={{ marginTop: 10 }}>Select a point to see its evidence.</p>}
            </div>
          ) : (
            <div className="panel">
              <button className="quiet small" onClick={() => { setSelected(null); setFeature(null); }}>Back to layers</button>
              <h2 style={{ marginTop: 10 }}>{feature.name}</h2>
              <p className="small">{feature.feature_type.replaceAll("_", " ")} · {feature.commodities.join(", ") || "no commodity recorded"}</p>
              <p className="small">
                Location: {feature.location_precision.replaceAll("_", " ")}, {feature.uncertainty_m != null ? `±${Math.round(feature.uncertainty_m)} m` : "uncertainty unknown"}.
                {feature.original_coordinates && <> As written: <q>{feature.original_coordinates}</q>.</>}
                {feature.source_crs && <> Reference system: {feature.source_crs}.</>}
              </p>
              {evidence && (
                <>
                  {evidence.summary.has_conflict && <div className="notice error">Sources disagree about this feature. Both accounts are kept below.</div>}
                  {evidence.summary.independently_verified === 0 && <div className="notice">No field or laboratory verification is linked yet.</div>}
                  {evidence.summary.hidden_by_permissions > 0 && <p className="small muted">{evidence.summary.hidden_by_permissions} evidence item(s) are in documents you can’t open.</p>}
                  {evidence.items.map((it) => (
                    <div key={it.claim_id} style={{ marginBottom: 12 }}>
                      <Pill value={it.relation} /> <Pill value={it.review_status} />
                      <blockquote className="cite">{it.statement}</blockquote>
                      {it.document_id && (
                        <Link className="small" href={`/documents/${it.document_id}?page=${it.page_number}`}>
                          {it.document_title}, page {it.page_number}
                        </Link>
                      )}
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
        </aside>
      </div>
      <style>{`@media (max-width: 1000px) { .map-grid { grid-template-columns: 1fr !important; } }`}</style>
    </>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="muted">Loading…</p>}><MapView /></Suspense>;
}
