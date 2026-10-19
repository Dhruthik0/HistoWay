// Histoway live-story proxy (Cloudflare Worker, free tier). Holds the LLM key so it never reaches the phone.
// The phone sends {system, user}; the Worker forwards them to an OpenAI-compatible chat endpoint (Gemini by default).
// Secrets (wrangler secret put NAME): LLM_KEY, APP_TOKEN. Vars in wrangler.toml: LLM_URL, LLM_MODEL, ALLOW_ORIGIN.
const MAX_BODY = 40000;

export default {
  async fetch(req, env) {
    const cors = { 'Access-Control-Allow-Origin': env.ALLOW_ORIGIN, 'Access-Control-Allow-Methods': 'POST',
      'Access-Control-Allow-Headers': 'Authorization, Content-Type' };
    const reply = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...cors, 'Content-Type': 'application/json' } });
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (req.method !== 'POST') return reply({ error: 'POST only' }, 405);
    if (!env.APP_TOKEN || req.headers.get('Authorization') !== `Bearer ${env.APP_TOKEN}`) return reply({ error: 'bad token' }, 401);
    const body = await req.text();
    if (body.length > MAX_BODY) return reply({ error: 'request too large' }, 413);
    let system, user;
    try { ({ system, user } = JSON.parse(body)); } catch { return reply({ error: 'bad JSON' }, 400); }
    if (typeof system !== 'string' || typeof user !== 'string') return reply({ error: 'need system and user strings' }, 400);
    const res = await fetch(env.LLM_URL.replace(/\/$/, '') + '/chat/completions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${env.LLM_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: env.LLM_MODEL, messages: [{ role: 'system', content: system }, { role: 'user', content: user }] }),
    });
    if (!res.ok) return reply({ error: `LLM HTTP ${res.status}` }, res.status === 429 ? 429 : 502);
    return reply({ content: (await res.json()).choices?.[0]?.message?.content ?? '' });
  },
};
