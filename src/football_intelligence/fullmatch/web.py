# ruff: noqa: E501  -- embedded JavaScript lines follow JS style, not 100-char Python
"""FastAPI-served browser page for resumable full-match uploads and results.

The page is deliberately small and self-contained: one HTML document with inline
CSS and JavaScript, served from the same origin as the API so no CORS is needed
for API calls. Parts are transferred directly from the browser to the configured
object store (MinIO) via presigned URLs, so the backend never proxies part
bodies. The full-file SHA-256 required by the upload contract is computed
incrementally in JavaScript (mirrored by the Python ``expected_part_size_bytes``
helper, which is contract-tested against the upload service).
"""

from __future__ import annotations

import json

from football_intelligence.uploads import PART_SIZE_BYTES

PAGE_PATH = "/full-match"

# Canonical API paths used by the page's JavaScript. Tests assert every value
# exists as a route on the application and that the page embeds exactly this map.
ENDPOINTS: dict[str, str] = {
    "createUpload": "/uploads",
    "presignPart": "/uploads/{upload_id}/parts/{part_number}/presign",
    "completeUpload": "/uploads/{upload_id}/complete",
    "abortUpload": "/uploads/{upload_id}",
    "runFullMatch": "/jobs/{job_id}/full-match/run",
    "fullMatchStatus": "/jobs/{job_id}/full-match/status",
    "jobStatus": "/jobs/{job_id}/status",
    "events": "/jobs/{job_id}/events",
    "eventClip": "/jobs/{job_id}/events/{event_id}/clip",
    "eventThumbnail": "/jobs/{job_id}/events/{event_id}/thumbnail",
    "scoreboard": "/jobs/{job_id}/scoreboard",
    "heatMap": "/jobs/{job_id}/heat-map",
    "annotatedVideo": "/jobs/{job_id}/annotated-video",
    "stopJob": "/jobs/{job_id}/stop",
}


def expected_part_size_bytes(size_bytes: int, part_number: int, part_count: int) -> int:
    """Python mirror of the server's part sizing, used to plan browser slices.

    Matches ``football_intelligence.uploads._expected_part_size``: every part is
    the fixed 16 MiB size except the final one, which holds the remainder.
    """
    if part_number < part_count:
        return PART_SIZE_BYTES
    return size_bytes - PART_SIZE_BYTES * (part_count - 1)


def _js_lib() -> str:
    """Pure-JS helpers embedded in the page and exercised by node tests."""
    return r"""/* ==== fm-js-lib-begin ==== */
'use strict';
var FM_PART_SIZE_BYTES = 16777216;

function expectedPartSizeBytes(sizeBytes, partNumber, partCount) {
  if (partNumber < partCount) return FM_PART_SIZE_BYTES;
  return sizeBytes - FM_PART_SIZE_BYTES * (partCount - 1);
}

var SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
]);

function rotr(x, n) { return ((x >>> n) | (x << (32 - n))) >>> 0; }

function bytesToHex(bytes) {
  var out = '';
  for (var i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, '0');
  }
  return out;
}

function bytesToBase64(bytes) {
  var binary = '';
  for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function Sha256() {
  this._h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  this._block = new Uint8Array(64);
  this._blockLen = 0;
  this._totalBytes = 0;
}

Sha256.prototype.update = function (data) {
  this._totalBytes += data.length;
  var offset = 0;
  while (offset < data.length) {
    var take = Math.min(64 - this._blockLen, data.length - offset);
    this._block.set(data.subarray(offset, offset + take), this._blockLen);
    this._blockLen += take;
    offset += take;
    if (this._blockLen === 64) {
      this._compress(this._block);
      this._blockLen = 0;
    }
  }
  return this;
};

Sha256.prototype.digest = function () {
  var totalBits = this._totalBytes * 8;
  var high = Math.floor(totalBits / 0x100000000) >>> 0;
  var low = totalBits >>> 0;
  var context = new Sha256();
  context._h = this._h.slice();
  var first = new Uint8Array(64);
  first.set(this._block.subarray(0, this._blockLen));
  first[this._blockLen] = 0x80;
  var view = new DataView(first.buffer);
  if (this._blockLen < 56) {
    view.setUint32(56, high);
    view.setUint32(60, low);
    context._compress(first);
  } else {
    context._compress(first);
    var second = new Uint8Array(64);
    var secondView = new DataView(second.buffer);
    secondView.setUint32(56, high);
    secondView.setUint32(60, low);
    context._compress(second);
  }
  var out = new Uint8Array(32);
  var outView = new DataView(out.buffer);
  for (var i = 0; i < 8; i++) outView.setUint32(i * 4, context._h[i]);
  return out;
};

Sha256.prototype.digestHex = function () {
  return bytesToHex(this.digest());
};

Sha256.prototype._compress = function (block) {
  var w = new Uint32Array(64);
  var view = new DataView(block.buffer, block.byteOffset, block.byteLength);
  for (var i = 0; i < 16; i++) w[i] = view.getUint32(i * 4);
  for (var i = 16; i < 64; i++) {
    var s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
    var s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
    w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
  }
  var a = this._h[0], b = this._h[1], c = this._h[2], d = this._h[3];
  var e = this._h[4], f = this._h[5], g = this._h[6], h = this._h[7];
  for (var i = 0; i < 64; i++) {
    var S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
    var ch = (e & f) ^ (~e & g);
    var t1 = (h + S1 + ch + SHA256_K[i] + w[i]) >>> 0;
    var S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
    var maj = (a & b) ^ (a & c) ^ (b & c);
    var t2 = (S0 + maj) >>> 0;
    h = g; g = f; f = e; e = (d + t1) >>> 0;
    d = c; c = b; b = a; a = (t1 + t2) >>> 0;
  }
  this._h[0] = (this._h[0] + a) >>> 0;
  this._h[1] = (this._h[1] + b) >>> 0;
  this._h[2] = (this._h[2] + c) >>> 0;
  this._h[3] = (this._h[3] + d) >>> 0;
  this._h[4] = (this._h[4] + e) >>> 0;
  this._h[5] = (this._h[5] + f) >>> 0;
  this._h[6] = (this._h[6] + g) >>> 0;
  this._h[7] = (this._h[7] + h) >>> 0;
};

function sha256Hex(bytes) {
  return new Sha256().update(bytes).digestHex();
}
/* ==== fm-js-lib-end ==== */
"""


