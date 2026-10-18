const test = require('node:test');
const assert = require('node:assert/strict');
const { create, STORAGE_KEY } = require('../src-tauri/frontend/shot-playback.js');

function clock() {
  let time = 1000;
  let id = 0;
  const jobs = new Map();
  return {
    now: () => time,
    schedule(fn, delay) { jobs.set(++id, { at: time + delay, fn }); return id; },
    cancel(job) { jobs.delete(job); },
    advance(ms) {
      const target = time + ms;
      while (true) {
        const next = [...jobs].filter(([, job]) => job.at <= target).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!next) break;
        time = next[1].at;
        jobs.delete(next[0]);
        next[1].fn();
      }
      time = target;
    },
  };
}

function storage(seed) {
  const values = new Map(Object.entries(seed || {}));
  return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), values };
}

function shot(delay = 0, overrides = {}) {
  return {
    map_id: 'map', origin: [0, 0, 0], destination: [10, 0, 0], delay_ms: delay, miss: false,
    contacts: [{ point: [5, 0, 0], cue: 'cover' }],
    style: {
      color: '#ffcc00', width: 2, travel_ms: 100, impact_ms: 100,
      fire_sound: { waveform: 'noise', frequency: 100, end_frequency: 80, duration_ms: 20, volume: 0.8 },
      cues: { cover: { color: '#00ccff', sound: { waveform: 'sine', frequency: 300, end_frequency: 100, duration_ms: 30, volume: 0.5 } } },
    },
    ...overrides,
  };
}

function harness(options = {}) {
  const timer = clock();
  const played = [];
  const stopped = [];
  const player = create({ storage: options.storage || storage(), now: timer.now, schedule: timer.schedule,
    cancel: timer.cancel, audio: { play(spec, volume) { played.push({ at: timer.now(), spec, volume }); return { stop: () => stopped.push(timer.now()) }; } } });
  return { timer, player, played, stopped };
}

test('uses every backend cue delay in a burst and never invents spacing', async () => {
  const h = harness();
  h.player.enter('session:g1', 4);
  await h.player.unlockAudio();
  h.player.ingest('session:g1', 5, [{ action_id: 'burst', revision: 5, shots: [shot(0), shot(75), shot(150)], unavailable: false }]);
  h.timer.advance(151);
  assert.deepEqual(h.played.filter((item) => item.spec.waveform === 'noise').map((item) => item.at), [1000, 1075, 1150]);
  assert.deepEqual(h.player.snapshot().active.map((item) => item.start_ms), [1000, 1075, 1150]);
});

test('deduplicates action refreshes without restarting visuals or sounds', async () => {
  const h = harness();
  h.player.enter('session:g1', 0);
  await h.player.unlockAudio();
  const record = { action_id: 'one', revision: 1, shots: [shot(40)], unavailable: false };
  h.player.ingest('session:g1', 1, [record]);
  h.timer.advance(10);
  h.player.ingest('session:g1', 1, [record]);
  assert.equal(h.player.snapshot().active.length, 1);
  h.timer.advance(40);
  assert.equal(h.played.filter((item) => item.spec.waveform === 'noise').length, 1);
});

test('scope changes and leave-return establish a fresh baseline without backlog', async () => {
  const h = harness();
  h.player.enter('a:g1', 0);
  await h.player.unlockAudio();
  h.player.ingest('a:g1', 1, [{ action_id: 'old', revision: 1, shots: [shot(100)] }]);
  h.player.enter('b:g1', 8);
  assert.equal(h.player.snapshot().active.length, 0);
  assert.equal(h.player.ingest('a:g1', 2, [{ action_id: 'wrong', revision: 2, shots: [shot()] }]), false);
  h.timer.advance(200);
  assert.equal(h.played.length, 0);
  h.player.leave();
  h.player.enter('a:g2', 12);
  h.player.ingest('a:g2', 12, [{ action_id: 'old-again', revision: 12, shots: [shot()] }]);
  assert.equal(h.player.snapshot().active.length, 0);
});

