// Run: node tests/test_engine.mjs   (no browser, no network)
import assert from 'node:assert/strict';
import { Engine, Route } from '../app/engine.js';

// A straight 20 km road due north from (12.0, 77.0): 0.01 deg lat ~ 1.1057 km.
const points = [], km = [];
for (let i = 0; i <= 200; i++) { points.push([12 + i * 0.1 / 110.57, 77]); km.push(i * 0.1); }
const place = (qid, at, tier = 'M') => ({ qid, name: qid, km: at, tier });
const pack = places => ({ route: { points, km }, places });

// Projection: on the road, off to the side, and the window.
const r = new Route(points, km);
let p = r.project(12 + 5 / 110.57, 77.0);
assert.ok(Math.abs(p.km - 5) < 0.01 && p.off < 0.01, JSON.stringify(p));
p = r.project(12 + 5 / 110.57, 77.01);
assert.ok(Math.abs(p.off - 1.08) < 0.02, JSON.stringify(p));
assert.ok(Math.abs(r.pointAt(7.5)[0] - (12 + 7.5 / 110.57)) < 1e-6);

// Drive at 60 km/h, one fix a second; stories "last" durS seconds. Returns [{qid, startKm, t}].
function drive(engine, { fromKm = 0, toKm = 20, kmh = 60, durS = 60, outcome = "completed" } = {}) {
  const log = [];
  let t = 0, at = fromKm, endAt = null;
  while (at <= toKm) {
    const [lat, lon] = r.pointAt(at);
    if (endAt != null && t >= endAt) { engine.storyEnded(t, outcome); endAt = null; }
    const started = engine.update({ lat, lon, accuracy: 5, speed: kmh / 3.6, t });
    if (started) { log.push({ qid: started.qid, startKm: at, t }); endAt = t + durS * 1000; }
    t += 1000;
    at += kmh / 3600;
  }
  return log;
}

// Order, lead time and gap: places told in route order, each started before reaching it, never overlapping.
let e = new Engine(pack([place('A', 2), place('B', 6), place('C', 12, 'L')]));
let log = drive(e);
assert.deepEqual(log.map(x => x.qid), ['A', 'B', 'C']);
for (const x of log) assert.ok(x.startKm < e.places.find(p => p.qid === x.qid).km, `${x.qid} started late`);
for (let i = 1; i < log.length; i++) assert.ok(log[i].t - log[i - 1].t >= (60 + 45) * 1000, 'overlap or no gap');

// No repeats: a second trip with the same told set says nothing.
const told = e.told;
assert.deepEqual(drive(new Engine(pack([place('A', 2), place('B', 6), place('C', 12, 'L')]), told)), []);

// Busy: B and S-tier D are 300 m apart; D is passed while B plays, so it is dropped (missed, not told).
e = new Engine(pack([place('B', 6), place('D', 6.3, 'S')]));
log = drive(e);
assert.deepEqual(log.map(x => x.qid), ['B']);
assert.ok(e.missed.has('D') && !e.told.has('D'));

// ...but an L-tier place passed while busy is still told late (within 2 km).
e = new Engine(pack([place('B', 6), place('E', 6.3, 'L')]));
assert.deepEqual(drive(e).map(x => x.qid), ['B', 'E']);

// Interrupted stories are not marked told.
e = new Engine(pack([place('A', 2)]));
drive(e, { outcome: 'interrupted', toKm: 4 });
assert.ok(!e.told.has('A'));

// Jam: crawling at 2 km/h for 10 minutes up to F must not start it; it starts once traffic moves.
e = new Engine(pack([place('F', 3.3, 'S')]));
let t = 0, at = 3.0;
for (; t < 600; t++, at += 2 / 3600) {
  const [lat, lon] = r.pointAt(at);
  const got = e.update({ lat, lon, accuracy: 5, speed: 2 / 3.6, t: t * 1000 });
  assert.ok(got == null || t < 120, `F started in the jam at ${t}s`);
}
assert.equal(e.playing, null);
let started = null;
for (; !started && t < 700; t++, at += 30 / 3600) {
  const [lat, lon] = r.pointAt(at);
  started = e.update({ lat, lon, accuracy: 5, speed: 30 / 3.6, t: t * 1000 });
}
assert.equal(started?.qid, 'F');

// Off route: fixes 2 km to the side are ignored and nothing plays.
e = new Engine(pack([place('A', 2)]));
for (let t = 0; t < 90; t++) assert.equal(e.update({ lat: 12 + 2 / 110.57, lon: 77.02, accuracy: 5, speed: 16, t: t * 1000 }), null);
assert.ok(e.offRoute);

// Bad accuracy is ignored.
assert.equal(new Engine(pack([place('A', 0.1)])).update({ lat: 12, lon: 77, accuracy: 500, speed: 10, t: 0 }), null);

console.log('ok');