def _app_js() -> str:
    """Browser application logic: upload, presign/PUT parts, run, and results."""
    return r"""/* ==== fm-app-begin ==== */
'use strict';
(function () {
  var EP = JSON.parse(document.getElementById('fm-endpoints').textContent);
  var API = window.location.origin;
  var state = { jobId: null, uploadId: null, timer: null, running: false };

  function byId(id) { return document.getElementById(id); }

  function apiPath(template, values) {
    var path = template;
    for (var key in values) {
      if (Object.prototype.hasOwnProperty.call(values, key)) {
        path = path.replace('{' + key + '}', encodeURIComponent(values[key]));
      }
    }
    return API + path;
  }

  function ownerHeader() {
    return { 'X-Owner-ID': byId('owner-id').value.trim() };
  }

  async function callApi(url, options) {
    var headers = Object.assign({}, options.headers || {}, ownerHeader());
    var response = await fetch(url, Object.assign({}, options, { headers: headers }));
    if (!response.ok) {
      var detail = response.statusText;
      try {
        var body = await response.json();
        if (body && body.detail) detail = body.detail;
      } catch (_) { /* keep status text */ }
      throw new Error(response.status + ' ' + detail);
    }
    return response;
  }

  function log(message) {
    var line = document.createElement('div');
    line.textContent = '[' + new Date().toISOString().slice(11, 19) + '] ' + message;
    byId('log').appendChild(line);
    byId('log').scrollTop = byId('log').scrollHeight;
  }

  function setBar(id, percent, label) {
    var bar = byId(id);
    bar.style.width = percent + '%';
    byId(id + '-label').textContent = label;
  }

  function formatBytes(value) {
    var units = ['B', 'KiB', 'MiB', 'GiB'];
    var index = 0;
    while (value >= 1024 && index < units.length - 1) { value /= 1024; index++; }
    return value.toFixed(index === 0 ? 0 : 1) + ' ' + units[index];
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  async function webcryptoSha256Hex(bytes) {
    var digest = await crypto.subtle.digest('SHA-256', bytes);
    return bytesToHex(new Uint8Array(digest));
  }

  function hashFile(file, onProgress) {
    return new Promise(function (resolve, reject) {
      var hasher = new Sha256();
      var chunkSize = 8 * 1024 * 1024;
      var offset = 0;
      function next() {
        if (offset >= file.size) {
          resolve(hasher.digestHex());
          return;
        }
        var slice = file.slice(offset, Math.min(offset + chunkSize, file.size));
        slice.arrayBuffer().then(function (buffer) {
          hasher.update(new Uint8Array(buffer));
          offset += chunkSize;
          onProgress(Math.min(100, Math.round(offset / file.size * 100)));
          next();
        }, reject);
      }
      next();
    });
  }

  function partCountOf(file, partSizeBytes) {
    return Math.max(1, Math.ceil(file.size / partSizeBytes));
  }

  async function uploadParts(file, session, uploadId, onPart) {
    var parts = [];
    var partCount = partCountOf(file, session.part_size_bytes);
    for (var n = 1; n <= partCount; n++) {
      var start = (n - 1) * session.part_size_bytes;
      var size = expectedPartSizeBytes(file.size, n, partCount);
      var slice = file.slice(start, start + size);
      var bytes = new Uint8Array(await slice.arrayBuffer());
      var partHex = await webcryptoSha256Hex(bytes);
      var presignResponse = await callApi(
        apiPath(EP.presignPart, { upload_id: uploadId, part_number: n }),
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ checksum_sha256: partHex })
        }
      );
      var presigned = await presignResponse.json();
      var putHeaders = {};
      for (var key in presigned.required_headers) {
        if (Object.prototype.hasOwnProperty.call(presigned.required_headers, key)) {
          if (key.toLowerCase() !== 'content-length') putHeaders[key] = presigned.required_headers[key];
        }
      }
      var put = await fetch(presigned.url, {
        method: 'PUT',
        headers: putHeaders,
        body: bytes
      });
      if (!put.ok) {
        throw new Error('part ' + n + ' upload failed: ' + put.status + ' ' + (await put.text()));
      }
      var etag = put.headers.get('etag') || '';
      parts.push({ part_number: n, etag: etag });
      onPart(n, partCount, presigned.expected_size_bytes);
    }
    return parts;
  }

  async function beginUpload(file) {
    log('hashing ' + file.name + ' (' + formatBytes(file.size) + ')');
    var checksum = await hashFile(file, function (percent) {
      setBar('hash-bar', percent, 'Hashing file: ' + percent + '%');
    });
    log('file SHA-256 ' + checksum.slice(0, 16) + '...');
    var created = await callApi(apiPath(EP.createUpload), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename: file.name,
        size_bytes: file.size,
        checksum_sha256: checksum
      })
    });
    var session = await created.json();
    state.uploadId = session.id;
    log('upload session ' + session.id + ' ready (' + formatBytes(session.size_bytes) + ')');
    var parts = await uploadParts(file, session, session.id, function (n, total, size) {
      setBar('parts-bar', Math.round(n / total * 100), 'Part ' + n + '/' + total + ' (' + formatBytes(size) + ')');
    });
    log('all ' + parts.length + ' parts transferred');
    var completed = await callApi(apiPath(EP.completeUpload, { upload_id: session.id }), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parts: parts })
    });
    var job = await completed.json();
    state.jobId = job.id;
    log('job ' + job.id + ' created from validated object');
    var started = await callApi(apiPath(EP.runFullMatch, { job_id: job.id }), { method: 'POST' });
    log('full-match run accepted: ' + (await started.json()).status);
    byId('stop-btn').disabled = false;
    state.running = true;
    state.timer = setInterval(pollStatus, 2000);
    await pollStatus();
  }

  async function pollStatus() {
    if (!state.jobId) return;
    var jobResponse = await callApi(apiPath(EP.jobStatus, { job_id: state.jobId }), {});
    var job = await jobResponse.json();
    var manifest = null;
    try {
      var fmResponse = await callApi(apiPath(EP.fullMatchStatus, { job_id: state.jobId }), {});
      manifest = (await fmResponse.json()).manifest;
    } catch (error) {
      log('status poll: ' + error.message);
    }
    renderJob(job, manifest);
    if (job.status === 'completed' || job.status === 'failed' || job.status === 'stopped') {
      clearInterval(state.timer);
      state.timer = null;
      state.running = false;
      byId('stop-btn').disabled = true;
      if (job.status === 'completed') {
        await renderResults();
      }
      log('job ' + job.status + ' (' + (job.progress || 0) + '%)');
    }
  }

  function renderJob(job, manifest) {
    setBar('job-bar', job.progress || 0, 'Job: ' + job.status + ' ' + (job.progress || 0) + '%');
    var html = '';
    if (manifest && manifest.chunks) {
      for (var i = 0; i < manifest.chunks.length; i++) {
        var chunk = manifest.chunks[i];
        var width = chunk.status === 'completed' ? 100 : 0;
        html += '<div class="chunk-row"><span>Chunk ' + (i + 1) + ' (' +
          Math.round(chunk.start_ms / 1000) + 's-' + Math.round(chunk.end_ms / 1000) + 's)</span>' +
          '<div class="chunk-bar"><div style="width:' + width + '%"></div></div>' +
          '<span class="chunk-status">' + escapeHtml(chunk.status) + '</span></div>';
      }
    }
    byId('chunks').innerHTML = html;
  }

  async function renderResults() {
    var eventsResponse = await callApi(apiPath(EP.events, { job_id: state.jobId }), {});
    var events = await eventsResponse.json();
    var rows = '';
    for (var i = 0; i < events.length; i++) {
      var event = events[i];
      var start = (event.start_ms / 1000).toFixed(1);
      var end = (event.end_ms / 1000).toFixed(1);
      var evidence = '';
      if (event.evidence) {
        evidence = event.evidence.map(function (item) {
          return escapeHtml(item.kind + '=' + item.value + ' (' + Math.round((item.confidence || 0) * 100) + '%)');
        }).join(', ');
      }
      var thumbUrl = apiPath(EP.eventThumbnail, { job_id: state.jobId, event_id: event.id });
      var clipUrl = apiPath(EP.eventClip, { job_id: state.jobId, event_id: event.id });
      rows += '<tr class="event-row" data-start="' + (event.start_ms / 1000) + '" data-event-id="' +
        escapeHtml(event.id) + '">' +
        '<td><img class="event-thumb" src="' + thumbUrl + '" loading="lazy" alt="event frame"></td>' +
        '<td>' + start + 's-' + end + 's</td><td>' + escapeHtml(event.event_type) +
        '</td><td>' + Math.round(event.confidence * 100) + '%</td><td>' +
        escapeHtml(event.needs_review ? 'review' : 'auto') + '</td><td>' +
        (event.source || []).join(', ') + '</td><td>' + evidence + '</td>' +
        '<td><button class="mini-btn seek-btn">\u25B6 seek</button> ' +
        '<button class="mini-btn clip-btn">clip</button></td></tr>';
    }
    byId('events-body').innerHTML = rows || '<tr><td colspan="8">no events</td></tr>';
    byId('events-panel').style.display = 'block';
    attachEventHandlers(events);
    renderScoreboard();
  }

  function attachEventHandlers(events) {
    var rows = document.querySelectorAll('#events-body .event-row');
    for (var i = 0; i < rows.length; i++) {
      (function (row, event) {
        row.addEventListener('click', function (evt) {
          if (evt.target.classList.contains('clip-btn')) {
            openClip(apiPath(EP.eventClip, { job_id: state.jobId, event_id: event.id }));
            return;
          }
          if (evt.target.classList.contains('seek-btn')) {
            seekVideo(event.start_ms);
            return;
          }
          seekVideo(event.start_ms);
        });
      })(rows[i], events[i]);
    }
  }

  function seekVideo(startMs) {
    var video = byId('video');
    if (!video) return;
    var seconds = Math.max(0, startMs / 1000);
    try {
      video.currentTime = seconds;
      video.play();
    } catch (_) { /* seek may fail before metadata loads */ }
  }

  function openClip(url) {
    var modal = byId('clip-modal');
    var clipVideo = byId('clip-video');
    clipVideo.src = url;
    modal.style.display = 'flex';
    clipVideo.play();
  }

  function closeClip() {
    var modal = byId('clip-modal');
    var clipVideo = byId('clip-video');
    clipVideo.pause();
    clipVideo.removeAttribute('src');
    clipVideo.load();
    modal.style.display = 'none';
  }

  async function renderScoreboard() {
    var scoreResponse = await callApi(apiPath(EP.scoreboard, { job_id: state.jobId }), {});
    var observations = await scoreResponse.json();
    var scoreRows = '';
    for (var i = 0; i < observations.length; i++) {
      var item = observations[i];
      scoreRows += '<tr><td>' + (item.timestamp_ms / 1000).toFixed(1) + 's</td><td>' +
        escapeHtml(item.raw_text || '') + '</td><td>' + Math.round((item.confidence || 0) * 100) +
        '%</td><td>' + escapeHtml(item.consensus_status || '') + '</td></tr>';
    }
    byId('scoreboard-body').innerHTML = scoreRows || '<tr><td colspan="4">no scoreboard observations</td></tr>';
    byId('scoreboard-panel').style.display = 'block';

    byId('video').src = apiPath(EP.annotatedVideo, { job_id: state.jobId });
    byId('heatmap').src = apiPath(EP.heatMap, { job_id: state.jobId });
    byId('results-panel').style.display = 'block';
  }

  async function stopJob() {
    if (!state.jobId) return;
    try {
      await callApi(apiPath(EP.stopJob, { job_id: state.jobId }), { method: 'POST' });
      log('stop requested');
    } catch (error) {
      log('stop failed: ' + error.message);
    }
  }

  function resetResults() {
    state.jobId = null;
    state.uploadId = null;
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    state.running = false;
    byId('stop-btn').disabled = true;
    byId('results-panel').style.display = 'none';
    byId('events-panel').style.display = 'none';
    byId('scoreboard-panel').style.display = 'none';
    byId('chunks').innerHTML = '';
    byId('events-body').innerHTML = '';
    byId('scoreboard-body').innerHTML = '';
    byId('log').innerHTML = '';
    setBar('hash-bar', 0, '');
    setBar('parts-bar', 0, '');
    setBar('job-bar', 0, '');
  }

  function init() {
    var owner = localStorage.getItem('fm-owner-id');
    if (!owner) {
      owner = 'browser-' + crypto.randomUUID();
      localStorage.setItem('fm-owner-id', owner);
    }
    byId('owner-id').value = owner;
    byId('upload-btn').addEventListener('click', async function () {
      var file = byId('file-input').files[0];
      if (!file) { log('choose a file first'); return; }
      resetResults();
      byId('upload-btn').disabled = true;
      try {
        await beginUpload(file);
      } catch (error) {
        log('FAILED: ' + error.message);
        if (state.uploadId) {
          try {
            await callApi(apiPath(EP.abortUpload, { upload_id: state.uploadId }), { method: 'DELETE' });
            log('upload aborted');
          } catch (_) { /* best effort */ }
        }
      } finally {
        byId('upload-btn').disabled = false;
      }
    });
    byId('stop-btn').addEventListener('click', stopJob);
    byId('clip-close').addEventListener('click', closeClip);
    byId('clip-modal').addEventListener('click', function (evt) {
      if (evt.target === byId('clip-modal')) closeClip();
    });
    byId('file-input').addEventListener('change', function () {
      var file = byId('file-input').files[0];
      if (!file) return;
      var parts = partCountOf(file, FM_PART_SIZE_BYTES);
      byId('file-info').textContent = file.name + ' — ' + formatBytes(file.size) +
        ' — ' + parts + ' part(s) of 16 MiB';
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
/* ==== fm-app-end ==== */
"""


