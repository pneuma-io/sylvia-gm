/* Original vector tile artwork. Face identities come only from admitted presentation data. */
(function (root) {
  'use strict';
  const numerals = ['一', '二', '三', '四', '五', '六', '七', '八', '九'];
  const honors = ['東', '南', '西', '北', '', '發', '中'];
  const names = ['East', 'South', 'West', 'North', 'White dragon', 'Green dragon', 'Red dragon'];
  function valid(face) {
    return face?.family === 'mahjong' && Number.isInteger(face.kind) && face.kind >= 0 && face.kind < 34;
  }
  function describe(face) {
    if (!valid(face)) return 'Tile';
    if (face.kind >= 27) return names[face.kind - 27];
    return `${face.red ? 'Red ' : ''}${face.kind % 9 + 1} of ${['characters', 'circles', 'bamboo'][Math.floor(face.kind / 9)]}`;
  }
  const spots = {
    1: [[20,28]], 2: [[20,15],[20,41]], 3: [[11,14],[20,28],[29,42]],
    4: [[11,15],[29,15],[11,41],[29,41]],
    5: [[10,14],[30,14],[20,28],[10,42],[30,42]],
    6: [[11,12],[29,12],[11,28],[29,28],[11,44],[29,44]],
    7: [[10,10],[20,16],[30,22],[11,34],[29,34],[11,46],[29,46]],
    8: [[11,9],[29,9],[11,22],[29,22],[11,35],[29,35],[11,48],[29,48]],
    9: [[9,11],[20,11],[31,11],[9,28],[20,28],[31,28],[9,45],[20,45],[31,45]],
  };
  function artwork(face) {
    const rank = face.kind % 9 + 1, suit = Math.floor(face.kind / 9);
    const red = '#b63835', green = '#236b4d', ink = '#173f49';
    if (suit === 0) return `<text x="20" y="25" font-size="23" fill="${face.red ? red : ink}">${numerals[rank - 1]}</text>` +
      `<text x="20" y="49" font-size="23" fill="${red}">萬</text>`;
    if (suit === 1) return spots[rank].map(([x,y], i) => {
      const color = face.red ? red : rank === 1 ? ink : [ink,green,red][i % 3];
      return `<circle cx="${x}" cy="${y}" r="${rank === 1 ? 10 : 4}" fill="none" stroke="${color}" stroke-width="2.2"/>` +
        `<circle cx="${x}" cy="${y}" r="${rank === 1 ? 5 : 1}" fill="${color}"/>`;
    }).join('');
    if (suit === 2) {
      if (rank === 1) return `<path d="M11 43Q8 27 17 18L20 9L24 15L32 19L24 23Q31 35 24 45L20 38L17 48Z" fill="${green}"/>` +
        `<path d="M15 26Q25 25 23 36M14 40L21 30M20 17L27 19" fill="none" stroke="#e9e4c8" stroke-width="2"/>` +
        `<circle cx="23" cy="17" r="1.2" fill="${ink}"/><path d="M30 19L35 22L29 22" fill="${red}"/>`;
      return spots[rank].map(([x,y]) => `<path d="M${x-2.2} ${y-4.6}V${y+4.6}M${x+2.2} ${y-4.6}V${y+4.6}M${x-3.5} ${y-4}H${x+3.5}M${x-3.5} ${y+4}H${x+3.5}" fill="none" stroke="${face.red ? red : green}" stroke-width="1.8"/>`).join('');
    }
    const honor = face.kind - 27;
    if (honor === 4) return `<rect x="8" y="10" width="24" height="36" rx="2" fill="none" stroke="#326479" stroke-width="2.5"/><rect x="11" y="13" width="18" height="30" rx="1" fill="none" stroke="#326479" stroke-width=".7"/>`;
    return `<text x="20" y="40" font-size="33" fill="${honor === 5 ? green : honor === 6 ? red : ink}">${honors[honor]}</text>`;
  }
  function render(face, back = false) {
    if (back) return '<span class="table-tile table-tile-back" aria-hidden="true"><i></i></span>';
    if (!valid(face)) return '';
    return `<span class="table-tile${face.red ? ' table-tile-red' : ''}" aria-hidden="true"><svg viewBox="0 0 40 56" focusable="false" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><g text-anchor="middle" font-family="Yu Mincho, Noto Serif CJK JP, serif" font-weight="600">${artwork(face)}</g></svg></span>`;
  }
  root.SylviaTileFaces = {render, describe};
})(typeof window === 'undefined' ? globalThis : window);
