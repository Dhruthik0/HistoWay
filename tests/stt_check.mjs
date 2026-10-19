// Run: node --experimental-websocket tests/stt_check.mjs [workdir]   (needs network once, google-chrome, .venv edge-tts)
// Offline Whisper check in headless Chrome: a copy of app/ is served, a Neerja clip asking a question is transcribed
// online (downloading the library and model), then Chrome is restarted with every network request failing and
// the app server stopped, and the clip is transcribed again, both directly and through listen() with the clip
// played into a fake microphone. Downloads about 100 MB into a fresh Chrome profile in workdir.
import { spawn, execFileSync } from 'node:child_process';
import { cpSync, existsSync, writeFileSync, mkdtempSync } from 'node:fs';
import { createServer } from 'node:net';
import { join } from 'node:path';
import assert from 'node:assert/strict';

const root = new URL('..', import.meta.url).pathname;
const work = process.argv[2] || mkdtempSync('/tmp/stt-check-');
const www = join(work, 'www'), profile = join(work, 'chrome'), CDP = 9444;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const port = await new Promise(r => { const s = createServer().listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });
const procs = [];

cpSync(join(root, 'app'), www, { recursive: true });
if (!existsSync(join(www, 'ask.js'))) writeFileSync(join(www, 'ask.js'), '');  // sw.js precaches it
execFileSync(join(root, '.venv/bin/edge-tts'), ['--voice', 'en-IN-NeerjaNeural', '--text', 'Tell me the dark history of this place', '--write-media', join(www, 'q.mp3')]);

async function chrome(flags) {
  const p = spawn('google-chrome', ['--headless=new', `--remote-debugging-port=${CDP}`, `--user-data-dir=${profile}`, '--no-first-run',
    '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', ...flags, 'about:blank'], { stdio: 'ignore' });
  procs.push(p);
  let ver;
  for (let i = 0; i < 50 && !ver; i++) { await sleep(200); ver = await fetch(`http://127.0.0.1:${CDP}/json/version`).then(r => r.json()).catch(() => null); }
  const ws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r, { once: true }));
  let id = 0, sessionId;
  const waiting = new Map(), bytes = {};
  ws.addEventListener('message', ({ data }) => {
    const m = JSON.parse(data);
    if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
    if (m.method === 'Network.responseReceived') bytes[m.params.requestId] = { url: m.params.response.url };
    if (m.method === 'Network.loadingFinished' && bytes[m.params.requestId]) bytes[m.params.requestId].n = m.params.encodedDataLength;
    if (m.method === 'Runtime.consoleAPICalled') console.log('  page:', m.params.args.map(a => a.value ?? a.description).join(' ').slice(0, 300));
  });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id; waiting.set(i, m => m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result));
    ws.send(JSON.stringify({ id: i, method, params, sessionId }));
  });
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  ({ sessionId } = await send('Target.attachToTarget', { targetId, flatten: true }));
  await send('Network.enable'); await send('Runtime.enable');
  const run = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
    return r.result.value;
  };
  return { send, run, bytes, close: () => { ws.close(); p.kill(); return new Promise(r => p.once('exit', r)); } };
}

const TEST = `(async () => {
  const stt = await import('./stt.js');
  let t = performance.now();
  await stt.loadWhisper(f => {});
  const load = performance.now() - t;
  const pcm = await stt.decode16k(await (await fetch('q.mp3')).blob());
  t = performance.now();
  const text = await stt.transcribe(pcm);
  return { text, loadMs: Math.round(load), asrMs: Math.round(performance.now() - t), seconds: pcm.length / 16000, state: stt.whisperState() };
})()`;
const want = /tell me the dark history of this place/i;

try {
  const server = spawn('python3', ['-m', 'http.server', String(port), '--bind', '127.0.0.1', '-d', www], { stdio: 'ignore' });
  procs.push(server);
  await sleep(500);
  let c = await chrome([]);
  await c.send('Page.navigate', { url: `http://127.0.0.1:${port}/index.html` });
  await c.run('navigator.serviceWorker.ready.then(() => new Promise(r => setTimeout(r, 1000)))');
  const online = await c.run(TEST);
  console.log('online:', online);
  assert.match(online.text, want);
  // a 48 kHz WAV of the clip (with silence after it) for the fake microphone
  const wav = await c.run(`(async () => {
    const b = await new OfflineAudioContext(1, 1, 48000).decodeAudioData(await (await fetch('q.mp3')).arrayBuffer());
    const s = new Float32Array(b.length + 48000 * 7); s.set(b.getChannelData(0), 24000);
    const v = new DataView(new ArrayBuffer(44 + s.length * 2)), w = (o, str) => [...str].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
    w(0, 'RIFF'); v.setUint32(4, 36 + s.length * 2, true); w(8, 'WAVEfmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
    v.setUint16(22, 1, true); v.setUint32(24, 48000, true); v.setUint32(28, 96000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    w(36, 'data'); v.setUint32(40, s.length * 2, true);
    s.forEach((x, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 32767, true));
    let bin = ''; new Uint8Array(v.buffer).forEach(x => bin += String.fromCharCode(x)); return btoa(bin);
  })()`);
  writeFileSync(join(work, 'q.wav'), Buffer.from(wav, 'base64'));
  const byHost = {};
  for (const { url, n = 0 } of Object.values(c.bytes)) { const h = new URL(url).host; byHost[h] = (byHost[h] ?? 0) + n; }
  console.log('downloaded MB by host:', Object.fromEntries(Object.entries(byHost).map(([h, n]) => [h, +(n / 1e6).toFixed(1)])));
  console.log('cache storage MB:', await c.run('navigator.storage.estimate().then(e => +(e.usage / 1e6).toFixed(1))'));
  await c.close(); server.kill();
  await sleep(1000);

  // Offline: the app server is gone and every other request goes to a dead proxy.
  c = await chrome(['--proxy-server=127.0.0.1:9', '--proxy-bypass-list=<-loopback>', `--use-file-for-fake-audio-capture=${join(work, 'q.wav')}%noloop`]);
  await c.send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await c.send('Page.navigate', { url: `http://127.0.0.1:${port}/index.html` });
  await sleep(1500);
  assert.equal(await c.run('document.title'), 'Histoway');
  const offline = await c.run(TEST);
  console.log('offline:', offline);
  assert.match(offline.text, want);
  await c.send('Page.reload'); await sleep(1500);
  const heard = await c.run(`(async () => { const t = performance.now(); const r = await (await import('./stt.js')).listen({ online: false });
    return { ...r, ms: Math.round(performance.now() - t) }; })()`);
  console.log('offline listen() through the fake microphone:', heard);
  assert.equal(heard.via, 'whisper');
  assert.match(heard.text, /dark history/i);
  await c.close();
  console.log('ok');
} finally {
  for (const p of procs) p.kill();
}