def page_html() -> str:
    """Return the complete self-contained HTML document for the page."""
    return _PAGE_TEMPLATE.replace("__FM_ENDPOINTS__", json.dumps(ENDPOINTS, indent=2))


_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Full-Match Upload &amp; Analysis</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; margin: 0; background: #14161a; color: #e8eaed; }
  main { max-width: 960px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 1.4rem; }
  h2 { font-size: 1.05rem; margin-top: 24px; border-bottom: 1px solid #2a2d33; padding-bottom: 6px; }
  .card { background: #1c1f25; border: 1px solid #2a2d33; border-radius: 8px; padding: 16px; margin-top: 12px; }
  label { display: block; margin: 8px 0 4px; font-size: 0.85rem; color: #b6bac1; }
  input[type=text], input[type=file] { width: 100%; box-sizing: border-box; padding: 8px; border-radius: 6px; border: 1px solid #3a3f47; background: #14161a; color: #e8eaed; }
  button { padding: 8px 14px; border-radius: 6px; border: 0; background: #2f6fdb; color: white; cursor: pointer; }
  button:disabled { opacity: 0.45; cursor: not-allowed; }
  #stop-btn { background: #b3403a; }
  .meter { height: 12px; background: #14161a; border: 1px solid #3a3f47; border-radius: 6px; overflow: hidden; margin: 6px 0; }
  .meter > div { height: 100%; background: #2f6fdb; width: 0; transition: width 0.2s; }
  .meter-label { font-size: 0.8rem; color: #b6bac1; }
  #log { height: 180px; overflow-y: auto; font-family: monospace; font-size: 0.8rem; background: #101215; border: 1px solid #2a2d33; border-radius: 6px; padding: 8px; margin-top: 12px; }
  #log div { white-space: pre-wrap; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #2a2d33; }
  th { color: #b6bac1; font-weight: 600; }
  video, img { max-width: 100%; border-radius: 6px; }
  .chunk-row { display: flex; align-items: center; gap: 8px; font-size: 0.8rem; margin: 4px 0; }
  .chunk-row > span:first-child { width: 200px; }
  .chunk-bar { flex: 1; height: 10px; background: #14161a; border: 1px solid #3a3f47; border-radius: 5px; overflow: hidden; }
  .chunk-bar > div { height: 100%; background: #2f9d6b; width: 0; }
  .chunk-status { width: 80px; text-transform: uppercase; font-size: 0.7rem; color: #b6bac1; }
  .hidden { display: none; }
  .event-row { cursor: pointer; }
  .event-row:hover { background: #24282f; }
  .event-thumb { width: 96px; height: auto; border-radius: 4px; display: block; }
  .mini-btn { padding: 3px 8px; border-radius: 4px; border: 1px solid #3a3f47; background: #24282f; color: #e8eaed; cursor: pointer; font-size: 0.78rem; }
  .mini-btn:hover { background: #2f6fdb; }
  .clip-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.75); align-items: center; justify-content: center; z-index: 10; }
  .clip-modal-card { background: #1c1f25; border: 1px solid #3a3f47; border-radius: 8px; padding: 12px; max-width: 90vw; }
  .clip-modal-card video { max-width: 80vw; max-height: 80vh; border-radius: 6px; display: block; margin-top: 8px; }
</style>
</head>
<body>
<main>
  <h1>Football Video Intelligence — Full-Match Upload &amp; Analysis</h1>
  <p style="font-size:0.85rem;color:#b6bac1">
    Direct 16 MiB multipart upload to MinIO, restartable full-match processing,
    manual scoreboard OCR, heat map, and annotated H.264 output. Track IDs are
    visual IDs; all events are review candidates with confidence and evidence.
  </p>

  <div class="card">
    <label for="owner-id">Owner ID (X-Owner-ID header, kept in this browser)</label>
    <input type="text" id="owner-id" autocomplete="off">
    <label for="file-input">Match video (MP4/MKV/MOV, up to 12 GiB)</label>
    <input type="file" id="file-input" accept=".mp4,.mkv,.mov">
    <div id="file-info" class="meter-label" style="margin-top:6px"></div>
    <div style="margin-top:12px;display:flex;gap:8px">
      <button id="upload-btn">Upload and process</button>
      <button id="stop-btn" disabled>Stop processing</button>
    </div>
  </div>

  <h2>Progress</h2>
  <div class="card">
    <div class="meter-label" id="hash-bar-label"></div>
    <div class="meter"><div id="hash-bar"></div></div>
    <div class="meter-label" id="parts-bar-label"></div>
    <div class="meter"><div id="parts-bar"></div></div>
    <div class="meter-label" id="job-bar-label"></div>
    <div class="meter"><div id="job-bar"></div></div>
    <div id="chunks"></div>
  </div>

  <h2>Log</h2>
  <div id="log"></div>

  <h2>Results</h2>
  <div class="card hidden" id="results-panel">
    <h3>Annotated video</h3>
    <video id="video" controls playsinline></video>
    <h3>Player-density heat map (screen space, not pitch calibrated)</h3>
    <img id="heatmap" alt="heat map">
  </div>
  <div class="card hidden" id="events-panel">
    <h3>Event timeline (click a row to seek the video)</h3>
    <table>
      <thead><tr><th>Frame</th><th>Time</th><th>Type</th><th>Confidence</th><th>Review</th><th>Source</th><th>Evidence</th><th>Actions</th></tr></thead>
      <tbody id="events-body"></tbody>
    </table>
  </div>
  <div class="card hidden" id="scoreboard-panel">
    <h3>Scoreboard observations (OCR candidates)</h3>
    <table>
      <thead><tr><th>Time</th><th>Raw text</th><th>Confidence</th><th>Consensus</th></tr></thead>
      <tbody id="scoreboard-body"></tbody>
    </table>
  </div>

  <script id="fm-endpoints" type="application/json">__FM_ENDPOINTS__</script>
  <div id="clip-modal" class="clip-modal" style="display:none">
    <div class="clip-modal-card">
      <button id="clip-close" class="mini-btn">\u2715 close</button>
      <video id="clip-video" controls playsinline></video>
    </div>
  </div>
  <script>
__FM_JS_LIB__
__FM_APP_JS__
  </script>
</main>
</body>
</html>
"""

_PAGE_TEMPLATE = _PAGE_TEMPLATE.replace("__FM_JS_LIB__", _js_lib()).replace(
    "__FM_APP_JS__", _app_js()
)
