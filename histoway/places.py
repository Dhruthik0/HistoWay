"""Find places along the route (Wikidata + OSM rivers), score and pick them, and fetch their Wikipedia sources."""
import math
import re
import urllib.error
import urllib.parse

import numpy as np

from .common import CONFIG, ROOT, get_json
from .route import KM_PER_DEG_LAT, Projector, dist_km

SEL = CONFIG["select"]
SEGMENT_KM = 20
REGION_STEP_KM = 5
SPARQL = """SELECT ?item ?coord ?title ?links (GROUP_CONCAT(DISTINCT ?tl; separator="|") AS ?types) WHERE {
  SERVICE wikibase:box { ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerSouthWest "Point(%f %f)"^^geo:wktLiteral ;
                    wikibase:cornerNorthEast "Point(%f %f)"^^geo:wktLiteral . }
  ?item wikibase:sitelinks ?links . FILTER(?links >= %d)
  ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?title .
  OPTIONAL { ?item wdt:P31/rdfs:label ?tl . FILTER(LANG(?tl) = "en") }
} GROUP BY ?item ?coord ?title ?links"""
# Rivers carry their wikidata tag on the waterway relation, sometimes on single ways. Geometry clipped to the box.
OVERPASS = '[out:json][timeout:90];(way["waterway"="river"]["wikidata"]({b});rel["waterway"="river"]["wikidata"]({b}););out tags geom({b});'


def _bbox(points: np.ndarray, pad_km: float) -> tuple[float, float, float, float]:
    """(south, west, north, east) around points, padded by pad_km."""
    dlat = pad_km / KM_PER_DEG_LAT
    dlon = pad_km / (KM_PER_DEG_LAT * math.cos(math.radians(points[:, 0].mean())))
    return points[:, 0].min() - dlat, points[:, 1].min() - dlon, points[:, 0].max() + dlat, points[:, 1].max() + dlon


def _types(types: str) -> list[str]:
    return [t for t in types.lower().split("|") if t]


def is_big(types: list[str]) -> bool:
    return any(b in t for t in types for b in SEL["big_types"])


def corridor_for(types: list[str]) -> float:
    return SEL["corridor_big"] if is_big(types) else SEL["corridor"]


def candidates(rt: dict, proj: Projector) -> dict[str, dict]:
    """All Wikidata items with an English article near the route, plus rivers the road crosses, keyed by QID."""
    pts, km = np.asarray(rt["points"]), np.asarray(rt["km"])
    found: dict[str, dict] = {}
    for start in np.arange(0, rt["total_km"], SEGMENT_KM):
        seg = pts[(km >= start - 0.5) & (km <= start + SEGMENT_KM + 0.5)]
        s, w, n, e = _bbox(seg, SEL["corridor_big"])
        q = SPARQL % (w, s, e, n, SEL["min_sitelinks"])
        rows = get_json("https://query.wikidata.org/sparql", {"query": q, "format": "json"})["results"]["bindings"]
        for r in rows:
            qid = r["item"]["value"].rsplit("/", 1)[1]
            if qid in found:
                continue
            lon, lat = map(float, re.findall(r"-?[\d.]+", r["coord"]["value"])[:2])
            types = _types(r.get("types", {}).get("value", ""))
            at_km, off, side = proj.project(lat, lon)
            if off <= corridor_for(types):
                found[qid] = {"qid": qid, "name": r["title"]["value"], "lat": lat, "lon": lon, "types": types,
                              "links": int(r["links"]["value"]), "km": round(at_km, 2), "off_km": round(off, 2),
                              "side": side}
        for river in _rivers(*_bbox(seg, 0.2)):
            qid = river["tags"]["wikidata"]
            lines = [river.get("geometry", [])] + [m.get("geometry", []) for m in river.get("members", []) if m["type"] == "way"]
            hits = sorted(k for line in lines for piece in _pieces(line) for k in proj.crossings(piece))
            if hits and (qid not in found or found[qid].get("crossing") is None):
                found[qid] = {"qid": qid, "name": river["tags"].get("name:en") or river["tags"].get("name", qid),
                              "lat": None, "lon": None, "types": ["river"], "links": 0, "km": round(hits[0], 2),
                              "off_km": 0.0, "side": "crossing", "crossing": True}
    _fill_rivers(found)
    return found


