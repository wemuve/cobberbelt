# Real-document evaluation log

Documents here were supplied by the business owner for **internal evaluation only**. Source: RDC Rocks archive (rdc.rocks); several scans carry the Université de Lubumbashi library stamp. Reuse and redistribution terms are **not confirmed**. The files are not committed to this repository and must not be shown to customers until the terms are settled.

Environment: one worker, Tesseract 5 `eng+fra`, 300 dpi rendering, development VM.

## 1. Carte du Katanga, Feuille Bukama SUD-C-35 H, *Planche des terrains superficiels*, 1:200,000

- **What it is:** the Comité Spécial du Katanga sheet, from surveys made 1926–1928 under H. Droogmans; the print mark is dated 1930. It covers the 1° square 25°–26°E, 9°–10°S. The uploaded filename ("leopol-ville") does not match the content.
- **Pipeline:** 1 page, 21.4 MB, no text layer. Processed by OCR in 73 s, mean word confidence 70.4%, 2,985 characters.
- **What worked:**
  - Legend and title-block prose, in French with accents, came out nearly verbatim.
  - Search finds *alluvions*, *latérites*, *Lubilash*, *Droogmans* and *Bukama*.
  - A survey-location claim cites the title block by exact offsets, and the sheet footprint is stored as a feature with uncertainty marked *unknown*.
- **What did not:**
  - Place names in the map body are mostly lost or garbled. Rotated and curved labels, contour numbers and hatching produce noise; only some names, such as *Kabangolo* and *Midengi*, survive.
  - Mean confidence (70%) hides a split: the legend reads well while the map body is noise. A page-level average is misleading for maps.
  - No claim candidates were produced, as expected: there are no commodity terms or coordinate pairs in prose.
  - The page viewer shows the whole sheet at about 570 px wide, which is unreadable for a 1:200,000 map.
- **Defect found and fixed:** creating a feature without an uncertainty silently stored a 250 m default. That is invented precision, so it has been removed: uncertainty now stays unknown unless stated. Edits go through an audited `PATCH`.

## 2. Hermans, *Résultats des observations magnétiques 1934–1938, Carte magnétique du Congo belge*, Fascicule IV

- **What it is:** a mémoire of the Institut Royal Colonial Belge, 72 scanned pages, no text layer, 53 MB. It carries the Université de Lubumbashi library stamp.
- **Pipeline:** 72 pages OCR'd in 990 s (about 14 s per page), mean word confidence 80.6%. Page 68 is blank.
- **Prose coordinates cannot be recovered safely.** The text writes station positions as `φ = 1°13′47″, λ = 23°39′21″`. OCR rendered one of these as `@ = 191347"`: φ became `@` or `¢`, the degree sign was read as `9`, and the prime marks were dropped. "191347" is equally consistent with 1°13′47″ and 19°13′47″, so the rules deliberately do not try.
- **The station table (p. 41) is largely recoverable.** A new strict rule (`rules-1.1.0`) parses numbered rows of `D MM SS  DDD MM SS`.
  - **Recall:** 60 of 78 table rows (77%). The 16 unparsed rows have OCR-merged digits (`11332`, `25846`) and are left for manual entry.
  - **Precision against the printed page (rows 1–26):** every parsed row matches except one. Bangiliwe reads `3 03 28` in the OCR but `3 53 28` in print, a 50′ error of about 92 km that is syntactically perfect and cannot be caught by pattern checks. **Review against the page image is therefore mandatory; it is not optional QA.**
  - **Hemisphere:** the table gives no N/S/E/W. Signs appear only near the equator (`+` north, `—` south). Unsigned rows are north by convention, which is an inference, not a statement. Parsed rows therefore keep absolute values only, and a reviewer must state the hemispheres, with a reason, before a location can be mapped.
- **A real historical conflict:** for Basoko, the text cites Delporte & Gillis (1891) at 1°13′47″, while the table gives 1°13′32″. Both should be kept as separate claims on one feature with `contradicts`, which is the purpose of the evidence model.
- **A defect found and fixed: double processing.** The job ran longer than the 900 s lock timeout, and a second worker reclaimed it while it was still running. Fix: a 30 s lock heartbeat and fencing. Progress reports and final results are accepted only from the worker that holds the lock, and a superseded worker steps aside without touching document state. Regression tests were added. After the fix, the interrupted job was left to recover on its own as a live test.
- **Another defect found and fixed:** reprocessing re-created duplicates of claims a person had already reviewed. Duplicates of reviewed passages are now skipped.

## Implications for the backlog (evidence-based)

1. **Map sheets need their own path:** georeferencing from graticule corners, region-based OCR (legend and title block, not the map body), and a zoomable tile viewer. Promoted to the top of List 2.
2. **Store OCR confidence per text block, not only per page,** so search and review can down-weight noise.
3. **Throughput:** at about 14–15 s per page on one worker, a 10,000-page archive needs about 40 worker-hours. Page-level parallelism (one job per page batch) is required before archive-scale onboarding.
4. **Tables deserve layout-aware OCR.** Tesseract's page mode merges columns and drops separators. Use table-structure detection or cell-level OCR, then evaluate managed OCR on exactly these pages (ADR 0005). The station table above is a ready benchmark: 78 rows with printed ground truth.
5. **Evaluation set seeded:** Bukama map sheet and Hermans Fasc. IV. Next, add the text reports (Fontainas & Ansotte 1932; Passau) to measure claim extraction on commodity prose.
