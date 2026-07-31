#!/usr/bin/env node
/**
 * Generate the extension's toolbar icons (16/48/128) as real PNGs, with no
 * image dependency — Node's zlib plus hand-rolled PNG chunks is all it takes.
 *
 * The mark: a rounded square in the Stept accent (#6366f1) with a white "S"
 * drawn as an analytic two-arc stroke (not a blocky bitmap, so 16px still
 * reads). Re-run with `node scripts/gen-icons.mjs` after changing the accent.
 */
import { deflateSync } from 'node:zlib';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'public', 'icon');
const SIZES = [16, 48, 128];
const ACCENT = [99, 102, 241]; // #6366f1
const SS = 3; // supersampling factor (antialiasing)

/** Sampled centreline of the letter "S" in normalized coords (y up, ±0.8). */
function letterPath() {
  const pts = [];
  const arc = (cx, cy, r, fromDeg, toDeg, steps) => {
    for (let i = 0; i <= steps; i++) {
      const a = ((fromDeg + ((toDeg - fromDeg) * i) / steps) * Math.PI) / 180;
      pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
    }
  };
  arc(0, 0.4, 0.4, 20, 270, 60); // top bowl: right → over the top → down to centre
  arc(0, -0.4, 0.4, 90, -160, 60); // bottom bowl: centre → right → under → left
  return pts;
}

const PATH = letterPath();

function distToPath(x, y) {
  let best = Infinity;
  for (let i = 1; i < PATH.length; i++) {
    const [ax, ay] = PATH[i - 1];
    const [bx, by] = PATH[i];
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy || 1;
    let t = ((x - ax) * dx + (y - ay) * dy) / len2;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const px = ax + t * dx - x;
    const py = ay + t * dy - y;
    const d = px * px + py * py;
    if (d < best) best = d;
  }
  return Math.sqrt(best);
}

/** RGBA raster for one icon size. */
function render(size) {
  const hi = size * SS;
  const radius = size * 0.22 * SS;
  const scale = size * 0.32 * SS; // 1.6 normalized units ≈ 56% of the icon
  const stroke = 0.115 * scale; // half-width
  const cx = hi / 2;
  const cy = hi / 2;

  // accumulate coverage per output pixel
  const bg = new Float32Array(size * size);
  const fg = new Float32Array(size * size);
  for (let hy = 0; hy < hi; hy++) {
    for (let hx = 0; hx < hi; hx++) {
      const px = hx + 0.5;
      const py = hy + 0.5;
      // rounded-rect coverage (signed distance to a rounded box)
      const qx = Math.abs(px - cx) - (hi / 2 - radius);
      const qy = Math.abs(py - cy) - (hi / 2 - radius);
      const outside = Math.hypot(Math.max(qx, 0), Math.max(qy, 0));
      const inside = outside + Math.min(Math.max(qx, qy), 0) - radius <= 0;
      const idx = Math.floor(hy / SS) * size + Math.floor(hx / SS);
      if (inside) {
        bg[idx] += 1;
        // letter coverage (y flipped: path is y-up, raster is y-down)
        if (distToPath((px - cx) / scale, (cy - py) / scale) * scale <= stroke) fg[idx] += 1;
      }
    }
  }

  const per = SS * SS;
  const rgba = Buffer.alloc(size * size * 4);
  for (let i = 0; i < size * size; i++) {
    const a = bg[i] / per;
    const l = Math.min(1, fg[i] / per);
    const o = i * 4;
    rgba[o] = Math.round(ACCENT[0] * (1 - l) + 255 * l);
    rgba[o + 1] = Math.round(ACCENT[1] * (1 - l) + 255 * l);
    rgba[o + 2] = Math.round(ACCENT[2] * (1 - l) + 255 * l);
    rgba[o + 3] = Math.round(a * 255);
  }
  return rgba;
}

// ---- minimal PNG encoder --------------------------------------------------

const CRC_TABLE = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();

function crc32(buf) {
  let c = -1;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

function encodePng(size, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // colour type RGBA
  const raw = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0; // filter: none
    rgba.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

mkdirSync(OUT_DIR, { recursive: true });
for (const size of SIZES) {
  const file = join(OUT_DIR, `${size}.png`);
  writeFileSync(file, encodePng(size, render(size)));
  console.log(`wrote ${file}`);
}