def _pieces(geometry: list) -> list[list]:
    """Overpass marks clipped-away nodes as null: split there instead of joining across the gap."""
    out = [[]]
    for g in geometry:
        out[-1].append([g["lat"], g["lon"]]) if g else out.append([])
    return [x for x in out if len(x) > 1]


def _rivers(s: float, w: float, n: float, e: float) -> list[dict]:
    """River ways with a wikidata tag in the box. Overpass is often overloaded: warn and go on without rivers."""
    try:
        return get_json("https://overpass-api.de/api/interpreter", min_gap=5, tries=2,
                        data=urllib.parse.urlencode({"data": OVERPASS.format(b=f"{s},{w},{n},{e}")}))["elements"]
    except (urllib.error.URLError, TimeoutError) as err:
        print(f"   warning: Overpass unavailable ({err}); river crossings skipped for this stretch", flush=True)
        return []


def _enwiki(qids: list[str]) -> dict[str, tuple[str, int]]:
    """QID -> (English Wikipedia title, sitelink count), for items with an article and at least min_sitelinks."""
    out = {}
    for i in range(0, len(qids), 50):
        ents = get_json("https://www.wikidata.org/w/api.php", {"action": "wbgetentities", "ids": "|".join(qids[i:i + 50]),
                                                                "props": "sitelinks", "format": "json"})["entities"]
        for qid in qids[i:i + 50]:
            links = ents.get(qid, {}).get("sitelinks", {})
            if "enwiki" in links and len(links) >= SEL["min_sitelinks"]:
                out[qid] = (links["enwiki"]["title"], len(links))
    return out


def _fill_rivers(found: dict) -> None:
    """OSM gives the river's QID only; get its English article from Wikidata."""
    known = _enwiki([q for q, p in found.items() if p.get("crossing")])
    for qid in [q for q, p in found.items() if p.get("crossing")]:
        if qid in known:
            found[qid] |= {"name": known[qid][0], "links": known[qid][1]}
        else:
            del found[qid]


def _region(lat: float, lon: float, zoom: int) -> str | None:
    """QID of the district (zoom 6) or state (zoom 5) at a point, from Nominatim; None where OSM has none."""
    r = get_json("https://nominatim.openstreetmap.org/reverse", {"lat": round(lat, 4), "lon": round(lon, 4), "zoom": zoom,
                                                                 "format": "jsonv2", "extratags": 1})
    want = "state_district" if zoom == 6 else "state"
    return r.get("extratags", {}).get("wikidata") if r.get("addresstype") == want else None


def regions(rt: dict, skip: set[str]) -> list[dict]:
    """District and state lines the road crosses, as places at the crossing km. Sampled every REGION_STEP_KM,
    then bisected to under 0.5 km. The starting region is never a crossing; each region is told once per trip."""
    proj = Projector(rt["points"], rt["km"])
    at = lambda k, zoom: _region(*proj.point_at(k), zoom)  # noqa: E731
    ks = [*np.arange(0, rt["total_km"], REGION_STEP_KM), rt["total_km"]]
    out: dict[str, dict] = {}
    for zoom, kind, tier in ((5, "state", "M"), (6, "district", "S")):
        prev = start = at(ks[0], zoom)
        for lo, hi in zip(ks, ks[1:]):
            here = at(hi, zoom)
            if here == prev:
                continue
            a, b = lo, hi
            while b - a > 0.5:
                mid = (a + b) / 2
                a, b = (mid, b) if at(mid, zoom) == prev else (a, mid)
            new = at(b, zoom)
            if new and new != start and new not in skip and new not in out:
                out[new] = {"qid": new, "km": round(float(b), 2), "off_km": 0.0, "side": "crossing", "region": kind,
                            "types": [kind], "lat": None, "lon": None, "tier": tier}
            prev = here
    names = _enwiki(list(out))
    return [p | {"name": names[q][0], "links": names[q][1]} for q, p in out.items() if q in names]


