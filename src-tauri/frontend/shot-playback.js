(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.SylviaShotPlayback = api;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this), function (root) {
  'use strict';

  const STORAGE_KEY = 'sylvia-gm-shot-effects-v1';
  const CONTACT_KINDS = new Set(['character', 'cover', 'stop', 'penetration']);
  const WAVEFORMS = new Set(['noise', 'sine', 'triangle']);
  const DEFAULT_COLOR = '#f5cc55';

  function finite(value, minimum, maximum) {
    if (value === null || value === undefined || typeof value === 'boolean' || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) && number >= minimum && number <= maximum ? number : null;
  }

  function point(value) {
    if (!Array.isArray(value) || value.length !== 3) return null;
    if (value.some((item) => typeof item !== 'number')) return null;
    const result = value.slice();
    return result.every(Number.isFinite) ? result : null;
  }

  function color(value) {
    return typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value) ? value.toLowerCase() : null;
  }

  function sound(value) {
    if (value === null) return null;
    if (!value || typeof value !== 'object' || !WAVEFORMS.has(value.waveform)) return undefined;
    const result = {
      waveform: value.waveform,
      frequency: finite(value.frequency, 40, 12000),
      end_frequency: finite(value.end_frequency, 40, 12000),
      duration_ms: finite(value.duration_ms, 10, 500),
      volume: finite(value.volume, 0, 1),
    };
    return Object.values(result).some((item) => item === null) ? undefined : result;
  }

  function cue(value) {
    if (!value || typeof value !== 'object' || typeof value.map_id !== 'string' || !value.map_id) return null;
    const origin = point(value.origin);
    const destination = point(value.destination);
    const delay = finite(value.delay_ms, 0, 60000);
    const style = value.style;
    if (!origin || !destination || delay === null || !style || typeof style !== 'object') return null;
    const fireSound = sound(style.fire_sound);
    if (fireSound === undefined) return null;
    const styles = {};
    if (!style.cues || typeof style.cues !== 'object') return null;
    for (const kind of CONTACT_KINDS) {
      const item = style.cues[kind];
      if (!item) continue;
      const itemColor = color(item.color);
      const itemSound = sound(item.sound);
      if (!itemColor || itemSound === undefined) return null;
      styles[kind] = { color: itemColor, sound: itemSound };
    }
    if (!Array.isArray(value.contacts) || value.contacts.length > 64 || typeof value.miss !== 'boolean') return null;
    const contacts = [];
    for (const item of value.contacts) {
      const location = point(item && item.point);
      if (!location || !CONTACT_KINDS.has(item.cue) || !styles[item.cue]) return null;
      contacts.push({ point: location, cue: item.cue });
    }
    const result = {
      map_id: value.map_id,
      origin,
      destination,
      delay_ms: delay,
      miss: value.miss === true,
      contacts,
      style: {
        color: color(style.color),
        width: finite(style.width, 1, 8),
        travel_ms: finite(style.travel_ms, 40, 250),
        impact_ms: finite(style.impact_ms, 60, 400),
        fire_sound: fireSound,
        cues: styles,
      },
    };
    return !result.style.color || result.style.width === null || result.style.travel_ms === null ||
      result.style.impact_ms === null ? null : result;
  }

  function defaultPreferences(storage) {
    const reduced = root && typeof root.matchMedia === 'function' && root.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let saved = null;
    try { saved = JSON.parse(storage && storage.getItem(STORAGE_KEY)); } catch (_) { /* use defaults */ }
    const visual = saved && ['motion', 'static', 'off'].includes(saved.visual) ? saved.visual : (reduced ? 'static' : 'motion');
    const volume = finite(saved && saved.volume, 0, 1);
    return { visual, volume: volume === null ? 0.5 : volume, muted: saved && typeof saved.muted === 'boolean' ? saved.muted : false };
  }

  function copy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function create({ storage, now = Date.now, schedule = setTimeout, cancel = clearTimeout, audio } = {}) {
    let scope = null;
    let revision = 0;
    let active = [];
    let preferences = defaultPreferences(storage);
    let child = false;
    let unlocked = false;
    let context = null;
    const actions = new Set();
    const timers = new Set();
    const sounds = new Set();

    function stopSounds() {
      sounds.forEach((handle) => { try { handle.stop(); } catch (_) { /* already stopped */ } });
      sounds.clear();
    }

    function clearPlayback() {
      timers.forEach(cancel);
      timers.clear();
      stopSounds();
      active = [];
    }

    function prune(at = now()) {
      active = active.filter((item) => at < item.start_ms + item.cue.style.travel_ms + item.cue.style.impact_ms);
    }

    function synthesize(spec, volume, delay = 0, audioAnchor = null) {
      if (audio && typeof audio.play === 'function') return audio.play(spec, volume) || null;
      const AudioContext = root && (root.AudioContext || root.webkitAudioContext);
      if (!AudioContext || !unlocked) return null;
      context ||= new AudioContext();
      const start = (audioAnchor === null ? context.currentTime : audioAnchor) + delay / 1000;
      const end = start + spec.duration_ms / 1000;
      const gain = context.createGain();
      gain.gain.setValueAtTime(spec.volume * volume, start);
      gain.gain.exponentialRampToValueAtTime(0.0001, end);
      let source;
      if (spec.waveform === 'noise') {
        const buffer = context.createBuffer(1, Math.ceil(context.sampleRate * spec.duration_ms / 1000), context.sampleRate);
        const data = buffer.getChannelData(0);
        for (let index = 0; index < data.length; index += 1) data[index] = Math.random() * 2 - 1;
        source = context.createBufferSource();
        source.buffer = buffer;
        const filter = context.createBiquadFilter();
        filter.type = 'bandpass';
        filter.frequency.setValueAtTime(spec.frequency, start);
        filter.frequency.linearRampToValueAtTime(spec.end_frequency, end);
        source.connect(filter).connect(gain).connect(context.destination);
      } else {
        source = context.createOscillator();
        source.type = spec.waveform;
        source.frequency.setValueAtTime(spec.frequency, start);
        source.frequency.linearRampToValueAtTime(spec.end_frequency, end);
      }
      if (spec.waveform !== 'noise') source.connect(gain).connect(context.destination);
      source.start(start);
      source.stop(end);
      return { stop() { try { source.stop(); } catch (_) { /* already stopped */ } } };
    }

    function play(spec, delay = 0, audioAnchor = null) {
      if (child || !unlocked || preferences.muted || preferences.volume === 0 || !spec) return;
      let handle;
      try { handle = synthesize(spec, preferences.volume, delay, audioAnchor); } catch (_) { return; }
      if (!handle) return;
      sounds.add(handle);
      const timer = schedule(
        () => { timers.delete(timer); sounds.delete(handle); },
        delay + spec.duration_ms,
      );
      timers.add(timer);
    }

    // Native starts use the audio clock so map redraws cannot bunch overdue timer callbacks.
    function queueSound(spec, delay, audioAnchor = null) {
      if (!spec || child) return;
      if (!audio && audioAnchor !== null) {
        play(spec, Math.max(0, delay), audioAnchor);
        return;
      }
      const timer = schedule(() => { timers.delete(timer); play(spec); }, Math.max(0, delay));
      timers.add(timer);
    }

    function addCue(id, value, start, withAudio, audioAnchor = null, audioDelay = null) {
      active.push({ id, start_ms: start, cue: value });
      const remaining = start - now();
      if (withAudio) {
        const fireDelay = audioDelay === null ? remaining : audioDelay;
        queueSound(value.style.fire_sound, fireDelay, audioAnchor);
        if (value.miss) return scheduleExpiry(value, start);
        let travelled = 0;
        const route = [value.origin, ...value.contacts.map((item) => item.point), value.destination];
        const lengths = route.slice(1).map((item, index) => Math.hypot(...item.map((number, axis) => number - route[index][axis])));
        const total = lengths.reduce((sum, item) => sum + item, 0) || 1;
        value.contacts.forEach((contact, index) => {
          travelled += lengths[index];
          queueSound(
            value.style.cues[contact.cue].sound,
            fireDelay + value.style.travel_ms * travelled / total,
            audioAnchor,
          );
        });
      }
      scheduleExpiry(value, start);
    }

    function scheduleExpiry(value, start) {
      const lifetime = Math.max(0, start + value.style.travel_ms + value.style.impact_ms - now());
      const timer = schedule(() => { timers.delete(timer); prune(); }, lifetime);
      timers.add(timer);
    }

    function enter(nextScope, baseline) {
      if (typeof nextScope !== 'string' || !nextScope || !Number.isSafeInteger(baseline) || baseline < 0) return false;
      if (scope === nextScope) {
        revision = Math.max(revision, baseline);
        return true;
      }
      clearPlayback();
      actions.clear();
      scope = nextScope;
      revision = baseline;
      child = false;
      return true;
    }

    function leave() {
      clearPlayback();
      actions.clear();
      scope = null;
      revision = 0;
    }

    function ingest(recordScope, recordRevision, records) {
      if (recordScope !== scope || !Number.isSafeInteger(recordRevision) || recordRevision < 0 || !Array.isArray(records)) return false;
      const received = now();
      const audioAnchor = !audio && unlocked && context ? context.currentTime : null;
      const ordered = records.slice().sort((a, b) => Number(a && a.revision) - Number(b && b.revision));
      for (const record of ordered) {
        if (!record || typeof record.action_id !== 'string' || !record.action_id || !Number.isSafeInteger(record.revision) || record.revision < 0) continue;
        if (record.revision <= revision || record.revision > recordRevision || actions.has(record.action_id)) continue;
        actions.add(record.action_id);
        revision = record.revision;
        if (!Array.isArray(record.shots)) continue;
        record.shots.forEach((raw, index) => {
          const value = cue(raw);
          if (value) addCue(
            `${record.action_id}:${index}`, value, received + value.delay_ms, true,
            audioAnchor, value.delay_ms,
          );
        });
      }
      revision = Math.max(revision, recordRevision);
      return true;
    }

    function snapshot() {
      prune();
      return { scope, revision, active: copy(active), preferences: copy(preferences) };
    }

    function receive(packet) {
      if (!packet || typeof packet !== 'object' || typeof packet.scope !== 'string' || !packet.scope ||
          !Number.isSafeInteger(packet.revision) || packet.revision < 0 || !Array.isArray(packet.active)) return false;
      clearPlayback();
      actions.clear();
      scope = packet.scope;
      revision = packet.revision;
      child = true;
      const incomingPreferences = packet.preferences || {};
      preferences = {
        visual: ['motion', 'static', 'off'].includes(incomingPreferences.visual) ? incomingPreferences.visual : 'motion',
        volume: finite(incomingPreferences.volume, 0, 1) ?? 0.5,
        muted: incomingPreferences.muted === true,
      };
      packet.active.forEach((item) => {
        const value = cue(item && item.cue);
        const start = finite(item && item.start_ms, 0, Number.MAX_SAFE_INTEGER);
        if (value && start !== null && typeof item.id === 'string' && now() < start + value.style.travel_ms + value.style.impact_ms) {
          addCue(item.id, value, start, false);
        }
      });
      return true;
    }

    function configure(patch) {
      if (!patch || typeof patch !== 'object') return copy(preferences);
      const next = { ...preferences };
      if (['motion', 'static', 'off'].includes(patch.visual)) next.visual = patch.visual;
      const volume = finite(patch.volume, 0, 1);
      if (volume !== null) next.volume = volume;
      if (typeof patch.muted === 'boolean') next.muted = patch.muted;
      if (next.muted || next.volume < preferences.volume) stopSounds();
      preferences = next;
      if (!child) {
        try { storage && storage.setItem(STORAGE_KEY, JSON.stringify(preferences)); } catch (_) { /* preference remains in memory */ }
      }
      return copy(preferences);
    }

    function project(projectPoint, value) {
      let result;
      try { result = projectPoint(value.slice()); } catch (_) { return null; }
      if (!Array.isArray(result) || result.length < 2) return null;
      const x = Number(result[0]);
      const y = Number(result[1]);
      return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
    }

    function number(value) { return Number(value.toFixed(3)); }
    function circle(location, radius, stroke, width, opacity, extra = '') {
      return `<circle cx="${number(location[0])}" cy="${number(location[1])}" r="${number(radius)}" fill="none" stroke="${stroke}" stroke-width="${number(width)}" opacity="${number(opacity)}"${extra}/>`;
    }

    function marker(kind, location, style, width, opacity) {
      const radius = width * 2;
      if (kind === 'character') return circle(location, radius, style.color, width, opacity, ' data-cue="character"');
      if (kind === 'cover') {
        return `<rect x="${number(location[0] - radius)}" y="${number(location[1] - radius)}" width="${number(radius * 2)}" height="${number(radius * 2)}" fill="none" stroke="${style.color}" stroke-width="${number(width)}" opacity="${number(opacity)}" data-cue="cover"/>`;
      }
      if (kind === 'stop') {
        const points = [[location[0], location[1] - radius], [location[0] + radius, location[1]], [location[0], location[1] + radius], [location[0] - radius, location[1]]];
        return `<polygon points="${points.map((item) => `${number(item[0])},${number(item[1])}`).join(' ')}" fill="none" stroke="${style.color}" stroke-width="${number(width)}" opacity="${number(opacity)}" data-cue="stop"/>`;
      }
      return `<path d="M ${number(location[0] - radius)} ${number(location[1] - radius)} L ${number(location[0] + radius)} ${number(location[1] + radius)} M ${number(location[0] + radius)} ${number(location[1] - radius)} L ${number(location[0] - radius)} ${number(location[1] + radius)}" fill="none" stroke="${style.color}" stroke-width="${number(width)}" opacity="${number(opacity)}" data-cue="penetration"/>`;
    }

    function frame(mapId, projectPoint, at = now()) {
      if (preferences.visual === 'off' || typeof mapId !== 'string' || typeof projectPoint !== 'function' || !Number.isFinite(at)) return '';
      prune(at);
      const markup = [];
      active.filter((item) => item.cue.map_id === mapId && at >= item.start_ms).forEach((item) => {
        const value = item.cue;
        const route = [value.origin, ...value.contacts.map((contact) => contact.point), value.destination];
        const screen = route.map((location) => project(projectPoint, location));
        if (screen.some((location) => !location)) return;
        const lengths = route.slice(1).map((location, index) => Math.hypot(...location.map((coordinate, axis) => coordinate - route[index][axis])));
        const total = lengths.reduce((sum, length) => sum + length, 0);
        if (!Number.isFinite(total) || total <= 0) return;
        const elapsed = at - item.start_ms;
        if (preferences.visual === 'static') {
          const opacity = Math.max(0, 1 - elapsed / (value.style.travel_ms + value.style.impact_ms));
          markup.push(`<polyline points="${screen.map((location) => `${number(location[0])},${number(location[1])}`).join(' ')}" fill="none" stroke="${value.style.color}" stroke-width="${number(value.style.width)}" opacity="${number(opacity)}"/>`);
          if (!value.miss) value.contacts.forEach((contact, index) => markup.push(marker(contact.cue, screen[index + 1], value.style.cues[contact.cue], value.style.width, opacity)));
          return;
        }
        const progress = Math.min(1, elapsed / value.style.travel_ms);
        const head = progress * total;
        const tail = Math.max(0, head - total * 0.18);
        let travelled = 0;
        lengths.forEach((length, index) => {
          const segmentStart = travelled;
          const segmentEnd = travelled + length;
          const from = Math.max(tail, segmentStart);
          const to = Math.min(head, segmentEnd);
          if (to > from && length > 0) {
            const a = (from - segmentStart) / length;
            const b = (to - segmentStart) / length;
            const start = [screen[index][0] + (screen[index + 1][0] - screen[index][0]) * a, screen[index][1] + (screen[index + 1][1] - screen[index][1]) * a];
            const end = [screen[index][0] + (screen[index + 1][0] - screen[index][0]) * b, screen[index][1] + (screen[index + 1][1] - screen[index][1]) * b];
            const opacity = value.miss || value.contacts.length === 0 ? Math.max(0, 1 - progress)
              : Math.max(0, 1 - Math.max(0, elapsed - value.style.travel_ms) / value.style.impact_ms);
            markup.push(`<line x1="${number(start[0])}" y1="${number(start[1])}" x2="${number(end[0])}" y2="${number(end[1])}" stroke="${value.style.color}" stroke-width="${number(value.style.width)}" stroke-linecap="round" opacity="${number(opacity)}"/>`);
          }
          travelled = segmentEnd;
        });
        if (value.miss) return;
        travelled = 0;
        value.contacts.forEach((contact, index) => {
          travelled += lengths[index];
          const arrival = value.style.travel_ms * travelled / total;
          const age = elapsed - arrival;
          if (age < 0 || age > value.style.impact_ms) return;
          const opacity = 1 - age / value.style.impact_ms;
          const style = value.style.cues[contact.cue];
          markup.push(marker(contact.cue, screen[index + 1], style,
            value.style.width * (1 + age / value.style.impact_ms * 0.5), opacity));
        });
      });
      return markup.length ? `<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false" style="pointer-events:none;overflow:visible">${markup.join('')}</svg>` : '';
    }

    async function unlockAudio() {
      if (child) return false;
      if (audio) {
        unlocked = true;
        return true;
      }
      try {
        const AudioContext = root && (root.AudioContext || root.webkitAudioContext);
        if (!AudioContext) return false;
        context ||= new AudioContext();
        if (context.state === 'suspended') await context.resume();
        unlocked = true;
        return true;
      } catch (_) {
        unlocked = false;
        return false;
      }
    }

    return { enter, leave, ingest, snapshot, receive, preferences: () => copy(preferences), configure,
      skip: clearPlayback, frame, unlockAudio };
  }

  return { create, STORAGE_KEY };
});
