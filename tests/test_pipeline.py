"""Run: .venv/bin/python tests/test_pipeline.py   (or pytest; no network)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from histoway import narrate, places  # noqa: E402
from histoway.route import Projector  # noqa: E402

# A straight road north from (12, 77), a point every 100 m for 30 km.
POINTS = [[12 + i * 0.1 / 110.57, 77.0] for i in range(301)]
KM = [i * 0.1 for i in range(301)]


def test_projection():
    proj = Projector(POINTS, KM)
    km, off, side = proj.project(12 + 5 / 110.57, 77.01)       # 5 km up, ~1.08 km east
    assert abs(km - 5) < 0.01 and abs(off - 1.08) < 0.02 and side == "right", (km, off, side)
    assert proj.project(12 + 5 / 110.57, 76.99)[2] == "left"
    river = [[12 + 7 / 110.57, 76.9], [12 + 7 / 110.57, 77.1]]  # crosses at km 7
    assert [round(k, 2) for k in proj.crossings(river)] == [7.0]
    assert proj.crossings([[12.5, 76.9], [12.5, 76.95]]) == []


def place(qid, km, score, types=("palace",), lat=None, off=0.1):
    return {"qid": qid, "name": qid, "km": km, "score": score, "types": list(types), "off_km": off,
            "lat": 12 + km / 110.57 if lat is None else lat, "lon": 77.0}


def test_select():
    found = {p["qid"]: p for p in [
        *(place(f"Q{i}", 1 + i * 0.5, 20 - i) for i in range(10)),  # 10 good places in the first 10 km
        place("Q100", 1.0001, 15),                                   # 1 m from Q0: merged into it, uses no slot
        place("Q200", 25, 3),                                        # below min_score
        place("Q300", 29.9, 30, off=3),                              # beside the destination, not on the road
    ]}
    picks = places.select(found, total_km=30)
    names = [p["qid"] for p in picks]
    assert len(names) == places.SEL["per_10km"], names           # budget per 10 km window
    assert names == sorted(names, key=lambda q: found[q]["km"])  # route order
    q0 = next(p for p in picks if p["qid"] == "Q0")
    assert [a["qid"] for a in q0["also"]] == ["Q100"]
    assert "Q200" not in names and "Q300" not in names
    assert q0["tier"] == "L" and {p["tier"] for p in picks} <= {"L", "M", "S"}


def test_score_types():
    base = {"links": 10, "kb": 10, "off_km": 0}
    assert places.score(base | {"types": ["fortification"]}) > places.score(base | {"types": ["park"]})
    # "=human" hits people only, not towns ("human settlement")
    assert places.score(base | {"types": ["human"]}) < 0 < places.score(base | {"types": ["human settlement"]})


def test_parse_reply():
    two = 'Here:\n```json\n[{"n": 1, "script": "A.", "claims": []}, {"n": 2, "script": "B.", "claims": []}]\n```'
    assert [s["script"] for s in narrate.parse_reply(two)] == ["A.", "B."]
    assert narrate.parse_reply('```json\n{"script": "One.", "claims": []}\n```')[0]["script"] == "One."
    for bad in ("no json", '[{"script": 5, "claims": []}]'):
        try:
            narrate.parse_reply(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_check():
    src = "The fort was built in 1537 by Kempe Gowda. In 1791 the British took it. Prisoners were deported to Srirangapatna."
    ok = {"script": "Long ago, Kempe Gowda built a fort. Picture it! The British took it, and prisoners were deported. "
                    + "word " * 50, "claims": [{}]}
    assert narrate.check(ok, src, 60)[0] == [], narrate.check(ok, src, 60)
    bad = {"script": "Built in 1540 by Kempe Gowda and his friend Ramesh, the fort had 12 gates. " + "word " * 50,
           "claims": [{}]}
    flags = narrate.check(bad, src, 60)[0]
    assert "year 1540 not in source" in flags and "name 'Ramesh' not in source" in flags, flags
    assert "number 12 not in source" in flags, flags
    assert sum("story does not" in f for f in flags) == 1, flags  # deported + prisoners: one theme, one flag
    linked = {"script": "Long ago, Kempe Gowda built a fort. Prisoners were deported, just like at Gumbaz. " + "word " * 50,
              "claims": [{}]}
    assert narrate.check(linked, src, 60)[0] == ["name 'Gumbaz' not in source"]
    assert narrate.check(linked, src, 60, known="Gumbaz, Srirangapatna")[0] == []  # another place on this trip
    tipu = {"script": "Tipu's soldiers marched past. " + "word " * 50, "claims": [{}]}
    assert any(f.startswith("sensitive") for f in narrate.check(tipu, "Tipu marched.", 60)[0])
    assert narrate.check({"script": "Short.", "claims": [{}]}, src, 60)[1]  # length is a note, not a flag
    link = {"script": "Remember Kempe Gowda's fort?", "claims": [{}]}
    assert narrate.check(link, src, 25, dark=False)[0] == []  # a callback line need not repeat the dark side


def test_regions():
    """District lines found by sampling, then bisected; the starting district is not a crossing; a region
    re-entered later is told once."""
    real = places._region, places._enwiki
    line = lambda lat, lon, zoom: (("QKA" if zoom == 5 else "QD1" if lat < 12 + 12.3 / 110.57 else "QD2" if lat < 12 + 20 / 110.57  # noqa: E731
                                    else "QD1"))
    places._region, places._enwiki = line, lambda qids: {q: (q + " district", 9) for q in qids}
    try:
        got = places.regions({"points": POINTS, "km": KM, "total_km": 30.0}, skip=set())
    finally:
        places._region, places._enwiki = real
    assert [(r["qid"], r["region"], r["tier"]) for r in got] == [("QD2", "district", "S")], got
    assert abs(got[0]["km"] - 12.3) < 0.5, got[0]["km"]


def test_again_and_batches():
    assert narrate.again_line({"name": "Kaveri", "crossing": True}) == "Crossing the Kaveri again."
    assert narrate.again_line({"name": "Lalbagh", "off_km": 1.2, "side": "left"}) == "On your left again: Lalbagh."
    assert narrate._batches(["a" * 10, "b" * 10, "c" * 30000, "d"], len) == [["a" * 10, "b" * 10], ["c" * 30000], ["d"]]


def test_pair_up():
    s = lambda script, src: {"script": script, "source_text": src}  # noqa: E731
    told = [({"name": "Lalbagh", "qid": "Q1"}, s("Long ago, Hyder Ali and his son Tipu planted roses here.", "Hyder Ali, Tipu")),
            ({"name": "Cubbon Park", "qid": "Q2"}, s("The park was planned by Richard Sankey.", "Richard Sankey")),
            ({"name": "Srirangapatna", "qid": "Q3"}, s("This island was where Tipu made his last stand.", "Tipu Sultan died")),
            ({"name": "Gumbaz", "qid": "Q4"}, s("Here lies Tipu, next to Srirangapatna.", "tomb of Tipu"))]
    pairs = narrate.pair_up(told, "Lalbagh Cubbon Park Srirangapatna Gumbaz Bangalore Mysore")
    assert [(a["qid"], b["qid"], shared) for a, _, b, _, shared in pairs] == [("Q1", "Q3", ["Tipu"])], pairs
    # Gumbaz shares Tipu with Srirangapatna, but already names it, so no callback line is needed


def test_pick_sections():
    text = "Intro.\n== Architecture ==\nA.\n== History ==\nH.\n== References ==\nR."
    assert places.pick_sections(text, 8000) == "Intro.\n== History ==\nH.\n== Architecture ==\nA."


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
