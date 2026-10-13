"""Run: python3 phase0/test_run.py  (no network)"""
from run import check, parse_reply, pick_sections

# reply parsing: fenced, bare, and garbage
assert parse_reply('Sure:\n```json\n{"script": "Hi.", "claims": []}\n```')["script"] == "Hi."
assert parse_reply('{"script": "Hi.", "claims": [{"text": "x", "kind": "legend"}]}')["claims"][0]["kind"] == "legend"
for bad in ("no json here", '{"script": 1, "claims": []}'):
    try:
        parse_reply(bad)
        raise AssertionError(bad)
    except (ValueError, AttributeError):
        pass

# grounding: a year not in the source is flagged, one in the source is not
src = "Built in 1760 by Hyder Ali."
story = {"script": " ".join(["word"] * 60) + " Built in 1760, rebuilt 1874.", "claims": [{}]}
assert check(story, src, 60) == ["year 1874 not in source"], check(story, src, 60)
assert any("length" in f for f in check({"script": "Too short.", "claims": [{}]}, src, 60))

# sections: intro first, history hoisted above other sections, junk dropped
text = "Intro.\n== Architecture ==\nA.\n== History ==\nH.\n== References ==\nR."
assert pick_sections(text) == "Intro.\n== History ==\nH.\n== Architecture ==\nA.", pick_sections(text)

print("ok")
