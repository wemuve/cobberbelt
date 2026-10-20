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

- **What it is:** a mémoire of the Institut Royal Colonial Belge, 72 scanned pages, no text layer, 53 MB.
- **Pipeline:** results are pending below.

## Implications for the backlog (evidence-based)

1. **Map sheets need their own path:** georeferencing from graticule corners, region-based OCR (legend and title block, not the map body), and a zoomable tile viewer. Promoted to the top of List 2.
2. **Store OCR confidence per text block, not only per page,** so search and review can down-weight noise.
3. **Throughput:** at about 15 s per page on one worker, a 10,000-page archive needs about 42 worker-hours. Page-level parallelism in the job design is required before archive-scale onboarding.
