"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { api, blobUrl, describeError } from "@/lib/api";
import type { Claim, Doc, Job, Listing, Page as PageText } from "@/lib/api";
import { useSession } from "@/components/session";
import { ErrorNote, Pill } from "@/components/shell";

interface Status {
  status: string; processing_error: string | null; malware_scan: string; retryable: boolean;
  last_processing: { pages: number; ocr_pages: number; mean_ocr_confidence: number | null; claim_candidates: number; empty_pages: number[] } | null;
  job: Job | null;
}

const TYPE_LABEL: Record<string, string> = {
  coordinate: "Coordinates", mineral_occurrence: "Mineral occurrence", drilling_reference: "Drilling reference",
  sample_assay_reference: "Sample / assay", formation: "Formation", mineralisation: "Mineralisation", other: "Other",
};

/** Split page text into plain runs and highlighted claim spans (overlaps: first claim wins). */
function segments(text: string, claims: Claim[]) {
  const spans = claims
    .filter((c) => c.char_start != null && c.char_end != null && c.char_end! <= text.length)
    .sort((a, b) => a.char_start! - b.char_start!);
  const out: { text: string; claim?: Claim }[] = [];
  let pos = 0;
  for (const c of spans) {
    if (c.char_start! < pos) continue;
    if (c.char_start! > pos) out.push({ text: text.slice(pos, c.char_start!) });
    out.push({ text: text.slice(c.char_start!, c.char_end!), claim: c });
    pos = c.char_end!;
  }
  if (pos < text.length) out.push({ text: text.slice(pos) });
  return out;
}