test('transfers wall-time playback to a child without duplicate audio or finished cues', async () => {
  const main = harness();
  main.player.enter('a:g1', 0);
  await main.player.unlockAudio();
  main.player.ingest('a:g1', 1, [{ action_id: 'one', revision: 1, shots: [shot(50)] }]);
  main.timer.advance(75);
  const packet = main.player.snapshot();
  const child = harness();
  child.timer.advance(75);
  assert.equal(child.player.receive(packet), true);
  assert.equal(child.player.snapshot().active[0].start_ms, 1050);
  child.timer.advance(200);
  assert.equal(child.played.length, 0);
  assert.equal(child.player.snapshot().active.length, 0);
  assert.equal(await child.player.unlockAudio(), false);
});

test('persists settings, stops sound on mute, and changes visuals immediately', async () => {
  const backing = storage();
  const h = harness({ storage: backing });
  h.player.enter('a:g1', 0);
  await h.player.unlockAudio();
  h.player.ingest('a:g1', 1, [{ action_id: 'one', revision: 1, shots: [shot()] }]);
  h.timer.advance(1);
  h.player.configure({ muted: true, visual: 'off' });
  assert.ok(h.stopped.length > 0);
  assert.equal(h.player.frame('map', ([x, y]) => [x, y]), '');
  h.player.configure({ muted: false, visual: 'static', volume: 0.25 });
  const svg = h.player.frame('map', ([x, y]) => [x, y]);
  assert.match(svg, /^<svg/);
  assert.match(svg, /polyline/);
  assert.deepEqual(JSON.parse(backing.values.get(STORAGE_KEY)), { visual: 'static', volume: 0.25, muted: false });
});

test('renders valid fire-sound-null cues without attempting audio', async () => {
  const h = harness();
  h.player.enter('a:g1', 0);
  await h.player.unlockAudio();
  const silent = shot();
  silent.style.fire_sound = null;
  silent.style.cues.cover.sound = null;
  h.player.ingest('a:g1', 1, [{ action_id: 'silent', revision: 1, shots: [silent] }]);
  h.timer.advance(60);
  assert.match(h.player.frame('map', ([x, y]) => [x, y]), /^<svg/);
  assert.equal(h.played.length, 0);
});

test('unavailable records still play their valid projected shots and discard malformed ones', () => {
  const h = harness();
  h.player.enter('a:g1', 0);
  h.player.ingest('a:g1', 1, [{ action_id: 'partial', revision: 1, unavailable: true,
    shots: [shot(), shot(0, { destination: [null, 0, 0] })] }]);
  assert.deepEqual(h.player.snapshot().active.map((item) => item.id), ['partial:0']);
});

test('skip clears playback and sound without rewinding the revision watermark', async () => {
  const h = harness();
  h.player.enter('a:g1', 0);
  await h.player.unlockAudio();
  const record = { action_id: 'one', revision: 1, shots: [shot()] };
  h.player.ingest('a:g1', 1, [record]);
  h.player.skip();
  h.player.ingest('a:g1', 1, [record]);
  assert.equal(h.player.snapshot().revision, 1);
  assert.equal(h.player.snapshot().active.length, 0);
});

test('renders ordered tracer and penetration markers from projected safe points', () => {
  const h = harness();
  h.player.enter('a:g1', 0);
  const value = shot(0, { destination: [15, 0, 0], contacts: [
    { point: [5, 0, 0], cue: 'penetration' }, { point: [10, 0, 0], cue: 'stop' },
  ] });
  value.style.cues = {
    penetration: { color: '#ff00ff', sound: null }, stop: { color: '#ffffff', sound: null },
  };
  h.player.ingest('a:g1', 1, [{ action_id: 'one', revision: 1, shots: [value] }]);
  h.timer.advance(70);
  const svg = h.player.frame('map', ([x, y]) => [x * 10, y]);
  assert.match(svg, /<line/);
  assert.match(svg, /data-cue="penetration"/);
  assert.match(svg, /data-cue="stop"/);
  assert.ok(svg.indexOf('penetration') < svg.indexOf('stop'));
});

