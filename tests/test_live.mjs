// Run: node tests/test_live.mjs   (no browser, no network): live-story checks and the Cloudflare Worker proxy.
import assert from 'node:assert/strict';
import { check, parseReply } from '../app/live.js';
import worker from '../worker/worker.js';

// Same cases as test_check in test_pipeline.py: the JS mirror must flag what the Python check flags.
const rules = { sensitive_words: ['tipu', 'caste'], dark_themes: ['deport|bondage|captiv|prisoner', 'famine'] };
const src = 'The fort was built in 1537 by Kempe Gowda. In 1791 the British took it. Prisoners were deported to Srirangapatna.';
const ok = { script: 'Long ago, Kempe Gowda built a fort. Picture it! The British took it, and prisoners were deported.', claims: [{}] };
assert.deepEqual(check(ok, src, rules), []);
const bad = { script: 'Built in 1540 by Kempe Gowda and his friend Ramesh, the fort had 12 gates.', claims: [{}] };
const flags = check(bad, src, rules);
for (const f of ['year 1540 not in source', "name 'Ramesh' not in source", 'number 12 not in source'])
  assert.ok(flags.includes(f), flags);
assert.equal(flags.filter(f => f.includes('story does not')).length, 1, flags);
assert.ok(check({ script: "Tipu's soldiers marched past.", claims: [{}] }, 'Tipu marched.', rules).some(f => f.startsWith('sensitive')));
assert.ok(check({ script: 'As Wikipedia says, nice.', claims: [] }, 'nice', rules).includes('no claims listed'));

assert.equal(parseReply('Here:\n```json\n[{"n": 1, "script": "A.", "claims": []}]\n```')[0].script, 'A.');
assert.equal(parseReply('{"script": "One.", "claims": []}')[0].script, 'One.');
assert.throws(() => parseReply('no json'));
assert.throws(() => parseReply('[{"script": 5, "claims": []}]'));

// Worker: token required, body forwarded with the secret key, reply reduced to {content}.
const env = { APP_TOKEN: 'secret', LLM_KEY: 'k', LLM_URL: 'https://llm.example/v1/', LLM_MODEL: 'm', ALLOW_ORIGIN: 'http://localhost:8765' };
const post = (body, token = 'secret') => new Request('https://w.example/', {
  method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: typeof body === 'string' ? body : JSON.stringify(body) });
let sent = null;
globalThis.fetch = async (url, init) => {
  sent = { url, init };
  return new Response(JSON.stringify({ choices: [{ message: { content: 'story' } }] }));
};
assert.equal((await worker.fetch(post({ system: 's', user: 'u' }, 'wrong'), env)).status, 401);
assert.equal((await worker.fetch(post({ system: 's', user: 'u' }), { ...env, APP_TOKEN: '' })).status, 401);
assert.equal((await worker.fetch(post('not json'), env)).status, 400);
assert.equal((await worker.fetch(post({ system: 's', user: 'x'.repeat(50000) }), env)).status, 413);
const res = await worker.fetch(post({ system: 's', user: 'u' }), env);
assert.deepEqual(await res.json(), { content: 'story' });
assert.equal(res.headers.get('Access-Control-Allow-Origin'), 'http://localhost:8765');
assert.equal(sent.url, 'https://llm.example/v1/chat/completions');
assert.equal(sent.init.headers.Authorization, 'Bearer k');
assert.deepEqual(JSON.parse(sent.init.body).messages.map(m => m.content), ['s', 'u']);
assert.equal((await worker.fetch(new Request('https://w.example/', { method: 'OPTIONS' }), env)).status, 200);

console.log('ok');
