"use client";

import * as maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { bboxOf, circlePolygon } from "@/lib/geo";

const STYLE = process.env.NEXT_PUBLIC_MAP_STYLE_URL ?? "https://demotiles.maplibre.org/style.json";
/** Used when the basemap cannot be fetched, so evidence layers still render on a plain ground. */
const FALLBACK_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": "#e3e8e4" } }],
};

const C = { malachite: "#1e7357", limonite: "#b0761a", haematite: "#9e3a2a", azurite: "#2c4f93", ink: "#18302c" };

export interface MapProps {
  features: GeoJSON.FeatureCollection;
  areas?: GeoJSON.FeatureCollection;
  selected?: string | null;
  onSelect?: (id: string) => void;
}

/** Uncertainty is drawn at true ground scale; unknown uncertainty gets a dashed warning halo instead of a ring. */
function rings(fc: GeoJSON.FeatureCollection): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: fc.features.flatMap((f) => {
      const p = f.properties ?? {};
      if (f.geometry?.type !== "Point" || p.uncertainty_m == null) return [];
      const [lon, lat] = (f.geometry as GeoJSON.Point).coordinates;
      return [{ type: "Feature", id: f.id, properties: { ...p, id: f.id }, geometry: circlePolygon(lon, lat, Number(p.uncertainty_m)) } as GeoJSON.Feature];
    }),
  };
}

export function GeoMap({ features, areas, selected, onSelect }: MapProps) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const ready = useRef(false);
  const fitted = useRef(false);
  /** Data that arrived before the style finished loading; applied once the layers exist. */
  const pending = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const m = new maplibregl.Map({ container: el.current, style: STYLE, center: [27.0, -12.0], zoom: 6, attributionControl: { compact: true } });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
    let fellBack = false;
    m.on("error", (e) => {
      if (!ready.current && !fellBack && /style|fetch|Failed/i.test(String(e.error?.message ?? ""))) {
        fellBack = true;
        m.setStyle(FALLBACK_STYLE);
      }
    });
    m.on("style.load", () => {
      if (m.getSource("features")) return;
      m.addSource("areas", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addSource("rings", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addSource("features", { type: "geojson", data: { type: "FeatureCollection", features: [] }, promoteId: "id" });
      m.addLayer({ id: "areas-fill", type: "fill", source: "areas", paint: { "fill-color": C.azurite, "fill-opacity": 0.08 } });
      m.addLayer({ id: "areas-line", type: "line", source: "areas", paint: { "line-color": C.azurite, "line-width": 2 } });
      m.addLayer({
        id: "rings", type: "fill", source: "rings",
        paint: { "fill-color": ["case", ["get", "has_conflict"], C.haematite, C.malachite], "fill-opacity": 0.12 },
      });
      m.addLayer({
        id: "rings-line", type: "line", source: "rings",
        paint: { "line-color": ["case", ["get", "has_conflict"], C.haematite, C.malachite], "line-width": 1 },
      });
      m.addLayer({
        id: "unknown-halo", type: "circle", source: "features", filter: ["all", ["==", ["geometry-type"], "Point"], ["!", ["get", "uncertainty_known"]]],
        paint: { "circle-radius": 14, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": C.limonite, "circle-stroke-width": 2, "circle-stroke-opacity": 0.9 },
      });
      m.addLayer({
        id: "points", type: "circle", source: "features", filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": ["case", ["boolean", ["feature-state", "selected"], false], 8, 5],
          "circle-color": ["case", ["get", "has_conflict"], C.haematite, ["==", ["get", "review_status"], "accepted"], C.malachite, C.limonite],
          "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5,
        },
      });
      m.addLayer({ id: "polys", type: "line", source: "features", filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": C.ink, "line-width": 2 } });
      if (fellBack) el.current?.setAttribute("data-basemap", "unavailable");
      m.on("click", "points", (e) => {
        const id = e.features?.[0]?.properties?.id ?? e.features?.[0]?.id;
        if (id && onSelect) onSelect(String(id));
      });
      m.on("mouseenter", "points", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "points", () => (m.getCanvas().style.cursor = ""));
      ready.current = true;
      pending.current?.();
      pending.current = null;
    });
    map.current = m;
    // The container is often sized after MapLibre measures it (dynamic import, grid layout): keep them in sync.
    const ro = new ResizeObserver(() => m.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      m.remove();
      map.current = null;
      ready.current = false;
      pending.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const apply = () => {
      const withIds = { ...features, features: features.features.map((f) => ({ ...f, properties: { ...f.properties, id: f.id } })) };
      (m.getSource("features") as maplibregl.GeoJSONSource)?.setData(withIds);
      (m.getSource("rings") as maplibregl.GeoJSONSource)?.setData(rings(features));
      (m.getSource("areas") as maplibregl.GeoJSONSource)?.setData(areas ?? { type: "FeatureCollection", features: [] });
      if (!fitted.current) {
        const bb = bboxOf({ type: "FeatureCollection", features: [...features.features, ...(areas?.features ?? [])] });
        if (bb) {
          m.fitBounds([[bb[0], bb[1]], [bb[2], bb[3]]], { padding: 60, maxZoom: 12, duration: 0 });
          fitted.current = true;
        }
      }
    };
    if (ready.current) apply();
    else pending.current = apply;
  }, [features, areas]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready.current) return;
    features.features.forEach((f) => f.id != null && m.setFeatureState({ source: "features", id: f.id }, { selected: f.id === selected }));
  }, [selected, features]);

  return <div ref={el} className="map-canvas" role="region" aria-label="Map of geological features" />;
}