def article_kb(titles: list[str]) -> dict[str, float]:
    out = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        q = get_json("https://en.wikipedia.org/w/api.php", {
            "action": "query", "format": "json", "prop": "info", "redirects": 1, "titles": "|".join(batch)})["query"]
        fwd = {r["from"]: r["to"] for r in q.get("normalized", []) + q.get("redirects", [])}
        length = {p["title"]: p.get("length", 0) / 1000 for p in q["pages"].values()}
        for t in batch:
            final = fwd.get(fwd.get(t, t), fwd.get(t, t))  # normalized, then redirected
            out[t] = length.get(final, 0)
    return out


def score(p: dict) -> float:
    bonus = sum(v for k, v in SEL["type_bonus"].items()
                if any(k[1:] == t if k[0] == "=" else k in t for t in p["types"]))
    return (3 * math.log1p(p["links"]) + math.log1p(p.get("kb", 0)) + bonus
            - 2 * p["off_km"] / corridor_for(p["types"]))


def _ids(name: str) -> set[str]:
    path = ROOT / name
    return set(re.findall(r"Q\d+", path.read_text())) if path.exists() else set()


def select(found: dict[str, dict], total_km: float) -> list[dict]:
    """Best places per 10 km window; places within merge_m of a better one join its story (and use no slot);
    tiers by rank. Returns route order."""
    include, exclude = _ids("include.txt"), _ids("exclude.txt")
    picked, per_window = [], {}
    for p in sorted((p for q, p in found.items() if q not in exclude), key=lambda p: -p["score"]):
        forced = p["qid"] in include
        beside_end = (p["km"] < 0.3 or p["km"] > total_km - 0.3) and p["off_km"] > 1  # around a city, not on the road
        if not forced and (p["score"] < SEL["min_score"] or beside_end):
            continue
        host = None if is_big(p["types"]) or p["lat"] is None else next(
            (m for m in picked if not is_big(m["types"]) and m["lat"] is not None and len(m.get("also", [])) < 2
             and dist_km((p["lat"], p["lon"]), (m["lat"], m["lon"])) * 1000 < SEL["merge_m"]), None)
        if host:
            host.setdefault("also", []).append(p)
            continue
        w = int(p["km"] // 10)
        if forced or per_window.get(w, 0) < SEL["per_10km"]:
            per_window[w] = per_window.get(w, 0) + 1
            picked.append(p)
    for rank, p in enumerate(picked):
        share = rank / len(picked)
        p["tier"] = "L" if share < SEL["share_L"] else "M" if share < SEL["share_L"] + SEL["share_M"] else "S"
    return sorted(picked, key=lambda p: p["km"])


_JUNK = re.compile(r"see also|references|external links|further reading|gallery|notes|bibliography|citations|sources", re.I)
_HOOK = re.compile(r"histor|legend|myth|controvers|disput|conflict|war|battle|siege|demoli|disaster|fire|etymolog|origin", re.I)


def pick_sections(text: str, limit: int) -> str:
    """Intro first, then history/conflict sections, then the rest; reference sections dropped."""
    parts = re.split(r"\n(?===[^=])", text)
    intro, sections = parts[0], [p for p in parts[1:] if not _JUNK.search(p.split("\n", 1)[0])]
    sections.sort(key=lambda p: not _HOOK.search(p.split("\n", 1)[0]))
    return "\n".join([intro, *sections])[:limit]


def source(title: str, limit: int = 8000) -> dict:
    """{"title", "url", "revid", "text"} for one English Wikipedia article."""
    pages = get_json("https://en.wikipedia.org/w/api.php", {
        "action": "query", "format": "json", "redirects": 1, "prop": "extracts|info", "explaintext": 1,
        "inprop": "url", "titles": title})["query"]["pages"]
    page = next(iter(pages.values()))
    return {"title": page.get("title", title), "url": page.get("fullurl"), "revid": page.get("lastrevid"),
            "text": pick_sections(page.get("extract", ""), limit)}


def find(rt: dict) -> list[dict]:
    proj = Projector(rt["points"], rt["km"])
    found = candidates(rt, proj)
    kb = article_kb([p["name"] for p in found.values()])
    for p in found.values():
        p["kb"] = kb.get(p["name"], 0)
        p["score"] = round(score(p), 2)
    return select(found, rt["total_km"])
