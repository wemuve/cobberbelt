import { describe, expect, it } from "vitest";
import { bboxOf, circlePolygon } from "../geo";

const R = 6371008.8;
/** Great-circle distance in metres (haversine), independent of the code under test. */
function distanceM([lon1, lat1]: number[], [lon2, lat2]: number[]): number {
  const rad = Math.PI / 180;
  const dφ = (lat2 - lat1) * rad;
  const dλ = (lon2 - lon1) * rad;
  const a = Math.sin(dφ / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dλ / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

describe("circlePolygon", () => {
  it("returns a closed ring with steps + 1 vertices", () => {
    const ring = circlePolygon(27.48, -10.98, 500, 32).coordinates[0];
    expect(ring).toHaveLength(33);
    expect(ring[0][0]).toBeCloseTo(ring[32][0], 9);
    expect(ring[0][1]).toBeCloseTo(ring[32][1], 9);
  });

  it.each([
    ["Kolwezi", 25.47, -10.71, 250],
    ["Lubumbashi", 27.48, -11.66, 5_000],
    ["high latitude", 15.0, 69.5, 20_000],
  ])("puts every vertex at the true radius (%s, %d m)", (_name, lon, lat, radius) => {
    for (const vertex of circlePolygon(lon, lat, radius).coordinates[0]) {
      expect(Math.abs(distanceM([lon, lat], vertex) - radius)).toBeLessThan(radius * 1e-6 + 1e-3);
    }
  });

  it("is wider in degrees of longitude than latitude away from the equator", () => {
    const ring = circlePolygon(15.0, 60.0, 10_000).coordinates[0];
    const lons = ring.map(([x]) => x);
    const lats = ring.map(([, y]) => y);
    const width = Math.max(...lons) - Math.min(...lons);
    const height = Math.max(...lats) - Math.min(...lats);
    expect(width / height).toBeCloseTo(1 / Math.cos((60 * Math.PI) / 180), 1);
  });
});

describe("bboxOf", () => {
  const fc = (geometries: (GeoJSON.Geometry | null)[]): GeoJSON.FeatureCollection => ({
    type: "FeatureCollection",
    features: geometries.map((geometry) => ({ type: "Feature", properties: {}, geometry }) as GeoJSON.Feature),
  });

  it("covers points, lines and polygons with holes", () => {
    const box = bboxOf(
      fc([
        { type: "Point", coordinates: [26.0, -11.0] },
        { type: "LineString", coordinates: [[25.5, -10.5], [27.0, -12.0]] },
        {
          type: "Polygon",
          coordinates: [
            [[28, -9], [29, -9], [29, -8], [28, -9]],
            [[28.2, -8.9], [28.5, -8.9], [28.2, -8.6], [28.2, -8.9]],
          ],
        },
      ]),
    );
    expect(box).toEqual([25.5, -12.0, 29, -8]);
  });

  it("handles multi-geometries", () => {
    const box = bboxOf(fc([{ type: "MultiPoint", coordinates: [[1, 2], [-3, 4]] }]));
    expect(box).toEqual([-3, 2, 1, 4]);
  });

  it("returns null when nothing has a position", () => {
    expect(bboxOf(fc([]))).toBeNull();
    expect(bboxOf(fc([null]))).toBeNull();
    expect(bboxOf(fc([{ type: "GeometryCollection", geometries: [] }]))).toBeNull();
  });
});