function DocumentView() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const { can } = useSession();
  const [doc, setDoc] = useState<Doc | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [pageNo, setPageNo] = useState(Number(params.get("page") ?? 1));
  const [page, setPage] = useState<PageText | null>(null);
  const [img, setImg] = useState<string | null>(null);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadDoc = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([api<Doc>(`/documents/${id}`), api<Status>(`/documents/${id}/status`)]);
      setDoc(d);
      setStatus(s);
    } catch (e) {
      setError(describeError(e));
    }
  }, [id]);

  useEffect(() => {
    loadDoc();
  }, [loadDoc]);

  useEffect(() => {
    if (status && (status.status === "queued" || status.status === "processing")) {
      const t = setTimeout(loadDoc, 2000);
      return () => clearTimeout(t);
    }
  }, [status, loadDoc]);

  const loadPage = useCallback(async () => {
    if (!doc || doc.status !== "processed") return;
    try {
      const [p, c] = await Promise.all([
        api<PageText>(`/documents/${id}/pages/${pageNo}`),
        api<Listing<Claim>>(`/claims?document_id=${id}&page_number=${pageNo}&limit=500`),
      ]);
      setPage(p);
      setClaims(c.items);
    } catch (e) {
      setError(describeError(e));
    }
    setImg(null);
    blobUrl(`/documents/${id}/pages/${pageNo}/image`).then(setImg).catch(() => setImg(""));
  }, [doc, id, pageNo]);

  useEffect(() => {
    loadPage();
  }, [loadPage]);

  const segs = useMemo(() => (page ? segments(page.text, claims) : []), [page, claims]);

  async function review(c: Claim, decision: string) {
    const comment = decision === "accepted" ? null : window.prompt(decision === "rejected" ? "Why is this claim rejected?" : "What information is missing?");
    if (decision !== "accepted" && !comment) return;
    try {
      const updated = await api<Claim>(`/claims/${c.id}/review`, { body: { decision, comment } });
      setClaims((cs) => cs.map((x) => (x.id === c.id ? updated : x)));
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function promote(c: Claim) {
    const name = window.prompt("Name for the new map feature (as used in the source):");
    if (!name) return;
    try {
      const f = await api<{ id: string; warnings: string[] }>(`/claims/${c.id}/promote`, { body: { name, commodities: c.commodities } });
      setNotice(`Feature “${name}” created. ${f.warnings.join(" ")}`);
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function reprocess(forceOcr: boolean) {
    try {
      await api(`/documents/${id}/process`, { body: { force_ocr: forceOcr } });
      setNotice(forceOcr ? "Re-running OCR on every page." : "Processing queued again.");
      loadDoc();
    } catch (e) {
      setError(describeError(e));
    }
  }

  if (error && !doc) return <ErrorNote error={error} />;
  if (!doc || !status) return <p className="muted">Loading…</p>;
  const pages = doc.page_count ?? 0;
  const lp = status.last_processing;

  return (
    <>
      <div className="page-head">
        <div>
          <p className="small"><Link href="/documents">Documents</Link></p>
          <h1>{doc.title}</h1>
          <p>{doc.original_filename} · {doc.publication_year ?? "year unknown"} · {doc.classification.replaceAll("_", " ")}</p>
        </div>
        <div><Pill value={status.status} /></div>
      </div>
      <ErrorNote error={error} />
      {notice && <div className="notice ok" role="status">{notice}</div>}

      {(status.status === "queued" || status.status === "processing") && (
        <div className="notice">
          Processing{status.job ? ` (attempt ${status.job.attempts} of ${status.job.max_attempts}, ${Math.round(status.job.progress)}%)` : ""}.
          {status.job?.progress_message ? ` ${status.job.progress_message}.` : ""} This page updates by itself.
          {status.processing_error && <div className="small">Last attempt failed: {status.processing_error}. It will retry automatically.</div>}
        </div>
      )}
      {(status.status === "failed" || status.status === "quarantined") && (
        <div className="notice error">
          <strong>{status.status === "quarantined" ? "Blocked by the malware scan." : "Processing failed."}</strong> {status.processing_error}
          {status.retryable && can("analyst") && status.status === "failed" && (
            <div style={{ marginTop: 8 }}><button onClick={() => reprocess(false)}>Retry processing</button></div>
          )}
        </div>
      )}
      {lp && (
        <p className="small muted">
          {lp.pages} pages, {lp.ocr_pages} read by OCR{lp.mean_ocr_confidence != null ? ` (mean confidence ${lp.mean_ocr_confidence}%)` : ""},
          {" "}{lp.claim_candidates} claim candidates found. Malware scan: {status.malware_scan.replaceAll("_", " ")}.
          {lp.empty_pages.length > 0 && ` No text found on page(s) ${lp.empty_pages.join(", ")}.`}
          {can("analyst") && <> <button className="quiet small" onClick={() => reprocess(true)}>Re-run OCR on all pages</button></>}
        </p>
      )}

      {doc.status === "processed" && pages > 0 && (
        <>
          <nav className="pager" aria-label="Pages" style={{ marginBottom: 10 }}>
            {Array.from({ length: pages }, (_, i) => i + 1).map((n) => (
              <button key={n} className="quiet" aria-current={n === pageNo} onClick={() => setPageNo(n)}>{n}</button>
            ))}
          </nav>
          <div className="evidence">
            <figure>
              {img === null ? <p className="muted" style={{ padding: 20 }}>Rendering page…</p>
                : img === "" ? <p className="muted" style={{ padding: 20 }}>No page image for this file type.</p>
                : /* eslint-disable-next-line @next/next/no-img-element */
                  <img src={img} alt={`Original page ${pageNo}`} />}
            </figure>
            <div>
              {page && (
                <div className="source-meta">
                  Page {page.page_number}: {page.extraction_method === "ocr" ? `OCR, confidence ${page.ocr_confidence ?? "?"}%` : "text layer"}
                  {page.detected_language ? `, ${page.detected_language}` : ""}. Underlined passages are extracted claims.
                </div>
              )}
              <div className="source-text" lang={page?.detected_language ?? undefined}>
                {segs.length === 0 ? <span className="muted">No text on this page.</span> : segs.map((s, i) =>
                  s.claim ? (
                    <mark key={i} data-status={s.claim.review_status} data-active={active === s.claim.id}
                      tabIndex={0} onClick={() => setActive(s.claim!.id)} onKeyDown={(e) => e.key === "Enter" && setActive(s.claim!.id)}
                      title={`${TYPE_LABEL[s.claim.claim_type] ?? s.claim.claim_type}: ${s.claim.review_status}`}>{s.text}</mark>
                  ) : <span key={i}>{s.text}</span>,
                )}
              </div>
            </div>
          </div>

          <section className="panel" style={{ marginTop: 18 }}>
            <h2>Claims on page {pageNo}</h2>
            {claims.length === 0 ? <div className="empty">No claims were extracted from this page.</div> : (
              <table>
                <thead><tr><th>Type</th><th>Statement</th><th>Commodities</th><th>Status</th><th></th></tr></thead>
                <tbody>
                  {claims.map((c) => (
                    <tr key={c.id} onMouseEnter={() => setActive(c.id)} style={active === c.id ? { outline: "2px solid var(--azurite)" } : undefined}>
                      <td className="small">{TYPE_LABEL[c.claim_type] ?? c.claim_type}
                        <div className="muted">{c.extraction_method}, confidence {c.confidence ?? "–"}</div></td>
                      <td>
                        <blockquote className="cite">{c.statement}</blockquote>
                        {c.claim_type === "coordinate" && (
                          <div className="small muted">
                            Parsed {String(c.attributes.lat)}, {String(c.attributes.lon)} · stated to {String(c.attributes.stated_precision)} · datum {String(c.attributes.datum)}
                          </div>
                        )}
                      </td>
                      <td className="small">{c.commodities.join(", ") || "–"}</td>
                      <td><Pill value={c.review_status} /></td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {can("reviewer") && c.review_status !== "accepted" && <button onClick={() => review(c, "accepted")}>Accept</button>}{" "}
                        {can("reviewer") && c.review_status !== "rejected" && <button className="quiet" onClick={() => review(c, "rejected")}>Reject</button>}{" "}
                        {can("reviewer") && <button className="quiet" onClick={() => review(c, "needs_information")}>Needs info</button>}{" "}
                        {can("analyst") && c.claim_type === "coordinate" && c.review_status === "accepted" && (
                          <button className="quiet" onClick={() => promote(c)}>Put on map</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}

      <section className="panel small muted">
        SHA-256 {doc.sha256} · {(doc.size_bytes / 1024).toFixed(0)} KB · {doc.content_type} · processing version {doc.processing_version ?? "–"}
      </section>
    </>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="muted">Loading…</p>}><DocumentView /></Suspense>;
}
