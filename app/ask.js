// Ask the guide: what kind of question it is, which place it is about, and live answers from the nearest places'
// sources (through the same Worker as live stories, with the same rule checks). Pure apart from liveAnswer,
// so tests/test_ask.mjs can drive it in node.
import { check } from './live.js';

export function intent(question) {
  const q = (question ?? '').toLowerCase().replace(/’/g, "'").trim();
  if (!q) return null;
  if (/\b(dark|bad|sad|wars?|battles?|cruel\w*|terribl\w*|tragic|traged\w*|horribl\w*|violen\w*|massacres?|bloody|killed)\b/.test(q)) return 'dark';
  if (/\bwhy\b.*\b(here|there|built|located|founded|set up|came up|come up)\b|\bhow come\b/.test(q)) return 'why';
  if (/\bwhere (are )?we\b|\bwhere am i\b|\bwhat(?:'s| is| was)? (that|this)\b|\b(which|what) \w+ is (this|that)\b|\bwhat am i (looking at|seeing)\b|\btell me about (this|that|it|here)\b/.test(q)) return 'what';
  return 'free';
}

const real = p => p?.km != null && !/^(intro|outro)-/.test(p.qid);
const dist = (p, km) => Math.abs(p.km - km) + (p.off_km ?? 0);
export const nearest = (places, km) => places.filter(real).sort((a, b) => dist(a, km) - dist(b, km));

// The story playing or just played if it is still within 3 km, else the nearest place within 3 km, else null.
export function about(places, km, current) {
  if (km == null) return null;
  if (real(current) && Math.abs(current.km - km) <= 3) return current;
  const p = nearest(places, km)[0];
  return p && dist(p, km) <= 3 ? p : null;
}

// places: the trip's places, nearest first (their names may be mentioned, as in narrate.check). Sends the question
// and up to 3 places' sources to the Worker; null if the answer is empty or fails a rule check (a name, year or
// number not in the sources, a sensitive topic).
export async function liveAnswer(question, places, cfg, live, where) {
  const ps = places.filter((p, i, a) => p.source && a.findIndex(x => x.qid === p.qid) === i).slice(0, 3);
  if (!ps.length) return null;
  const user = 'INPUT START\n' + ps.map((p, i) => `=== PLACE ${i + 1}: ${p.name} ===\n[${p.name}]\n${p.source}`).join('\n') + '\nINPUT END';
  const system = live.ask_prompt.replaceAll('$question', () => question).replaceAll('$where', () => where);
  const res = await fetch(cfg.url, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${cfg.token}` },
    body: JSON.stringify({ system, user }), signal: AbortSignal.timeout(30000),
  });
  if (!res.ok) throw new Error(`Worker HTTP ${res.status}`);
  const reply = (await res.json()).content, m = reply.match(/```(?:json)?\s*(\{[\s\S]*\})\s*```/) || reply.match(/\{[\s\S]*\}/);
  const answer = m && JSON.parse(m[1] ?? m[0]).answer;
  if (typeof answer !== 'string') throw new Error('no answer in reply');
  const flags = check({ script: answer, claims: [answer] }, user, live.review, places.map(p => p.name).join(' '), false);
  if (flags.length) { console.info('live answer held back:', question, flags); return null; }
  return answer.trim() || null;
}