test('uses distinct fixed marker shapes in motion and static modes', () => {
  const h = harness();
  h.player.enter('a:g1', 0);
  const value = shot(0, { destination: [20, 0, 0], contacts: [
    { point: [4, 0, 0], cue: 'character' }, { point: [8, 0, 0], cue: 'cover' },
    { point: [12, 0, 0], cue: 'stop' }, { point: [16, 0, 0], cue: 'penetration' },
  ] });
  value.style.cues = {
    character: { color: '#111111', sound: null }, cover: { color: '#222222', sound: null },
    stop: { color: '#333333', sound: null }, penetration: { color: '#444444', sound: null },
  };
  h.player.ingest('a:g1', 1, [{ action_id: 'markers', revision: 1, shots: [value] }]);
  h.timer.advance(90);
  let svg = h.player.frame('map', ([x, y]) => [x, y]);
  assert.match(svg, /<circle[^>]+data-cue="character"/);
  assert.match(svg, /<rect[^>]+data-cue="cover"/);
  assert.match(svg, /<polygon[^>]+data-cue="stop"/);
  assert.match(svg, /<path[^>]+data-cue="penetration"/);
  h.player.configure({ visual: 'static' });
  svg = h.player.frame('map', ([x, y]) => [x, y]);
  assert.match(svg, /<circle[^>]+data-cue="character"/);
  assert.match(svg, /<rect[^>]+data-cue="cover"/);
  assert.match(svg, /<polygon[^>]+data-cue="stop"/);
  assert.match(svg, /<path[^>]+data-cue="penetration"/);
});

test('aligns visual impact arrival to the same 3D segment fractions as audio', () => {
  const h = harness();
  h.player.enter('a:g1', 0);
  const value = shot(0, { destination: [10, 0, 9], contacts: [{ point: [1, 0, 9], cue: 'cover' }] });
  h.player.ingest('a:g1', 1, [{ action_id: 'depth', revision: 1, shots: [value] }]);
  h.timer.advance(40);
  assert.doesNotMatch(h.player.frame('map', ([x, y]) => [x, y]), /data-cue="cover"/);
  h.timer.advance(20);
  assert.match(h.player.frame('map', ([x, y]) => [x, y]), /data-cue="cover"/);
});

test('a miss fades its tracer without impact markers or impact sound', async () => {
  const h = harness();
  h.player.enter('a:g1', 0);
  await h.player.unlockAudio();
  h.player.ingest('a:g1', 1, [{ action_id: 'miss', revision: 1,
    shots: [shot(0, { miss: true })] }]);
  h.timer.advance(50);
  const svg = h.player.frame('map', ([x, y]) => [x, y]);
  assert.match(svg, /<line/);
  assert.doesNotMatch(svg, /data-cue=/);
  assert.deepEqual(h.played.map((item) => item.spec.waveform), ['noise']);
});

test('fails malformed packets, cues, colors, and projections closed', () => {
  const h = harness();
  assert.equal(h.player.enter('', 0), false);
  h.player.enter('a:g1', 0);
  h.player.ingest('a:g1', 1, [{ action_id: 'bad', revision: 1, shots: [shot(0, { origin: [NaN, 0, 0] })] }]);
  assert.equal(h.player.snapshot().active.length, 0);
  h.player.ingest('a:g1', 2, [{ action_id: 'bad-color', revision: 2, shots: [shot(0, { style: { ...shot().style, color: 'red' } })] }]);
  assert.equal(h.player.snapshot().active.length, 0);
  assert.equal(h.player.receive({ scope: 'a:g1', revision: 2, active: 'wrong' }), false);
  h.player.ingest('a:g1', 3, [{ action_id: 'ok', revision: 3, shots: [shot()] }]);
  assert.equal(h.player.frame('map', () => [Infinity, 0]), '');
});

test('strictly rejects coerced points, nonboolean misses, excessive delay and contacts', () => {
  const h = harness();
  h.player.enter('a:g1', 0);
  const invalid = [
    shot(0, { origin: ['0', 0, 0] }), shot(0, { origin: [false, 0, 0] }),
    shot(0, { origin: [null, 0, 0] }), shot(0, { miss: 'false' }), shot(60001),
    shot(0, { contacts: Array.from({ length: 65 }, () => ({ point: [1, 0, 0], cue: 'cover' })) }),
  ];
  invalid.forEach((value, index) => h.player.ingest('a:g1', index + 1,
    [{ action_id: `bad-${index}`, revision: index + 1, shots: [value] }]));
  assert.equal(h.player.snapshot().active.length, 0);
});

