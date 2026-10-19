// Speech to text for "Ask": Chrome's own recognition when online; Whisper in the browser (transformers.js) as
// the offline fallback. The library and model are downloaded once, on request, and then work with no signal:
// transformers.js keeps the model and onnxruntime files in the Cache API, sw.js keeps the library itself.

const TJS = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.2.0/dist/transformers.min.js';
const MODEL = 'onnx-community/whisper-base.en';
const FILES = ['encoder_model_quantized.onnx', 'decoder_model_merged_quantized.onnx']  // dtype q8: about 77 MB
  .map(f => `https://huggingface.co/${MODEL}/resolve/main/onnx/${f}`);  // transformers.js cache keys

let asr = null, loading = null, state = 'none';
let stopNow = null, stopped = false;

export const chromeAvailable = () => !!(window.SpeechRecognition || window.webkitSpeechRecognition);
export const whisperState = () => state;
export const whisperCached = async () => (await Promise.all(FILES.map(f => caches.match(f)))).every(Boolean);

// Safe to call again: later calls share the first download (and its progress callback).
export function loadWhisper(onProgress) {
  loading ??= (async () => {
    state = 'loading';
    const { pipeline } = await import(TJS);
    // 'basic': onnxruntime-web 1.26-dev's extended graph optimisation fails on Whisper's merged q8/int8 decoder
    asr = await pipeline('automatic-speech-recognition', MODEL, {
      dtype: 'q8', device: 'wasm', session_options: { graphOptimizationLevel: 'basic' },
      progress_callback: p => p.status === 'progress_total' && onProgress?.(p.progress / 100) });
    state = 'ready';
  })().catch(e => { state = 'error'; loading = null; throw e; });
  return loading;
}

// 16 kHz mono samples -> text. Whisper writes "[BLANK_AUDIO]" or "(wind)" for silence and noise; drop those.
export async function transcribe(pcm) {
  await loadWhisper();
  return (await asr(pcm)).text.replace(/\[[^\]]*\]|\([^)]*\)/g, '').trim();
}

// Any recorded or fetched audio (webm/opus, mp3...) -> 16 kHz mono Float32Array.
export async function decode16k(blob) {
  const buf = await new OfflineAudioContext(1, 1, 16000).decodeAudioData(await blob.arrayBuffer());
  const out = new Float32Array(buf.length);
  for (let c = 0; c < buf.numberOfChannels; c++) buf.getChannelData(c).forEach((v, i) => out[i] += v / buf.numberOfChannels);
  return out;
}

function chromeListen(maxMs) {
  return new Promise((resolve, reject) => {
    const r = new (window.SpeechRecognition || window.webkitSpeechRecognition)();
    r.lang = 'en-IN'; r.continuous = false; r.interimResults = false; r.maxAlternatives = 1;
    let text = '', err = null;
    r.onresult = e => { text = e.results[0]?.[0]?.transcript ?? ''; };
    r.onerror = e => { err = e.error; };
    r.onend = () => { clearTimeout(timer); stopNow = null; err && !text ? reject(new Error(err)) : resolve(text.trim()); };
    const timer = setTimeout(() => r.stop(), maxMs);
    stopNow = () => r.stop();  // stop(), not abort(): keeps what was already said
    r.start();
  });
}

// ponytail: fixed length (maxMs or a tap), no silence detection; add an AnalyserNode level check if 8 s feels slow
async function record(maxMs) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const rec = new MediaRecorder(stream), chunks = [];
  rec.ondataavailable = e => chunks.push(e.data);
  const done = new Promise(r => { rec.onstop = r; });
  const end = () => rec.state !== 'inactive' && rec.stop();
  rec.start();
  const timer = setTimeout(end, maxMs);
  stopNow = end;
  await done;
  clearTimeout(timer); stopNow = null;
  stream.getTracks().forEach(t => t.stop());
  return new Blob(chunks, { type: rec.mimeType });
}

// beforeRetry: awaited before Whisper records after Chrome failed, so the app can ask the user to say it again.
export async function listen({ online, maxMs = 8000, beforeRetry } = {}) {
  stopped = false;
  const triedChrome = online && chromeAvailable();
  if (triedChrome) {
    try {
      const text = await chromeListen(maxMs);
      if (text || stopped) return { text, via: text ? 'chrome' : 'none' };
    } catch (e) {
      console.info('Chrome speech recognition:', e.message);  // network, no-speech, not-allowed...
      if (stopped || e.message === 'aborted') return { text: '', via: 'none' };
    }
  }
  if (state !== 'ready' && !(await whisperCached().catch(() => false)) || stopped) return { text: '', via: 'none' };
  try {
    if (triedChrome && beforeRetry) await beforeRetry();
    if (stopped) return { text: '', via: 'none' };
    const ready = loadWhisper();  // from the cache, while the user speaks
    ready.catch(() => {});
    const blob = await record(maxMs);
    await ready;
    return { text: await transcribe(await decode16k(blob)), via: 'whisper' };
  } catch (e) {
    console.info('Whisper:', e.message);
    return { text: '', via: 'none' };
  }
}

export function stopListening() {
  stopped = true;
  stopNow?.();
}
