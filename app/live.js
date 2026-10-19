// Live fallback, used off the planned route: Wikipedia nearby search -> LLM through your Cloudflare Worker
// (which holds the key) -> the same rule checks the laptop runs. A story with any flag is not played.
// check/parseReply are pure, so tests/test_live.mjs can drive them in node.

const WIKI = 'https://en.wikipedia.org/w/api.php';
const STOP = new Set(["I", "I'm", "I'd", "I'll", "I've", "OK", "Okay", "Oh", "Mr", "Mrs", "Sir",
  "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth"]);

export function parseReply(reply) {
  const m = reply.match(/```(?:json)?\s*([\[{][\s\S]*[\]}])\s*```/) || reply.match(/[\[{][\s\S]*[\]}]/);
  if (!m) throw new Error('no JSON in reply');
  let items = JSON.parse(m[1] ?? m[0]);
  items = Array.isArray(items) ? items : [items];
  for (const it of items) if (typeof it?.script !== 'string' || !Array.isArray(it.claims)) throw new Error('missing script/claims');
  return items;
}

// Mirror of narrate.check in histoway/narrate.py (flags only): change both together.
export function check(story, sourceText, rules, known = '', dark = true) {  // dark: false for answers to questions
  const text = story.script + ' ' + (story.more_script || ''), src = sourceText.replaceAll(',', ''), low = text.toLowerCase();
  const isYear = n => /^(1\d{3}|20\d{2})$/.test(n);
  const flags = [];
  for (const y of [...new Set(text.match(/\b(1[0-9]{3}|20[0-9]{2})\b/g))].sort()) if (!src.includes(y)) flags.push(`year ${y} not in source`);
  for (const n of [...new Set(text.match(/\b\d[\d,.]*\d\b|\b\d\b/g))].sort())
    if (!isYear(n) && !src.includes(n.replaceAll(',', ''))) flags.push(`number ${n} not in source`);
  const names = new Set([...text.matchAll(/(?<![.!?"'\n]\s)(?<!^)\b([A-Z][a-z]+(?:['’-][A-Za-z]+)?)/g)].map(m => m[1].replace(/['’]s$/, '')));
  const hay = (src + ' ' + known).toLowerCase();
  for (const w of [...names].sort()) if (!STOP.has(w) && !hay.includes(w.toLowerCase())) flags.push(`name '${w}' not in source`);
  if (/wikipedia|\bsources\b/i.test(text)) flags.push('mentions sources/Wikipedia');
  if (!story.claims.length) flags.push('no claims listed');
  const srcLow = src.toLowerCase();
  for (const w of dark ? rules.dark_themes : []) {
    const m = srcLow.match(new RegExp(w));
    if (m && !new RegExp(w).test(low)) flags.push(`source mentions '${m[0]}', story does not`);
  }
  const hit = rules.sensitive_words.filter(w => new RegExp(w).test(low));
  if (hit.length) flags.push(`sensitive topic (${hit.join(', ')}): review by hand`);
  return flags;
}

async function wiki(params) {
  const res = await fetch(`${WIKI}?${new URLSearchParams({ format: 'json', origin: '*', ...params })}`);
  if (!res.ok) throw new Error(`Wikipedia HTTP ${res.status}`);
  return (await res.json()).query?.pages ?? {};
}

// The best untold place within 5 km, told by the LLM; null if nothing nearby or the story failed a check.
// skip: qids not to try (told, or tried on this journey); it gains the qid tried here.
// Sends the Worker (and its LLM) the place's name and source text; Wikipedia gets the position rounded to ~100 m.
export async function liveStory({ lat, lon }, cfg, live, skip, previous = '') {
  const at = `${lat.toFixed(3)}|${lon.toFixed(3)}`;
  const pages = Object.values(await wiki({ generator: 'geosearch', ggscoord: at, ggsradius: 5000, ggslimit: 30,
    prop: 'pageprops|info|coordinates', ppprop: 'wikibase_item', codistancefrompoint: at }));
  const best = pages
    .map(p => ({ qid: p.pageprops?.wikibase_item, title: p.title, kb: p.length / 1000, km: (p.coordinates?.[0]?.dist ?? 5000) / 1000 }))
    .filter(p => p.qid && !skip.has(p.qid) && p.kb >= 4)
    .sort((a, b) => (Math.log1p(b.kb) - b.km / 2) - (Math.log1p(a.kb) - a.km / 2))[0];  // ponytail: size and distance only; no Wikidata types
  if (!best) return null;
  skip.add(best.qid);
  const page = Object.values(await wiki({ prop: 'extracts|info', explaintext: 1, inprop: 'url', redirects: 1, titles: best.title }))[0];
  const text = (page?.extract ?? '').slice(0, 6000);
  if (text.length < 500) return null;
  const line = `1. ${best.title}. Where the listener is: about ${best.km.toFixed(1)} km away, off their planned route. ` +
    `Length: about ${live.words.M} words for "script" (M tier); set "more_script" to null.`;
  const system = live.prompt.replace('$trip', () => 'a detour off the planned road trip')
    .replace('$previous', () => previous || 'nothing yet').replace('$places', () => line);
  const res = await fetch(cfg.url, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${cfg.token}` },
    body: JSON.stringify({ system, user: `INPUT START\n=== PLACE 1: ${best.title} ===\n[${page.title}]\n${text}\nINPUT END` }),
  });
  if (!res.ok) throw new Error(`Worker HTTP ${res.status}`);
  const story = parseReply((await res.json()).content)[0];
  const flags = check(story, text, live.review);
  if (flags.length) { console.info('live story held back:', best.title, flags); return null; }
  return { qid: best.qid, name: best.title, km: null, tier: 'M', script: story.script, more_script: null, audio: null,
    live: true, sources: [{ title: page.title, url: page.fullurl, revid: page.lastrevid }] };
}
