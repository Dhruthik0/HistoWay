"""Geocode (Nominatim), route (OSRM demo server), and project points onto the route as km markers."""
import numpy as np

from .common import get_json

KM_PER_DEG_LAT = 110.57


def geocode(query: str) -> tuple[float, float, str]:
    hits = get_json("https://nominatim.openstreetmap.org/search", {"q": query, "format": "json", "limit": 1})
    if not hits:
        raise SystemExit(f"Nominatim found nothing for {query!r}")
    return float(hits[0]["lat"]), float(hits[0]["lon"]), hits[0]["display_name"]


def route(a: tuple[float, float], b: tuple[float, float], min_gap_m: float = 50) -> dict:
    """Driving route a->b as {"points": [[lat, lon]], "km": [cumulative km], "total_km"}.
    Points closer than min_gap_m are dropped to keep the pack small."""
    r = get_json(f"https://router.project-osrm.org/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}",
                 {"overview": "full", "geometries": "geojson"})
    if r.get("code") != "Ok":
        raise SystemExit(f"OSRM: {r.get('code')} {r.get('message', '')}")
    coords = [(lat, lon) for lon, lat in r["routes"][0]["geometry"]["coordinates"]]
    points, km = [coords[0]], [0.0]
    for p in coords[1:]:
        d = dist_km(points[-1], p)
        if d * 1000 >= min_gap_m or p is coords[-1]:
            points.append(p)
            km.append(km[-1] + d)
    return {"points": [[round(la, 5), round(lo, 5)] for la, lo in points], "km": [round(k, 3) for k in km],
            "total_km": round(km[-1], 1)}


def dist_km(p: tuple[float, float], q: tuple[float, float]) -> float:
    """Equirectangular distance; fine at road-trip scales (error well under 1% below ~100 km)."""
    x = (q[1] - p[1]) * KM_PER_DEG_LAT * np.cos(np.radians((p[0] + q[0]) / 2))
    return float(np.hypot(x, (q[0] - p[0]) * KM_PER_DEG_LAT))


class Projector:
    """Projects points onto a route polyline: -> (km along route, km off route, 'left'|'right')."""

    def __init__(self, points: list, km: list):
        pts = self.pts = np.asarray(points, dtype=float)
        self.lat0 = np.radians(pts[:, 0].mean())
        self.xy = self._xy(pts[:, 0], pts[:, 1])
        self.km = np.asarray(km, dtype=float)

    def _xy(self, lat, lon):
        return np.stack([np.asarray(lon) * KM_PER_DEG_LAT * np.cos(self.lat0), np.asarray(lat) * KM_PER_DEG_LAT], -1)

    def project(self, lat: float, lon: float) -> tuple[float, float, str]:
        p = self._xy(lat, lon)
        a, b = self.xy[:-1], self.xy[1:]
        ab = b - a
        t = np.clip(((p - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
        d = np.hypot(*(a + ab * t[:, None] - p).T)
        i = int(d.argmin())
        cross = ab[i, 0] * (p[1] - a[i, 1]) - ab[i, 1] * (p[0] - a[i, 0])
        return float(self.km[i] + t[i] * (self.km[i + 1] - self.km[i])), float(d[i]), "left" if cross > 0 else "right"

    def point_at(self, km: float) -> tuple[float, float]:
        """(lat, lon) at a km marker along the route."""
        return float(np.interp(km, self.km, self.pts[:, 0])), float(np.interp(km, self.km, self.pts[:, 1]))

    def crossings(self, line: list) -> list[float]:
        """km markers where a polyline [[lat, lon], ...] (e.g. a river) crosses the route."""
        q = self._xy(*np.asarray(line, dtype=float).T)
        if len(q) < 2:
            return []
        lo, hi = q.min(0), q.max(0)  # only route segments that touch the river's bounding box
        near = np.nonzero(((np.minimum(self.xy[:-1], self.xy[1:]) <= hi) & (np.maximum(self.xy[:-1], self.xy[1:]) >= lo)).all(1))[0]
        return sorted(self._cross(near, q))

    def _cross(self, idx: np.ndarray, q: np.ndarray):
        a, r = self.xy[idx, None, :], (self.xy[idx + 1] - self.xy[idx])[:, None, :]
        c, s = q[None, :-1, :], (q[1:] - q[:-1])[None, :, :]
        denom = r[..., 0] * s[..., 1] - r[..., 1] * s[..., 0]
        ca = c - a
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (ca[..., 0] * s[..., 1] - ca[..., 1] * s[..., 0]) / denom
            u = (ca[..., 0] * r[..., 1] - ca[..., 1] * r[..., 0]) / denom
        hits = np.nonzero((denom != 0) & (t >= 0) & (t < 1) & (u >= 0) & (u < 1))  # half-open: a vertex counts once
        for k, j in zip(*hits):
            i = idx[k]
            yield float(self.km[i] + t[k, j] * (self.km[i + 1] - self.km[i]))
