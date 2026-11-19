// Shared helpers for the functional suites: env loading, a cookie-aware API client, xlsx/zip builder.
// Never prints or persists passwords, cookies or tokens.
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, '..', '..');

export function loadEnv(file = path.join(ROOT, 'backend', '.env.qa')) {
  const env = {};
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    if (!line.includes('=') || line.trimStart().startsWith('#')) continue;
    const i = line.indexOf('=');
    env[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^["']|["']$/g, '');
  }
  return env;
}

export const BASE = (process.env.QA_BASE || 'http://127.0.0.1:5181').replace(/\/$/, '');
export const ORIGIN = 'http://127.0.0.1:5181';

/** Minimal cookie-jar client. Every state-changing call sends Origin + X-CSRF-Token unless told otherwise. */
export class Client {
  constructor(name = 'anon') { this.name = name; this.cookies = {}; }
  get csrf() { return this.cookies.aastar_csrf; }
  get session() { return this.cookies.aastar_session; }
  clone(name) { const c = new Client(name); c.cookies = { ...this.cookies }; return c; }
  _store(res) {
    const list = typeof res.headers.getSetCookie === 'function' ? res.headers.getSetCookie() : [];
    for (const sc of list) {
      const [pair, ...attrs] = sc.split(';');
      const i = pair.indexOf('=');
      const k = pair.slice(0, i).trim();
      const v = pair.slice(i + 1).trim();
      const expired = attrs.some((a) => /max-age=0\b/i.test(a.trim()) || /expires=thu, 01 jan 1970/i.test(a.trim()));
      if (!v || v === '""' || expired) delete this.cookies[k]; else this.cookies[k] = v;
    }
  }
  async req(method, url, { json, form, headers = {}, origin = ORIGIN, csrf = true, raw, cookies } = {}) {
    const h = { ...headers };
    const jar = cookies ?? this.cookies;
    const ck = Object.entries(jar).map(([k, v]) => `${k}=${v}`).join('; ');
    if (ck) h.cookie = ck;
    const unsafe = !['GET', 'HEAD', 'OPTIONS'].includes(method);
    if (unsafe && origin) h.origin = origin;
    if (unsafe && csrf === true && jar.aastar_csrf) h['x-csrf-token'] = jar.aastar_csrf;
    else if (typeof csrf === 'string') h['x-csrf-token'] = csrf;
    let body;
    if (json !== undefined) { body = JSON.stringify(json); h['content-type'] = 'application/json'; }
    else if (form) body = form;
    else if (raw !== undefined) body = raw;
    const t0 = Date.now();
    let res;
    try {
      res = await fetch(BASE + url, { method, headers: h, body, redirect: 'manual' });
    } catch (e) {
      return { status: 0, ok: false, text: String(e), data: null, headers: new Headers(), ms: Date.now() - t0 };
    }
    this._store(res);
    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch { /* not JSON */ }
    return { status: res.status, ok: res.ok, text, data, headers: res.headers, ms: Date.now() - t0 };
  }
  get(u, o) { return this.req('GET', u, o); }
  post(u, json, o = {}) { return this.req('POST', u, { json, ...o }); }
  put(u, json, o = {}) { return this.req('PUT', u, { json, ...o }); }
  patch(u, json, o = {}) { return this.req('PATCH', u, { json, ...o }); }
  del(u, o) { return this.req('DELETE', u, o); }
  async login(email, password, o = {}) {
    return this.req('POST', '/api/auth/login', { json: { email, password }, csrf: false, ...o });
  }
}

/** Short, secret-free description of a response for the evidence column. */
export function ev(r, extra = '') {
  if (!r) return extra;
  const isErr = r.data && typeof r.data.error === 'object' && r.data.error !== null;
  const code = isErr && r.data.error.code ? ` code=${r.data.error.code}` : '';
  let body = '';
  if (r.data && !isErr) {
    const d = { ...r.data };
    delete d.temporary_password;
    body = ' ' + JSON.stringify(d).slice(0, 160);
  } else if (r.data?.error?.message) body = ` msg="${String(r.data.error.message).slice(0, 140)}"`;
  else if (!r.data) body = ' ' + (r.text || '').slice(0, 100).replace(/\s+/g, ' ');
  return `HTTP ${r.status}${code}${body}${extra ? ' | ' + extra : ''}`;
}

// ── tiny zip writer (stored, no compression) for .xlsx fixtures ───────────────────────────────────────
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
export function zip(files) {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    const nameBuf = Buffer.from(name, 'utf8');
    const raw = Buffer.isBuffer(content) ? content : Buffer.from(content, 'utf8');
    const data = zlib.deflateRawSync(raw);
    const crc = crc32(raw);
    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt16LE(0, 6); lh.writeUInt16LE(8, 8);
    lh.writeUInt16LE(0, 10); lh.writeUInt16LE(0x21, 12); lh.writeUInt32LE(crc, 14);
    lh.writeUInt32LE(data.length, 18); lh.writeUInt32LE(raw.length, 22); lh.writeUInt16LE(nameBuf.length, 26);
    lh.writeUInt16LE(0, 28);
    locals.push(lh, nameBuf, data);
    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0); ch.writeUInt16LE(20, 4); ch.writeUInt16LE(20, 6); ch.writeUInt16LE(0, 8);
    ch.writeUInt16LE(8, 10); ch.writeUInt16LE(0, 12); ch.writeUInt16LE(0x21, 14); ch.writeUInt32LE(crc, 16);
    ch.writeUInt32LE(data.length, 20); ch.writeUInt32LE(raw.length, 24); ch.writeUInt16LE(nameBuf.length, 28);
    ch.writeUInt32LE(offset, 42);
    centrals.push(ch, nameBuf);
    offset += 30 + nameBuf.length + data.length;
  }
  const cd = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  const count = Object.keys(files).length;
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(count, 8); end.writeUInt16LE(count, 10);
  end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, end]);
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
function colName(i) { let s = ''; i += 1; while (i > 0) { const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); } return s; }