test('audio adapter failures and unavailable WebAudio fail quietly', async () => {
  const timer = clock();
  const player = create({ now: timer.now, schedule: timer.schedule, cancel: timer.cancel,
    audio: { play() { throw new Error('device failed'); } } });
  player.enter('a:g1', 0);
  assert.equal(await player.unlockAudio(), true);
  player.ingest('a:g1', 1, [{ action_id: 'one', revision: 1, shots: [shot()] }]);
  assert.doesNotThrow(() => timer.advance(100));

  const previous = globalThis.AudioContext;
  delete globalThis.AudioContext;
  try { assert.equal(await create().unlockAudio(), false); } finally { globalThis.AudioContext = previous; }
});

test('native WebAudio schedules a burst on its audio clock before JS timers run', async () => {
  const timer = clock();
  const starts = [];
  const manualStops = [];
  const parameter = () => ({
    setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {},
  });
  class AudioContextMock {
    constructor() { this.state = 'running'; this.currentTime = 4; this.sampleRate = 1000; this.destination = {}; }
    createGain() { return { gain: parameter(), connect: () => this.destination }; }
    createBuffer() { return { getChannelData: () => new Float32Array(80) }; }
    createBufferSource() {
      return {
        connect: (next) => next,
        start(at) { starts.push(at); },
        stop(at) { if (at === undefined) manualStops.push(true); },
      };
    }
    createBiquadFilter() {
      return { type: '', frequency: parameter(), connect: (next) => next };
    }
    createOscillator() {
      return { type: '', frequency: parameter(), connect: (next) => next, start() {}, stop() {} };
    }
  }
  const previous = globalThis.AudioContext;
  globalThis.AudioContext = AudioContextMock;
  try {
    const player = create({ now: timer.now, schedule: timer.schedule, cancel: timer.cancel });
    player.enter('session:g1', 4);
    assert.equal(await player.unlockAudio(), true);
    player.ingest('session:g1', 5, [{
      action_id: 'burst', revision: 5, shots: [shot(0), shot(80), shot(160)],
    }]);

    assert.deepEqual(starts, [4, 4.08, 4.16]);
    player.skip();
    assert.equal(manualStops.length, 3, 'future native sources remain stoppable');
  } finally {
    globalThis.AudioContext = previous;
  }
});

test('built-in noise synthesis applies the declared frequency sweep', async () => {
  const timer = clock();
  const calls = [];
  const parameter = (name) => ({
    setValueAtTime(value) { calls.push([name, 'start', value]); },
    linearRampToValueAtTime(value) { calls.push([name, 'end', value]); },
    exponentialRampToValueAtTime() {},
  });
  class AudioContextMock {
    constructor() { this.state = 'running'; this.currentTime = 2; this.sampleRate = 1000; this.destination = {}; }
    createGain() { return { gain: parameter('gain'), connect: () => this.destination }; }
    createBuffer() { return { getChannelData: () => new Float32Array(20) }; }
    createBufferSource() { return { connect: (next) => next, start() {}, stop() {} }; }
    createBiquadFilter() {
      const filter = { type: '', frequency: parameter('filter'), connect: (next) => next };
      calls.push(['filter-created', filter]);
      return filter;
    }
  }
  const previous = globalThis.AudioContext;
  globalThis.AudioContext = AudioContextMock;
  try {
    const player = create({ now: timer.now, schedule: timer.schedule, cancel: timer.cancel });
    player.enter('a:g1', 0);
    assert.equal(await player.unlockAudio(), true);
    player.ingest('a:g1', 1, [{ action_id: 'noise', revision: 1, shots: [shot()] }]);
    timer.advance(1);
    const filter = calls.find((item) => item[0] === 'filter-created')[1];
    assert.equal(filter.type, 'bandpass');
    assert.ok(calls.some((item) => item[0] === 'filter' && item[1] === 'start' && item[2] === 100));
    assert.ok(calls.some((item) => item[0] === 'filter' && item[1] === 'end' && item[2] === 80));
  } finally {
    globalThis.AudioContext = previous;
  }
});
