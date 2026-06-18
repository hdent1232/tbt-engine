#!/usr/bin/env python3
"""
pdf_metadata_ui — a local browser UI for viewing and editing every bit of a
PDF's metadata, transferring metadata between PDFs, matching byte sizes, and
verifying the result.

Run it and your browser opens the dashboard:

    python pdf_metadata_ui.py
    # then open http://127.0.0.1:8765 if it doesn't open automatically

What it shows / lets you do:
  * Every /Info field (standard and custom) — add, edit or remove any key.
  * The full XMP /Metadata packet — edit the raw RDF/XML.
  * Both /ID byte strings (as hex) — edit directly.
  * The file's byte size and a "pad to N bytes" control, so you can make the
    file size match a reference exactly while the metadata stays identical.
  * A selectable-text indicator confirming the output keeps real text and is
    not a flattened "print to PDF".
  * One-click transfer of all metadata from a source PDF onto a target.
  * Verification that two PDFs are identical (metadata, and optionally size).

It binds only to localhost and never re-renders pages, so saved PDFs keep their
original selectable text.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pdf_metadata_core as core

HOST, PORT = "127.0.0.1", 8765


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF Metadata Studio</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font: 14px/1.45 system-ui, sans-serif; margin: 0; padding: 1.2rem;
         background: #11141a; color: #e7e9ee; }
  h1 { font-size: 1.3rem; margin: 0 0 .2rem; }
  .sub { color: #97a0b0; margin: 0 0 1rem; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .card { background: #1a1f29; border: 1px solid #2a3140; border-radius: 10px;
          padding: 1rem; }
  .card h2 { font-size: 1.05rem; margin: 0 0 .6rem; }
  label { display: block; font-size: .8rem; color: #97a0b0; margin: .5rem 0 .2rem; }
  input[type=text], textarea {
    width: 100%; background: #0e1117; color: #e7e9ee; border: 1px solid #2a3140;
    border-radius: 6px; padding: .45rem .55rem; font: inherit; }
  textarea { min-height: 150px; font-family: ui-monospace, monospace; font-size: 12px; }
  button { background: #3a6df0; color: #fff; border: 0; border-radius: 6px;
           padding: .5rem .8rem; font: inherit; cursor: pointer; margin: .2rem .3rem .2rem 0; }
  button.ghost { background: #2a3140; }
  button.tiny { padding: .2rem .5rem; font-size: .8rem; }
  .row { display: flex; gap: .4rem; align-items: center; }
  .kv { display: grid; grid-template-columns: 200px 1fr auto; gap: .4rem;
        margin-bottom: .35rem; align-items: center; }
  .pill { display: inline-block; padding: .1rem .5rem; border-radius: 999px;
          font-size: .75rem; font-weight: 600; }
  .ok { background: #15391f; color: #57e08a; }
  .bad { background: #3a1620; color: #ff7a93; }
  .muted { color: #97a0b0; }
  .stat { font-size: 1.6rem; font-weight: 700; }
  .hint { font-size: .78rem; color: #97a0b0; margin-top: .3rem; }
  #log { white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: 12px;
         background: #0e1117; border: 1px solid #2a3140; border-radius: 6px;
         padding: .6rem; min-height: 2rem; margin-top: .6rem; }
  hr { border: 0; border-top: 1px solid #2a3140; margin: 1rem 0; }
  .mono { font-family: ui-monospace, monospace; }
</style>
</head>
<body>
  <h1>PDF Metadata Studio</h1>
  <p class="sub">View and edit every bit of a PDF's metadata, transfer it
     between files, match byte sizes, and verify. Output keeps selectable text —
     it is never a flattened print-to-PDF.</p>

  <div class="cols">
    <!-- SOURCE -->
    <div class="card">
      <h2>Source (reference)</h2>
      <label>Source PDF path</label>
      <div class="row">
        <input type="text" id="srcPath" placeholder="/path/to/reference.pdf">
        <button class="ghost" onclick="loadPdf('src')">Load</button>
      </div>
      <div id="srcSummary" class="hint">Not loaded.</div>
      <hr>
      <label>Output path (where the target is written)</label>
      <input type="text" id="outPath" placeholder="/path/to/output.pdf (defaults to target)">
      <div class="row" style="margin-top:.6rem">
        <input type="checkbox" id="matchSize" style="width:auto">
        <label style="margin:0">Pad output to match source's byte size</label>
      </div>
      <button onclick="transferAll()">⇒ Transfer ALL metadata from source onto target</button>
    </div>

    <!-- TARGET -->
    <div class="card">
      <h2>Target (editable)</h2>
      <label>Target PDF path</label>
      <div class="row">
        <input type="text" id="tgtPath" placeholder="/path/to/target.pdf">
        <button class="ghost" onclick="loadPdf('tgt')">Load</button>
      </div>
      <div class="row" style="margin-top:.6rem; gap:1.2rem">
        <div><div class="muted">File size</div><div class="stat"><span id="tgtSize">—</span> <span class="muted" style="font-size:.9rem">bytes</span></div></div>
        <div><div class="muted">Selectable text</div><div id="tgtText" class="stat">—</div></div>
        <div><div class="muted">XMP size</div><div class="stat"><span id="tgtXmpSize">—</span></div></div>
      </div>
    </div>
  </div>

  <div class="card" style="margin-top:1rem" id="editor" hidden>
    <h2>/Info dictionary</h2>
    <div id="infoRows"></div>
    <button class="ghost tiny" onclick="addInfoRow()">+ Add field</button>

    <hr>
    <h2>Trailer /ID (hex)</h2>
    <label>ID[0] — permanent identifier</label>
    <input type="text" id="id0" class="mono" placeholder="(none)">
    <label>ID[1] — changed-since identifier</label>
    <input type="text" id="id1" class="mono" placeholder="(none)">

    <hr>
    <h2>XMP /Metadata (raw RDF/XML)</h2>
    <textarea id="xmp" spellcheck="false"></textarea>

    <hr>
    <h2>Byte size</h2>
    <label>Pad output to exactly N bytes (leave blank to skip; cannot shrink)</label>
    <div class="row">
      <input type="text" id="padTo" class="mono" style="max-width:220px" placeholder="e.g. 482113">
      <button class="ghost tiny" onclick="copySrcSize()">Use source size</button>
    </div>
    <div class="hint">Padding appends an ignored PDF comment after %%EOF — it
       does not touch page content, so text stays selectable.</div>

    <hr>
    <button onclick="saveEdits()">💾 Save edits to output</button>
    <button class="ghost" onclick="runVerify()">✔ Verify source vs output</button>
  </div>

  <div id="log" class="muted">Ready.</div>

<script>
let SRC = null, TGT = null;

function log(msg, cls) {
  const el = document.getElementById('log');
  el.className = cls || '';
  el.textContent = msg;
}
async function api(path, body) {
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(body)});
  const data = await r.json();
  if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}

async function loadPdf(role) {
  const path = document.getElementById(role === 'src' ? 'srcPath' : 'tgtPath').value.trim();
  if (!path) { log('Enter a path first.', 'bad'); return; }
  try {
    const md = await api('/api/load', {path});
    if (role === 'src') { SRC = md; renderSrc(); }
    else { TGT = md; renderTgt(); }
    log('Loaded ' + path);
  } catch (e) { log('Load failed: ' + e.message, 'bad'); }
}

function renderSrc() {
  document.getElementById('srcSummary').innerHTML =
    `<b>${SRC.path}</b><br>${SRC.size} bytes · ` +
    `${Object.keys(SRC.info).length} /Info fields · ` +
    `XMP ${SRC.xmp_bytes ?? 0} bytes · ` +
    `/ID ${SRC.id ? 'present' : 'none'} · ` +
    `text ${SRC.has_text ? 'selectable' : 'not detected'}`;
}

function renderTgt() {
  document.getElementById('tgtSize').textContent = TGT.size;
  document.getElementById('tgtXmpSize').textContent = (TGT.xmp_bytes ?? 0) + ' B';
  const t = document.getElementById('tgtText');
  t.innerHTML = TGT.has_text ? '<span class="pill ok">yes</span>'
                             : '<span class="pill bad">no</span>';
  // /Info rows
  const rows = document.getElementById('infoRows'); rows.innerHTML = '';
  for (const [k, v] of Object.entries(TGT.info)) addInfoRow(k, v);
  // /ID
  document.getElementById('id0').value = TGT.id ? TGT.id[0] : '';
  document.getElementById('id1').value = TGT.id ? (TGT.id[1] ?? '') : '';
  // XMP
  document.getElementById('xmp').value = TGT.xmp ?? '';
  document.getElementById('editor').hidden = false;
}

function addInfoRow(key = '', val = '') {
  const wrap = document.getElementById('infoRows');
  const div = document.createElement('div');
  div.className = 'kv';
  div.innerHTML =
    `<input type="text" class="ik" value="${escapeHtml(key)}" placeholder="/Key">` +
    `<input type="text" class="iv" value="${escapeHtml(val)}" placeholder="value">` +
    `<button class="ghost tiny" onclick="this.parentElement.remove()">✕</button>`;
  wrap.appendChild(div);
}
function escapeHtml(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function collectInfo() {
  const info = {};
  for (const row of document.querySelectorAll('#infoRows .kv')) {
    const k = row.querySelector('.ik').value.trim();
    const v = row.querySelector('.iv').value;
    if (k) info[k] = v;
  }
  return info;
}
function collectId() {
  const a = document.getElementById('id0').value.trim();
  const b = document.getElementById('id1').value.trim();
  if (!a && !b) return null;          // leave untouched / none
  return [a, b].filter(x => x.length); // hex strings
}
function padValue() {
  const p = document.getElementById('padTo').value.trim();
  return p ? parseInt(p, 10) : null;
}

function copySrcSize() {
  if (!SRC) { log('Load a source first.', 'bad'); return; }
  document.getElementById('padTo').value = SRC.size;
}

async function transferAll() {
  const source = document.getElementById('srcPath').value.trim();
  const target = document.getElementById('tgtPath').value.trim();
  const output = document.getElementById('outPath').value.trim() || target;
  if (!source || !target) { log('Need both source and target paths.', 'bad'); return; }
  try {
    const res = await api('/api/transfer',
      {source, target, output, match_size: document.getElementById('matchSize').checked});
    TGT = res.result; document.getElementById('tgtPath').value = output;
    renderTgt();
    showReport('Transferred metadata onto ' + output, res.report);
  } catch (e) { log('Transfer failed: ' + e.message, 'bad'); }
}

async function saveEdits() {
  const target = document.getElementById('tgtPath').value.trim();
  const output = document.getElementById('outPath').value.trim() || target;
  if (!target) { log('Load a target first.', 'bad'); return; }
  try {
    const res = await api('/api/save', {
      target, output,
      info: collectInfo(),
      xmp: document.getElementById('xmp').value,
      id: collectId(),
      pad_to: padValue(),
    });
    TGT = res.result; document.getElementById('tgtPath').value = output;
    renderTgt();
    log('Saved to ' + output + '\n' + res.result.size + ' bytes · text ' +
        (res.result.has_text ? 'selectable' : 'NOT detected'),
        res.result.has_text ? '' : 'bad');
  } catch (e) { log('Save failed: ' + e.message, 'bad'); }
}

async function runVerify() {
  const a = document.getElementById('srcPath').value.trim();
  const b = document.getElementById('outPath').value.trim() ||
            document.getElementById('tgtPath').value.trim();
  if (!a || !b) { log('Need source and output paths to verify.', 'bad'); return; }
  try {
    const res = await api('/api/verify', {a, b});
    showReport('Verify ' + a + '  vs  ' + b, res);
  } catch (e) { log('Verify failed: ' + e.message, 'bad'); }
}

function showReport(title, report) {
  let lines = [title, ''];
  for (const [k, ok] of Object.entries(report.checks))
    lines.push((ok ? '  [MATCH] ' : '  [DIFF ] ') + k);
  lines.push('');
  lines.push('  A size: ' + report.a.size + ' bytes   B size: ' + report.b.size + ' bytes');
  lines.push('');
  lines.push(report.metadata_identical ? 'Metadata: IDENTICAL' : 'Metadata: DIFFERS');
  if (report.metadata_identical && !report.byte_identical_size)
    lines.push('(metadata matches; byte sizes differ — use the pad control to equalize)');
  log(lines.join('\n'), report.metadata_identical ? '' : 'bad');
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def log_message(self, *args) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid JSON"})

        try:
            if self.path == "/api/load":
                self._json(200, core.get_metadata(_require(req, "path")))

            elif self.path == "/api/transfer":
                output = req.get("output") or req["target"]
                result = core.transfer_metadata(
                    _require(req, "source"), _require(req, "target"), output,
                    match_size=bool(req.get("match_size")))
                report = core.compare(req["source"], output)
                self._json(200, {"result": result, "report": report})

            elif self.path == "/api/save":
                output = req.get("output") or req["target"]
                doc_id = req.get("id")
                doc_id_bytes = (None if doc_id is None
                                else [bytes.fromhex(h) for h in doc_id])
                result = core.apply_metadata(
                    _require(req, "target"), output,
                    info=req.get("info"),
                    xmp=req.get("xmp"),
                    doc_id=doc_id_bytes,
                    pad_to=req.get("pad_to"))
                self._json(200, {"result": result})

            elif self.path == "/api/verify":
                self._json(200, core.compare(_require(req, "a"), _require(req, "b")))

            else:
                self._json(404, {"error": "unknown endpoint"})

        except FileNotFoundError as e:
            self._json(400, {"error": f"file not found: {e}"})
        except (ValueError, KeyError) as e:
            self._json(400, {"error": str(e)})
        except Exception as e:  # surface anything else to the UI
            self._json(500, {"error": f"{type(e).__name__}: {e}"})


def _require(req: dict, key: str) -> str:
    value = req.get(key)
    if not value:
        raise KeyError(f"missing required field: {key}")
    if not Path(value).is_file() and key not in ("output",):
        raise FileNotFoundError(value)
    return value


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"PDF Metadata Studio running at {url}  (Ctrl-C to stop)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
