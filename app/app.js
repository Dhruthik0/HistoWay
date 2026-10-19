// Histoway phone app: pack download, GPS or simulator -> engine -> audio. Plain JS, no build step.
import { Engine } from './engine.js';

const $ = id => document.getElementById(id);
const store = {  // localStorage can throw (private mode, blocked storage); the app still works without it
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

let pack = null, packBase = '', engine = null;
let watchId = null, simTimer = null, simClock = null, wakeLock = null;
let current = null;  // {place, which: 'audio'|'more_audio'} being played (or last played)
let paused = false, playToken = 0;
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const audio = new Audio();
const told = () => store.get('told', {});
const now = () => simClock ?? Date.now();

// ---------- packs ----------
async function listPacks() {
  try {
    const list = await (await fetch('packs/index.json', { cache: 'no-store' })).json();
    $('packSel').innerHTML = list.map(p => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join('');
  } catch {
    $('packSel').innerHTML = '';
    $('dlNote').textContent = 'Laptop not reachable. Downloaded packs still work offline.';
  }
  const saved = store.get('pack', null);
  if (saved && ![...$('packSel').options].some(o => o.value === saved))
    $('packSel').insertAdjacentHTML('afterbegin', `<option value="${esc(saved)}">${esc(saved)} (downloaded)</option>`);
  if (saved) $('packSel').value = saved;
}

async function download() {
  const id = $('packSel').value;
  if (!id) return;
  const base = `packs/${id}/`;
  $('dl').hidden = false; $('download').disabled = true;
  try {
    const p = await (await fetch(base + 'pack.json', { cache: 'no-store' })).json();
    const cache = await caches.open('pack-' + id);
    await cache.put(base + 'pack.json', new Response(JSON.stringify(p), { headers: { 'Content-Type': 'application/json' } }));
    const urls = p.places.flatMap(x => [x.audio, x.more_audio]).filter(Boolean).map(a => base + a);
    $('dl').max = urls.length || 1;
    for (const [i, u] of urls.entries()) {
      if (!(await cache.match(u))) await cache.add(u);
      $('dl').value = i + 1;
    }
    await navigator.storage?.persist?.();
    store.set('pack', id);
    $('dlNote').textContent = `Downloaded: ${p.places.length} stories, ${urls.length} audio files. Works offline now.`;
    await loadPack(id);
  } catch (e) {
    $('dlNote').textContent = 'Download failed: ' + e.message;
  } finally {
    $('download').disabled = false;
  }
}

async function loadPack(id) {
  const res = await caches.match(`packs/${id}/pack.json`);
  if (!res) return;
  pack = await res.json();
  packBase = `packs/${id}/`;
  $('packTitle').textContent = `${pack.title} · ${pack.total_km} km · ${pack.places.length} stories`;
  $('start').disabled = $('sim').disabled = false;
  showTold();
}

// ---------- audio ----------
function chime() {
  return new Promise(resolve => {
    try {
      const ctx = new AudioContext(), t = ctx.currentTime;
      for (const [f, at] of [[660, 0], [880, 0.18]]) {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
        g.gain.setValueAtTime(0.0001, t + at); g.gain.exponentialRampToValueAtTime(0.25, t + at + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.35);
        o.start(t + at); o.stop(t + at + 0.4);
      }
      setTimeout(() => { ctx.close(); resolve(); }, 700);
    } catch { resolve(); }
  });
}

function speakDevice(text, done) {  // fallback when a story has no MP3
  speechSynthesis.cancel();
  const voices = speechSynthesis.getVoices(), lang = v => v.lang.replace('_', '-');
  const voice = voices.find(v => lang(v) === 'en-IN') || voices.find(v => lang(v).startsWith('en'));
  const parts = text.match(/[^.!?]+[.!?]+["')\]]*\s*|[^.!?]+$/g) || [text];  // Chrome cuts long utterances off
  parts.forEach((s, i) => {
    const u = new SpeechSynthesisUtterance(s.trim());
    if (voice) u.voice = voice;
    u.rate = 1.05; u.pitch = 1.1;
    if (i === parts.length - 1) u.onend = done;
    speechSynthesis.speak(u);
  });
}

async function play(place, which = 'audio') {
  stopAudio();
  const token = playToken;
  current = { place, which };
  paused = false;
  engine.busy(place);
  $('now').textContent = (which === 'more_audio' ? 'More: ' : '') + place.name;
  if ('mediaSession' in navigator) navigator.mediaSession.metadata = new MediaMetadata({ title: place.name, artist: 'Histoway', album: pack.title });
  setButtons();
  await chime();
  if (token !== playToken) return;  // skipped or replaced during the chime
  const file = place[which];
  if (file) {
    try {
      const res = (await caches.match(packBase + file)) || (await fetch(packBase + file));
      audio.src = URL.createObjectURL(await res.blob());
      await audio.play();
      return;
    } catch (e) { console.warn('mp3 failed, using device voice', e); }
  }
  speakDevice(which === 'audio' ? place.script : place.more_script, () => finished('completed'));
}

function stopAudio() {
  playToken++;
  audio.pause();
  if (audio.src) URL.revokeObjectURL(audio.src);
  audio.removeAttribute('src');
  speechSynthesis.cancel();
}

function finished(outcome) {
  if (!engine?.playing) return;
  const p = engine.storyEnded(now(), outcome);
  if (p && outcome !== 'interrupted') {
    const log = told();
    if (!log[p.qid]) { log[p.qid] = { at: Date.now(), outcome, name: p.name }; store.set('told', log); }
    showTold();
  }
  $('now').textContent = ' ';
  setButtons();
}
audio.onended = () => finished('completed');

function setButtons() {
  const playing = !!engine?.playing;
  $('pause').disabled = $('skip').disabled = !playing;
  $('pause').textContent = paused ? 'Resume' : 'Pause';
  $('replay').disabled = !current;
  $('more').disabled = !current?.place.more_script;
}

$('pause').onclick = () => {
  paused = !paused;
  if (paused) { audio.pause(); speechSynthesis.pause(); } else { if (audio.src) audio.play(); speechSynthesis.resume(); }
  setButtons();
};
$('skip').onclick = () => { stopAudio(); finished('skipped'); };
$('replay').onclick = () => current && play(current.place, current.which);
$('more').onclick = () => current?.place.more_script && play(current.place, 'more_audio');
if ('mediaSession' in navigator) {
  navigator.mediaSession.setActionHandler('pause', () => !paused && $('pause').click());
  navigator.mediaSession.setActionHandler('play', () => paused && $('pause').click());
  navigator.mediaSession.setActionHandler('nexttrack', () => $('skip').click());
}

// ---------- journey ----------
function onFix(fix) {
  const place = engine.update(fix);
  const next = engine.nextPlace();
  const km = engine.km == null ? '?' : engine.km.toFixed(1);
  $('status').innerHTML = engine.offRoute ? '<span class="warn">Off the planned route: staying quiet until you are back on it.</span>'
    : `km ${km} of ${pack.total_km} · ${(engine.speed * 3600).toFixed(0)} km/h` +
      (next ? ` · next: ${esc(next.name)} in ${Math.max(0, next.km - (engine.km ?? 0)).toFixed(1)} km` : ' · no more stories') +
      (simClock ? ' · SIMULATION' : '');
  if (place) play(place);
}

function begin() {
  engine = new Engine(pack, new Set(Object.keys(told())));
  current = null;
  $('start').textContent = 'Stop journey';
  requestWakeLock();
  setButtons();
}

function end() {
  if (watchId != null) navigator.geolocation.clearWatch(watchId);
  clearInterval(simTimer);
  watchId = simTimer = simClock = null;
  stopAudio();
  if (engine?.playing) finished('interrupted');
  engine = null;
  wakeLock?.release?.(); wakeLock = null;
  $('start').textContent = 'Start journey';
  $('status').textContent = 'Stopped';
  setButtons();
}

$('start').onclick = () => {
  if (engine) return end();
  begin();
  $('status').textContent = 'Waiting for GPS...';
  watchId = navigator.geolocation.watchPosition(
    p => onFix({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy, speed: p.coords.speed, t: p.timestamp }),
    e => { $('status').innerHTML = `<span class="warn">GPS: ${e.message}</span>`; },
    { enableHighAccuracy: true, maximumAge: 0, timeout: 30000 });
};

function simulate(track) {  // track: [{lat, lon, t}] from a GPX file, or null to drive the route itself
  if (engine) end();
  begin();
  const mult = +$('simMult').value, kmh = +$('simSpeed').value;
  let km = +$('simFrom').value, i = 0;
  simClock = track ? track[0].t : Date.now();
  simTimer = setInterval(() => {
    simClock += 1000 * mult;
    if (track) {
      while (i < track.length - 1 && track[i + 1].t <= simClock) i++;
      if (i >= track.length - 1) return end();
      onFix({ lat: track[i].lat, lon: track[i].lon, accuracy: 5, speed: null, t: simClock });
    } else {
      km += kmh / 3600 * mult;
      if (km > pack.total_km) return end();
      const [lat, lon] = engine.route.pointAt(km);
      onFix({ lat, lon, accuracy: 5, speed: kmh / 3.6, t: simClock });
    }
  }, 1000);
}
$('sim').onclick = () => simulate(null);
$('gpx').onchange = async e => {
  const file = e.target.files[0];
  if (!file || !pack) return;
  const doc = new DOMParser().parseFromString(await file.text(), 'application/xml');
  const pts = [...doc.getElementsByTagName('trkpt')].map(n => ({
    lat: +n.getAttribute('lat'), lon: +n.getAttribute('lon'), t: Date.parse(n.getElementsByTagName('time')[0]?.textContent) }));
  if (pts.length < 2 || pts.some(p => Number.isNaN(p.t))) return alertNote('That GPX has no timed track points.');
  simulate(pts);
};
const alertNote = msg => { $('dlNote').textContent = msg; };

async function requestWakeLock() {
  try { wakeLock = await navigator.wakeLock?.request('screen'); } catch {}
}
document.addEventListener('visibilitychange', () => { if (engine && document.visibilityState === 'visible') requestWakeLock(); });

// ---------- told log ----------
function showTold() {
  const log = told(), names = Object.values(log);
  $('toldCount').textContent = `${names.length} places remembered`;
  const ids = new Set(pack?.places.map(p => p.qid));
  $('log').innerHTML = Object.entries(log).filter(([q]) => ids.has(q)).sort((a, b) => a[1].at - b[1].at)
    .map(([, v]) => `<li>${esc(v.name)}${v.outcome === 'skipped' ? ' (skipped)' : ''}</li>`).join('');
}
$('resetTold').onclick = () => { store.set('told', {}); if (engine) engine.told.clear(); showTold(); };

// ---------- start-up ----------
$('download').onclick = download;
$('packSel').onchange = () => loadPack($('packSel').value);
if (!store.get('driverOk', false)) $('driverNote').hidden = false;
$('driverOk').onclick = () => { store.set('driverOk', true); $('driverNote').hidden = true; };
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js');
await listPacks();
if ($('packSel').value) await loadPack($('packSel').value);
if (!pack) $('setup').open = true;
setButtons();