/** Build a minimal .xlsx: sheets = { 'Tab title': [[cell, ...], ...] }. Numbers stay numeric, rest inline strings. */
export function xlsx(sheets) {
  const names = Object.keys(sheets);
  const files = {
    '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
      '<Default Extension="xml" ContentType="application/xml"/>' +
      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
      names.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('') +
      '</Types>',
    '_rels/.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
      '</Relationships>',
    'xl/workbook.xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' +
      names.map((n, i) => `<sheet name="${esc(n)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join('') +
      '</sheets></workbook>',
    'xl/_rels/workbook.xml.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      names.map((_, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join('') +
      '</Relationships>',
  };
  names.forEach((n, i) => {
    const rows = sheets[n].map((row, r) => `<row r="${r + 1}">` + row.map((v, c) => {
      const ref = `${colName(c)}${r + 1}`;
      if (v === null || v === undefined || v === '') return '';
      if (typeof v === 'number') return `<c r="${ref}"><v>${v}</v></c>`;
      return `<c r="${ref}" t="inlineStr"><is><t>${esc(v)}</t></is></c>`;
    }).join('') + '</row>').join('');
    files[`xl/worksheets/sheet${i + 1}.xml`] = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${rows}</sheetData></worksheet>`;
  });
  return zip(files);
}

export function fileForm(fields, file) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields || {})) fd.append(k, String(v));
  if (file) fd.append(file.field || 'file', new Blob([file.data], { type: file.type || 'application/octet-stream' }), file.name);
  return fd;
}

export function strongPassword() {
  const pool = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let s = '';
  for (let i = 0; i < 14; i++) s += pool[Math.floor(Math.random() * pool.length)];
  return `Qf${s}!7x`;
}
