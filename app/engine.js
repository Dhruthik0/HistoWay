// Histoway trigger engine: GPS fix -> km along the route -> which story starts now.
// Pure logic, no DOM, so tests/test_engine.mjs can drive it in node. Times are ms, distances km.

export const DEFAULTS = {
  maxAccuracyM: 100,                  // ignore fixes worse than this
  offRouteKm: 0.5, offRouteS: 60,     // further than this from the road for this long = off route (stay quiet)
  gapS: 45,                           // silence between stories
  jamKmh: 5, jamS: 120,               // slower than this for this long = jam or stop: start nothing new
  leadS: { S: 20, M: 30, L: 45 },     // start this many seconds of driving before the place
  minLeadKm: 0.15,
  lateKm: { S: 0.2, M: 0.5, L: 2 },   // still worth telling this far past the place (a place's late_km wins)
  againGapS: 10,                      // silence after a one-line "on your left again"
  minScore: -Infinity, skipPenalty: 0, // from the pack: each Skip lowers later places sharing a type with it
};

const KM_PER_DEG = 110.57;

export class Route {
  constructor(points, km) {
    this.lat0 = Math.cos(points.reduce((s, p) => s + p[0], 0) / points.length * Math.PI / 180);
    this.x = Float64Array.from(points, p => p[1] * KM_PER_DEG * this.lat0);
    this.y = Float64Array.from(points, p => p[0] * KM_PER_DEG);
    this.km = Float64Array.from(km);
    this.points = points;
  }

  index(km) {  // last point index with this.km[i] <= km
    let lo = 0, hi = this.km.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (this.km[mid] <= km) lo = mid; else hi = mid - 1; }
    return lo;
  }

  // Nearest point on the route, optionally only between fromKm and toKm: {km, off}.
  // ponytail: linear scan over the window (whole route when no window); fine at ~1 fix/s even for 3000 km.
  project(lat, lon, fromKm = -Infinity, toKm = Infinity) {
    const px = lon * KM_PER_DEG * this.lat0, py = lat * KM_PER_DEG;
    const i0 = fromKm === -Infinity ? 0 : this.index(fromKm);
    const i1 = toKm === Infinity ? this.km.length - 1 : Math.min(this.index(toKm) + 1, this.km.length - 1);
    let best = { km: 0, off: Infinity };
    for (let i = i0; i < i1; i++) {
      const ax = this.x[i], ay = this.y[i], dx = this.x[i + 1] - ax, dy = this.y[i + 1] - ay;
      const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy || 1e-12)));
      const off = Math.hypot(ax + dx * t - px, ay + dy * t - py);
      if (off < best.off) best = { km: this.km[i] + t * (this.km[i + 1] - this.km[i]), off };
    }
    return best;
  }

  pointAt(km) {  // [lat, lon] at a km marker, for the simulator
    const i = Math.min(this.index(km), this.km.length - 2);
    const t = Math.max(0, Math.min(1, (km - this.km[i]) / (this.km[i + 1] - this.km[i] || 1)));
    const [a, b] = [this.points[i], this.points[i + 1]];
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  }
}

export class Engine {
  constructor(pack, told = new Set(), opts = {}) {
    this.o = { ...DEFAULTS, ...pack.engine, ...opts };
    this.route = new Route(pack.route.points, pack.route.km);
    this.places = [...pack.places].sort((a, b) => a.km - b.km);
    this.told = told;          // qids already told on any trip (persisted by the app)
    this.missed = new Set();   // passed without telling on this trip
    this.played = new Set();   // started on this trip, as a story or a one-line reminder
    this.skips = {};           // type -> Skip presses on this trip
    this.km = null; this.t = null; this.speed = 0;  // speed in km/s
    this.playing = null; this.quietUntil = -Infinity;
    this.offSince = null; this.offRoute = false; this.stoppedSince = null;
  }

  // One GPS fix {lat, lon, accuracy (m), speed (m/s or null), t (ms)} -> a place to start telling, or null.
  update({ lat, lon, accuracy = 0, speed = null, t }) {
    if (accuracy > this.o.maxAccuracyM) return null;
    const dt = this.t == null ? 0 : (t - this.t) / 1000;
    let p = null;
    if (this.km != null)  // search near where we were, so a parallel stretch further on (switchbacks) cannot win
      p = this.route.project(lat, lon, this.km - 0.5, this.km + 2 + dt * 0.05);
    if (!p || p.off > this.o.offRouteKm) p = this.route.project(lat, lon);
    if (p.off > this.o.offRouteKm) {
      this.offSince ??= t;
      this.offRoute = t - this.offSince > this.o.offRouteS * 1000;
      this.t = t;
      return null;
    }
    this.offSince = null; this.offRoute = false;
    const moved = this.km == null ? 0 : Math.max(0, p.km - this.km);  // km never runs backwards (GPS jitter)
    const v = speed != null && speed >= 0 ? speed / 1000 : dt > 0 ? moved / dt : 0;
    this.speed = this.km == null ? v : 0.5 * this.speed + 0.5 * v;
    this.km = this.km == null ? p.km : this.km + moved;
    this.t = t;
    if (this.speed * 3600 < this.o.jamKmh) this.stoppedSince ??= t; else this.stoppedSince = null;
    return this.next(t);
  }

  next(t) {
    if (this.playing || this.km == null || t < this.quietUntil) return null;
    if (this.stoppedSince != null && t - this.stoppedSince > this.o.jamS * 1000) return null;
    for (const p of this.places) {
      if (p.km - this.km > 5) break;  // sorted by km: nothing further is due yet
      const told = this.told.has(p.qid);
      if (this.missed.has(p.qid) || this.played.has(p.qid) || (told && !p.again)) continue;
      if (p.km < this.km - (p.late_km ?? this.o.lateKm[told ? 'S' : p.tier])) { this.missed.add(p.qid); continue; }
      if (!told && p.score != null && this.penalty(p) > 0 && p.score - this.penalty(p) < this.o.minScore) {
        this.missed.add(p.qid);
        continue;
      }
      if (p.km - this.km <= Math.max(this.o.minLeadKm, this.speed * this.o.leadS[told ? 'S' : p.tier])) {
        this.played.add(p.qid);
        this.playing = told ? { ...p, reminder: true } : p;
        return this.playing;
      }
    }
    return null;
  }

  penalty(p) { return (p.types ?? []).reduce((sum, ty) => sum + (this.skips[ty] ?? 0), 0) * this.o.skipPenalty; }

  busy(place) { this.playing = place; }  // replay / tell me more: hold off new stories

  // outcome: completed | skipped | interrupted. Only "interrupted" leaves the place untold.
  storyEnded(t, outcome) {
    const p = this.playing;
    if (p && outcome !== 'interrupted') this.told.add(p.qid);
    if (p && outcome === 'skipped' && !p.reminder) for (const ty of p.types ?? []) this.skips[ty] = (this.skips[ty] ?? 0) + 1;
    this.playing = null;
    this.quietUntil = t + (p?.gap_s ?? (p?.reminder ? this.o.againGapS : this.o.gapS)) * 1000;  // gap_s: the opening is a lead-in
    return p;
  }

  nextPlace() {  // for the status line
    return this.places.find(p => p !== this.playing && !this.told.has(p.qid) && !this.missed.has(p.qid) && p.km >= (this.km ?? 0) - this.o.lateKm[p.tier]);
  }
}
