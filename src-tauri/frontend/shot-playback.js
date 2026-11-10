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
    // Deterministic per-cue variation (spark angles) so a redrawn frame keeps the same pixels.
    function seeded(text) {
      let state = 0;
      for (const character of String(text)) state = (state * 31 + character.charCodeAt(0)) >>> 0;
      return () => ((state = (state * 1664525 + 1013904223) >>> 0) / 4294967296);
    }
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
        // Motion: a short tapered muzzle flash and gas puff at the origin, a thin tracer streak that fades
        // toward its tail, and compact impacts (ricochet sparks and dust, exit spall, hit ticks) beside the
        // contact markers. Sizes are screen pixels scaled from the cue's own width.
        const width = value.style.width;
        const screenLengths = screen.slice(1).map((location, index) => Math.hypot(location[0] - screen[index][0], location[1] - screen[index][1]));
        const screenTotal = screenLengths.reduce((sum, length) => sum + length, 0) || 1;
        const pointAt = (distance) => {
          let left = Math.max(0, Math.min(total, distance));
          for (let index = 0; index < lengths.length; index += 1) {
            if (left <= lengths[index] || index === lengths.length - 1) {
              const k = lengths[index] > 0 ? Math.min(1, left / lengths[index]) : 0;
              return [screen[index][0] + (screen[index + 1][0] - screen[index][0]) * k, screen[index][1] + (screen[index + 1][1] - screen[index][1]) * k];
            }
            left -= lengths[index];
          }
          return screen[screen.length - 1];
        };
        const firstLength = screenLengths[0] || 1;
        const direction = [(screen[1][0] - screen[0][0]) / firstLength, (screen[1][1] - screen[0][1]) / firstLength];
        const seed = seeded(item.id);
        const flashMs = Math.min(70, value.style.travel_ms * 0.5);
        if (elapsed < flashMs) {
          const fade = 1 - elapsed / flashMs;
          const reach = (3 * width + 5) * (0.6 + 0.4 * fade);
          const half = (0.9 * width + 0.6) * fade;
          const origin = screen[0];
          const tip = [origin[0] + direction[0] * reach, origin[1] + direction[1] * reach];
          markup.push(`<path d="M ${number(origin[0] - direction[1] * half)} ${number(origin[1] + direction[0] * half)} L ${number(tip[0])} ${number(tip[1])} L ${number(origin[0] + direction[1] * half)} ${number(origin[1] - direction[0] * half)} Z" fill="#fff1cf" opacity="${number(fade)}"/>`);
          markup.push(`<circle cx="${number(origin[0])}" cy="${number(origin[1])}" r="${number(0.6 * width + 0.6)}" fill="#fffaf0" opacity="${number(fade)}"/>`);
        }
        const puffMs = value.style.travel_ms + value.style.impact_ms;
        if (elapsed < puffMs) {
          const k = elapsed / puffMs;
          const size = Math.max(1, width * 0.6);
          markup.push(`<circle cx="${number(screen[0][0] + direction[0] * (2 + 7 * k) * size)}" cy="${number(screen[0][1] + direction[1] * (2 + 7 * k) * size - 2 * k)}" r="${number((1.2 + 4 * k) * size)}" fill="#b9c1ca" opacity="${number(0.12 * (1 - k))}"/>`);
        }
        const progress = Math.min(1, elapsed / value.style.travel_ms);
        const head = progress * total;
        // The streak stays about 60 screen pixels long however far the round travels.
        const streak = total * Math.max(0.06, Math.min(0.3, 60 / screenTotal));
        const tail = Math.max(0, head - streak);
        const tracerOpacity = Math.max(0, 1 - Math.max(0, elapsed - value.style.travel_ms) / 90);
        if (head - tail > 0 && tracerOpacity > 0) {
          // Butt-capped pieces with rising opacity read as one streak fading toward its tail (no overlapping caps).
          const pieces = 6;
          for (let piece = 0; piece < pieces; piece += 1) {
            const a = pointAt(tail + (head - tail) * piece / pieces);
            const b = pointAt(tail + (head - tail) * (piece + 1) / pieces);
            const ramp = Math.pow((piece + 1) / pieces, 1.6);
            markup.push(`<line x1="${number(a[0])}" y1="${number(a[1])}" x2="${number(b[0])}" y2="${number(b[1])}" stroke="${value.style.color}" stroke-width="${number(width * 2.4)}" stroke-linecap="butt" opacity="${number(0.14 * ramp * tracerOpacity)}"/>`);
            markup.push(`<line x1="${number(a[0])}" y1="${number(a[1])}" x2="${number(b[0])}" y2="${number(b[1])}" stroke="#fff4dc" stroke-width="${number(Math.max(1, width * 0.65))}" stroke-linecap="butt" opacity="${number(ramp * tracerOpacity)}"/>`);
          }
        }
        if (value.miss) return;
        let travelled = 0;
        value.contacts.forEach((contact, index) => {
          travelled += lengths[index];
          const arrival = value.style.travel_ms * travelled / total;
          const age = elapsed - arrival;
          if (age < 0 || age > value.style.impact_ms) return;
          const opacity = 1 - age / value.style.impact_ms;
          const style = value.style.cues[contact.cue];
          const at = screen[index + 1];
          const quick = Math.min(1, age / 170);
          const scale = Math.max(1, width);
          if (contact.cue === 'cover' || contact.cue === 'stop') {
            if (quick < 1) {
              const back = Math.atan2(-direction[1], -direction[0]);
              for (let spark = 0; spark < 5; spark += 1) {
                const angle = back + (seed() - 0.5) * 2.4;
                const reach = (3 + seed() * 5) * scale;
                const near = reach * quick * 0.35, far = reach * (0.3 + quick);
                markup.push(`<line x1="${number(at[0] + Math.cos(angle) * near)}" y1="${number(at[1] + Math.sin(angle) * near)}" x2="${number(at[0] + Math.cos(angle) * far)}" y2="${number(at[1] + Math.sin(angle) * far)}" stroke="#ffe3b8" stroke-width="1" stroke-linecap="round" opacity="${number(1 - quick)}"/>`);
              }
            }
            markup.push(`<circle cx="${number(at[0] - direction[0] * 3 * (1 - opacity))}" cy="${number(at[1] - direction[1] * 3 * (1 - opacity) - 2 * (1 - opacity))}" r="${number((1.5 + 6 * (1 - opacity)) * scale * 0.6)}" fill="#9ea8b3" opacity="${number(0.3 * opacity)}"/>`);
          } else if (contact.cue === 'penetration' && quick < 1) {
            for (let chip = 0; chip < 4; chip += 1) {
              const angle = Math.atan2(direction[1], direction[0]) + (seed() - 0.5) * 1.1;
              const reach = (3 + seed() * 6) * scale * (0.2 + quick);
              markup.push(`<circle cx="${number(at[0] + Math.cos(angle) * reach)}" cy="${number(at[1] + Math.sin(angle) * reach)}" r="0.8" fill="#d8cbb0" opacity="${number(1 - quick)}"/>`);
            }
          } else if (contact.cue === 'character' && age < 340) {
            const reach = (5 + 2.5 * quick) * scale * 0.7;
            const tick = 3 * scale * 0.7;
            const fade = Math.max(0, 1 - Math.max(0, age - 120) / 220);
            [[1, 1], [-1, 1], [1, -1], [-1, -1]].forEach(([sx, sy]) => {
              const ux = sx * 0.707, uy = sy * 0.707;
              markup.push(`<line x1="${number(at[0] + ux * reach)}" y1="${number(at[1] + uy * reach)}" x2="${number(at[0] + ux * (reach + tick))}" y2="${number(at[1] + uy * (reach + tick))}" stroke="#fff4ef" stroke-width="1.5" stroke-linecap="round" opacity="${number(fade)}"/>`);
            });
          }
          markup.push(marker(contact.cue, at, style, Math.max(1, width * 0.7) * (1 + age / value.style.impact_ms * 0.4), opacity));
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
