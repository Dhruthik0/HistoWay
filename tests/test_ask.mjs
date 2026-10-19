// Run: node tests/test_ask.mjs   (no browser, no network): the Ask button's question kinds, place choice and live answers.
import assert from 'node:assert/strict';
import { intent, about, liveAnswer } from '../app/ask.js';

const cases = {
  dark: ['Tell me the dark history', 'Any bad history here?', 'What sad things happened here?', 'What all wars happened here?',
    'Was there any battle in this place?', 'Tell me the dark side of this place na', 'Anything terrible happened here?'],
  why: ['Why is this town here?', 'Why was this city built here?', 'Why they built the town here only?', 'How come there is a town here?',
    'Why did people settle here?', 'Why is Hyderabad here?'],
  what: ['What is that building?', "What's this?", 'What’s that on the left?', 'Where are we?', 'Where we are now?', 'Which temple is this?',
    'What place is this?', 'Tell me about this place', 'What am I looking at?'],
  free: ['Who built the palace?', 'Is the zoo open on Tuesdays?', 'Why is Hyderabad called Hyderabad?', 'How old is Mysore?'],
};
for (const [kind, qs] of Object.entries(cases)) for (const q of qs) assert.equal(intent(q), kind, q);
for (const q of ['', '   ', null, undefined]) assert.equal(intent(q), null);

// about(): the current story while within 3 km, else the nearest real place (km gap + off_km) within 3 km.
const P = (qid, km, off_km = 0, more = {}) => ({ qid, name: qid, km, off_km, ...more });
const places = [P('intro-x', 0), P('A', 2, 0.1), P('B', 5, 2.5), P('C', 6, 0.2), P('outro-x', 9), P('D', 20)];
assert.equal(about(places, 2.5, null).qid, 'A');
assert.equal(about(places, 5.2, null).qid, 'C', 'B is closer by km but 2.5 km off the road');
assert.equal(about(places, 4.5, places[1]).qid, 'A', 'the story just played wins within 3 km');
assert.equal(about(places, 6, places[1]).qid, 'C', '...but not 4 km on');
assert.equal(about(places, 0, places[0]).qid, 'A', 'intro/outro pieces are never what a question is about');
assert.equal(about(places, 9, null), null, 'only the outro within 3 km');
assert.equal(about(places, 14, null), null);
assert.equal(about(places, null, places[1]), null);
assert.equal(about(places, 2, { qid: 'Q9', name: 'Live', km: null }).qid, 'A', 'a live story has no km');

// liveAnswer(): the 3 nearest places with a source go to the Worker; a flagged answer is not spoken.
const src = 'Srirangapatna fort was built in 1454. Tipu Sultan died here in 1799 during the siege by the British.';
const trip = [P('S', 10, 0, { name: 'Srirangapatna', source: src }), P('N', 11, 0, { name: 'Nimishamba', source: 'A temple on the Kaveri.' }),
  P('X', 12), P('K', 13, 0, { name: 'Karighatta', source: 'A hill.' }), P('Z', 14, 0, { name: 'Zed', source: 'Far.' })];
const live = { ask_prompt: 'Q: $question\nAT: $where\n$question', review: { sensitive_words: ['caste'], dark_themes: ['siege|massacre'] } };
const cfg = { url: 'https://w.example/', token: 't' };
let sent, content;
globalThis.fetch = async (url, init) => { sent = { url, init, body: JSON.parse(init.body) }; return new Response(JSON.stringify({ content })); };

content = 'Sure!\n```json\n{"answer": "The fort here was built in 1454, and the British won it after a long fight."}\n```';
assert.equal(await liveAnswer('Why was the fort built here?', [trip[0], ...trip], cfg, live, 'km 10'),
  'The fort here was built in 1454, and the British won it after a long fight.', 'no dark-theme rule for answers');
assert.equal(sent.url, cfg.url);
assert.equal(sent.init.headers.Authorization, 'Bearer t');
assert.equal(sent.body.system, 'Q: Why was the fort built here?\nAT: km 10\nWhy was the fort built here?');
assert.equal(sent.body.user, `INPUT START\n=== PLACE 1: Srirangapatna ===\n[Srirangapatna]\n${src}\n` +
  '=== PLACE 2: Nimishamba ===\n[Nimishamba]\nA temple on the Kaveri.\n=== PLACE 3: Karighatta ===\n[Karighatta]\nA hill.\nINPUT END');
content = '{"answer": "Built in 1600 by Raja Ramesh."}';
assert.equal(await liveAnswer('Who built it?', trip, cfg, live, 'km 10'), null, 'year and name not in the sources');
content = '{"answer": "Nimishamba is a temple by the Kaveri."}';
assert.equal(await liveAnswer('What is that?', trip, cfg, live, 'km 10'), 'Nimishamba is a temple by the Kaveri.');
content = 'no json';
await assert.rejects(liveAnswer('What?', trip, cfg, live, 'km 10'));
assert.equal(await liveAnswer('What?', [P('X', 12)], cfg, live, 'km 10'), null, 'no sources: nothing to ask');

console.log('ok');
