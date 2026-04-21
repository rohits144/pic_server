"""
server.py  —  Burst Photo Detector
------------------------------------
Scans a folder for images, groups photos taken within N seconds of each other
(using EXIF timestamp), then serves a web UI to review and delete duplicates.

Run:
    python server.py --folder /path/to/photos --gap 3 --port 8000
"""

import os
import sys
import json
import shutil
import argparse
import base64
import mimetypes
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    from PIL import Image
    import piexif
except ImportError:
    print("Missing dependencies. Run:  pip install Pillow piexif")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tiff"}
EXIF_DT_TAGS = (piexif.ExifIFD.DateTimeOriginal, piexif.ExifIFD.DateTimeDigitized)
EXIF_DT_FORMAT = "%Y:%m:%d %H:%M:%S"
THUMBNAIL_SIZE = (600, 600)  # max thumbnail dimensions sent to browser (increased for better preview)


# ---------------------------------------------------------------------------
# Step 1 — Read EXIF timestamp from a single image
# ---------------------------------------------------------------------------

def read_timestamp(image_path):
    """
    Returns (datetime, source) for the image at image_path.
    source is "exif" if an EXIF datetime was found, else "file_mtime".
    Never raises — always returns something usable.
    """
    try:
        img = Image.open(image_path)
        raw_exif = img.info.get("exif")
        if raw_exif:
            exif = piexif.load(raw_exif)
            exif_block = exif.get("Exif", {})
            for tag in EXIF_DT_TAGS:
                value = exif_block.get(tag)
                if value:
                    try:
                        dt = datetime.strptime(value.decode(), EXIF_DT_FORMAT)
                        return dt, "exif"
                    except (ValueError, AttributeError):
                        pass
    except Exception:
        pass

    # Fallback: use file modification time
    mtime = os.path.getmtime(image_path)
    return datetime.fromtimestamp(mtime), "file_mtime"


# ---------------------------------------------------------------------------
# Step 2 — Collect all images in the folder
# ---------------------------------------------------------------------------

def collect_images(folder):
    """
    Returns a list of dicts, one per image file, sorted by timestamp.
    Each dict has: path, filename, timestamp (datetime), timestamp_source, size_bytes.
    """
    images = []
    for filename in os.listdir(folder):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        full_path = os.path.join(folder, filename)
        if not os.path.isfile(full_path):
            continue
        ts, source = read_timestamp(full_path)
        images.append({
            "path": full_path,
            "filename": filename,
            "timestamp": ts,
            "timestamp_source": source,
            "size_bytes": os.path.getsize(full_path),
        })

    images.sort(key=lambda x: x["timestamp"])
    return images


# ---------------------------------------------------------------------------
# Step 3 — Group images by time proximity
# ---------------------------------------------------------------------------

def group_by_time(images, gap_seconds=3):
    """
    Groups consecutive images whose timestamps are within gap_seconds of each other.
    Returns a list of groups. Each group is a list of image dicts.
    Images must already be sorted by timestamp (collect_images does this).
    """
    if not images:
        return []

    groups = [[images[0]]]

    for image in images[1:]:
        prev = groups[-1][-1]
        diff = (image["timestamp"] - prev["timestamp"]).total_seconds()
        if diff <= gap_seconds:
            groups[-1].append(image)  # same burst
        else:
            groups.append([image])    # new group

    return groups


# ---------------------------------------------------------------------------
# Step 4 — Serialise for JSON (datetime is not JSON-serialisable by default)
# ---------------------------------------------------------------------------

def groups_to_json(groups):
    """
    Converts the list of groups (list of list of dicts) into a JSON-safe structure.
    Returns a list of group dicts, each with an id, count, is_burst, and photos list.
    """
    result = []
    for i, group in enumerate(groups):
        photos = []
        for p in group:
            photos.append({
                "path": p["path"],
                "filename": p["filename"],
                "timestamp": p["timestamp"].isoformat(),
                "timestamp_source": p["timestamp_source"],
                "size_bytes": p["size_bytes"],
            })

        first_ts = group[0]["timestamp"]
        last_ts = group[-1]["timestamp"]
        span = (last_ts - first_ts).total_seconds()

        result.append({
            "group_id": i + 1,
            "count": len(group),
            "is_burst": len(group) > 1,
            "time_span_seconds": round(span, 2),
            "photos": photos,
        })

    return result


# ---------------------------------------------------------------------------
# Step 5 — Thumbnail helper (sends image to browser as base64)
# ---------------------------------------------------------------------------

def make_thumbnail_b64(image_path):
    """
    Opens image, resizes it to THUMBNAIL_SIZE, and returns a base64 data-URI string.
    Returns None if the image cannot be opened.
    """
    try:
        img = Image.open(image_path)
        img.thumbnail(THUMBNAIL_SIZE)
        img = img.convert("RGB")  # ensure JPEG-compatible mode

        import io
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return None


def make_image_b64(image_path):
    """
    Returns the original image file (no resize) as a base64 data-URI string.
    Returns None if the image cannot be opened.
    """
    try:
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type:
            mime_type = "application/octet-stream"
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:{mime_type};base64,{b64}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Step 6 — HTTP request handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    # Suppress default access log to keep terminal clean
    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    # --- GET routing ---

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self.send_html(HTML_PAGE)

        elif path == "/api/scan":
            # Returns grouped images as JSON
            folder = self.server.folder
            gap    = self.server.gap_seconds
            images = collect_images(folder)
            groups = group_by_time(images, gap)
            data   = groups_to_json(groups)
            self.send_json({"folder": folder, "gap_seconds": gap, "groups": data})

        elif path == "/api/thumbnail":
            # Returns a single image thumbnail as base64 JSON
            image_path = params.get("path", [None])[0]
            if not image_path or not os.path.isfile(image_path):
                self.send_json({"error": "file not found"}, 404)
                return
            # Security: only serve files inside the configured folder
            if not image_path.startswith(self.server.folder):
                self.send_json({"error": "access denied"}, 403)
                return
            b64 = make_thumbnail_b64(image_path)
            if b64:
                self.send_json({"thumbnail": b64})
            else:
                self.send_json({"error": "could not open image"}, 500)

        elif path == "/api/image":
            # Returns the original image as base64 (for magnified view)
            image_path = params.get("path", [None])[0]
            if not image_path or not os.path.isfile(image_path):
                self.send_json({"error": "file not found"}, 404)
                return
            # Security: only serve files inside the configured folder
            if not image_path.startswith(self.server.folder):
                self.send_json({"error": "access denied"}, 403)
                return
            b64 = make_image_b64(image_path)
            if b64:
                self.send_json({"image": b64})
            else:
                self.send_json({"error": "could not open image"}, 500)

        else:
            self.send_json({"error": "not found"}, 404)

    # --- POST routing ---

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Read request body
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"error": "invalid JSON"}, 400)
            return

        if path == "/api/delete":
            # Moves files to a 'burst_deleted' subfolder (safe — not permanent delete)
            paths_to_delete = payload.get("paths", [])
            deleted = []
            errors = []

            trash_folder = os.path.join(self.server.folder, "burst_deleted")
            os.makedirs(trash_folder, exist_ok=True)

            for file_path in paths_to_delete:
                # Security: only delete files inside the configured folder
                if not file_path.startswith(self.server.folder):
                    errors.append({"path": file_path, "error": "access denied"})
                    continue
                if not os.path.isfile(file_path):
                    errors.append({"path": file_path, "error": "file not found"})
                    continue
                try:
                    dest = os.path.join(trash_folder, os.path.basename(file_path))
                    shutil.move(file_path, dest)
                    deleted.append(file_path)
                except Exception as e:
                    errors.append({"path": file_path, "error": str(e)})

            self.send_json({"deleted": deleted, "errors": errors, "trash": trash_folder})

        else:
            self.send_json({"error": "not found"}, 404)


# ---------------------------------------------------------------------------
# Step 7 — HTML + JS frontend (single string, served at "/")
# ---------------------------------------------------------------------------

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Burst Photo Detector</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #f5f5f5; color: #222; }
  header { background: #fff; border-bottom: 1px solid #ddd; padding: 16px 24px;
           display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 18px; font-weight: 600; }
  #status { font-size: 13px; color: #666; }
  .stats { display: flex; gap: 12px; padding: 16px 24px; }
  .stat { background: #fff; border: 1px solid #ddd; border-radius: 8px;
          padding: 12px 18px; min-width: 120px; }
  .stat-label { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.04em; }
  .stat-val { font-size: 24px; font-weight: 600; color: #111; margin-top: 2px; }
  main { padding: 0 24px 24px; }
  .group { background: #fff; border: 1px solid #ddd; border-radius: 10px;
           margin-bottom: 12px; overflow: hidden; }
  .group-header { padding: 12px 16px; display: flex; justify-content: space-between;
                  align-items: center; cursor: pointer; }
  .group-header:hover { background: #fafafa; }
  .group-title { font-size: 14px; font-weight: 500; display: flex; align-items: center; gap: 8px; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 20px;
           background: #dbeafe; color: #1d4ed8; font-weight: 500; }
  .group-meta { font-size: 12px; color: #666; }
  .group-body { display: none; padding: 12px 16px; border-top: 1px solid #eee; }
  .group-body.open { display: block; }
  .photo-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
  .photo-card { border: 1px solid #ddd; border-radius: 8px; overflow: hidden;
                transition: opacity 0.2s, transform 0.15s; background: #fff; }
  .photo-card:hover { transform: translateY(-4px); }
  .photo-card.deleted { opacity: 0.35; }
  .thumb { width: 100%; aspect-ratio: 4/3; background: #eee; display: flex;
           align-items: center; justify-content: center; overflow: hidden; cursor: zoom-in; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; }
  .thumb .icon { font-size: 32px; color: #bbb; }
  /* Modal (magnifier) */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: none;
                   align-items: center; justify-content: center; z-index: 9999; padding: 20px; }
  .modal-overlay.open { display: flex; }
  .modal-img { max-width: 95%; max-height: 95%; border-radius: 8px; box-shadow: 0 8px 30px rgba(0,0,0,0.5); }
  .modal-close { position: absolute; top: 18px; right: 22px; background: rgba(255,255,255,0.9); border-radius: 999px;
                 padding: 6px 10px; cursor: pointer; font-weight: 700; }
  .photo-info { padding: 8px 10px; }
  .photo-name { font-size: 12px; font-weight: 500; white-space: nowrap;
                overflow: hidden; text-overflow: ellipsis; }
  .photo-time { font-size: 11px; color: #666; margin-top: 2px; }
  .photo-size { font-size: 11px; color: #999; }
  .src-exif  { font-size: 10px; padding: 1px 6px; border-radius: 20px;
               background: #dcfce7; color: #166534; display: inline-block; margin-top: 3px; }
  .src-mtime { font-size: 10px; padding: 1px 6px; border-radius: 20px;
               background: #fef9c3; color: #854d0e; display: inline-block; margin-top: 3px; }
  .photo-actions { display: flex; gap: 6px; padding: 0 10px 10px; }
  .btn-keep { flex: 1; font-size: 12px; padding: 5px; border-radius: 6px; cursor: pointer;
              border: 1px solid #ccc; background: #fff; color: #333; }
  .btn-keep.active { background: #dcfce7; border-color: #16a34a; color: #166534; }
  .btn-del  { flex: 1; font-size: 12px; padding: 5px; border-radius: 6px; cursor: pointer;
              border: 1px solid #ccc; background: #fff; color: #333; }
  .btn-del.active { background: #fee2e2; border-color: #dc2626; color: #991b1b; }
  .toolbar { display: flex; gap: 10px; align-items: center; padding: 12px 24px;
             background: #fff; border-top: 1px solid #ddd; position: sticky; bottom: 0; }
  .toolbar span { font-size: 13px; color: #666; flex: 1; }
  .btn-action { padding: 8px 20px; border-radius: 8px; font-size: 13px; cursor: pointer; border: none; }
  .btn-primary { background: #111; color: #fff; }
  .btn-primary:hover { background: #333; }
  .btn-secondary { background: #fff; color: #333; border: 1px solid #ccc; }
  .btn-secondary:hover { background: #f5f5f5; }
  .empty { text-align: center; padding: 60px 24px; color: #888; font-size: 15px; }
</style>
</head>
<body>

<header>
  <h1>Burst Photo Detector</h1>
  <span id="status">Loading...</span>
</header>

<div class="stats" id="stats" style="display:none">
  <div class="stat"><div class="stat-label">Total photos</div><div class="stat-val" id="s-total">0</div></div>
  <div class="stat"><div class="stat-label">Burst groups</div><div class="stat-val" id="s-bursts">0</div></div>
  <div class="stat"><div class="stat-label">In bursts</div><div class="stat-val" id="s-inburst">0</div></div>
  <div class="stat"><div class="stat-label">Marked to delete</div><div class="stat-val" id="s-del">0</div></div>
</div>

<main id="main"><div class="empty">Scanning...</div></main>

<div class="toolbar">
  <span id="toolbar-info">Select photos to delete</span>
  <button class="btn-action btn-secondary" onclick="expandAll()">Expand all</button>
  <button class="btn-action btn-primary" id="btn-delete" onclick="confirmDelete()">
    Move selected to trash folder
  </button>
</div>

<!-- Modal for magnified image -->
<div id="modal" class="modal-overlay" onclick="closeModal(event)">
  <div class="modal-close" onclick="closeModal(event)">✕</div>
  <img id="modal-img" class="modal-img" src="" alt=""/>
</div>

<script>
// --- State ---
const state = { groups: [], kept: {}, toDelete: {} };

// --- Helpers ---
function fmtSize(b) {
  return b > 1048576 ? (b/1048576).toFixed(1)+' MB' : (b/1024).toFixed(0)+' KB';
}
function fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}
function countDeleted() {
  return Object.values(state.toDelete).filter(Boolean).length;
}

// --- Initial load: fetch scan results ---
async function load() {
  const res = await fetch('/api/scan');
  const data = await res.json();
  state.groups = data.groups;
  document.getElementById('status').textContent =
    'Folder: ' + data.folder + '  |  Gap: ' + data.gap_seconds + 's';
  render();
}

// --- Render all groups ---
function render() {
  const main = document.getElementById('main');
  if (!state.groups.length) {
    main.innerHTML = '<div class="empty">No images found in folder.</div>';
    return;
  }

  let html = '';
  state.groups.forEach((g, i) => {
    const burstBadge = g.is_burst ? '<span class="badge">burst</span>' : '';
    const span = g.is_burst ? g.time_span_seconds.toFixed(1)+'s span' : 'unique shot';
    const open = g.is_burst ? 'open' : '';
    html += `
      <div class="group">
        <div class="group-header" onclick="toggleGroup(${i})">
          <div class="group-title">Group ${g.group_id} ${burstBadge}</div>
          <div class="group-meta">${g.count} photo${g.count>1?'s':''} &nbsp;·&nbsp; ${span}</div>
        </div>
        <div class="group-body ${open}" id="body-${i}">
          <div class="photo-grid">${g.photos.map(p => photoCard(p)).join('')}</div>
        </div>
      </div>`;
  });
  main.innerHTML = html;

  // Lazy-load thumbnails for open burst groups
  state.groups.forEach((g, i) => {
    if (g.is_burst) loadThumbnails(g.photos);
  });

  updateStats();
}

// --- Single photo card HTML ---
function photoCard(p) {
  const path64 = encodeURIComponent(p.path);
  const srcClass = p.timestamp_source === 'exif' ? 'src-exif' : 'src-mtime';
  const srcLabel = p.timestamp_source === 'exif' ? 'EXIF' : 'mtime';
  const isDel = state.toDelete[p.path];
  const isKept = state.kept[p.path];
  return `
    <div class="photo-card ${isDel?'deleted':''}" id="card-${path64}">
      <div class="thumb" onclick="showImage('${p.path.replace(/'/g, "\\'")}')">
        <img id="thumb-${path64}" src="" alt="" style="display:none"
             onload="this.style.display='block';this.previousSibling.style.display='none'">
        <div class="icon">&#128247;</div>
      </div>
      <div class="photo-info">
        <div class="photo-name" title="${p.path}">${p.filename}</div>
        <div class="photo-time">${fmtTime(p.timestamp)}</div>
        <div class="photo-size">${fmtSize(p.size_bytes)}</div>
        <span class="${srcClass}">${srcLabel}</span>
      </div>
      <div class="photo-actions">
        <button class="btn-keep ${isKept?'active':''}" onclick="markKeep('${p.path.replace(/'/g, "\\'")}')">
          ${isKept ? '✓ Keeping' : 'Keep'}
        </button>
        <button class="btn-del ${isDel?'active':''}" onclick="markDelete('${p.path.replace(/'/g, "\\'")}')">
          ${isDel ? '✕ Delete' : 'Delete'}
        </button>
      </div>
    </div>`;
}

// --- Load thumbnail for a list of photos ---
async function loadThumbnails(photos) {
  for (const p of photos) {
    const path64 = encodeURIComponent(p.path);
    const img = document.getElementById('thumb-' + path64);
    if (!img || img.src.startsWith('data:')) continue;
    try {
      const res = await fetch('/api/thumbnail?path=' + encodeURIComponent(p.path));
      const data = await res.json();
      if (data.thumbnail) img.src = data.thumbnail;
    } catch (e) { /* silent */ }
  }
}

// --- Show full-size image in modal ---
async function showImage(path) {
  const modal = document.getElementById('modal');
  const modalImg = document.getElementById('modal-img');
  modal.classList.add('open');
  modalImg.src = ''; // clear while loading
  try {
    const res = await fetch('/api/image?path=' + encodeURIComponent(path));
    const data = await res.json();
    if (data.image) {
      modalImg.src = data.image;
    } else {
      modalImg.alt = 'Could not load image';
    }
  } catch (e) {
    modalImg.alt = 'Error loading image';
  }
}

function closeModal(e) {
  // prevent propagation from close button to overlay double-run
  if (e) e.stopPropagation && e.stopPropagation();
  const modal = document.getElementById('modal');
  const modalImg = document.getElementById('modal-img');
  modal.classList.remove('open');
  modalImg.src = '';
}

// --- Toggle group open/closed ---
function toggleGroup(i) {
  const body = document.getElementById('body-' + i);
  const wasOpen = body.classList.toggle('open');
  if (wasOpen) loadThumbnails(state.groups[i].photos);
}

function expandAll() {
  state.groups.forEach((g, i) => {
    const body = document.getElementById('body-' + i);
    if (body) { body.classList.add('open'); loadThumbnails(g.photos); }
  });
}

// --- Mark photo as keep or delete ---
function markKeep(path) {
  state.kept[path] = !state.kept[path];
  if (state.kept[path]) delete state.toDelete[path];
  refreshCard(path);
  updateStats();
}
function markDelete(path) {
  state.toDelete[path] = !state.toDelete[path];
  if (state.toDelete[path]) delete state.kept[path];
  refreshCard(path);
  updateStats();
}

// Re-render just one card without full re-render
function refreshCard(path) {
  const path64 = encodeURIComponent(path);
  const cardEl = document.getElementById('card-' + path64);
  if (!cardEl) return;

  // Find the photo data
  for (const g of state.groups) {
    const p = g.photos.find(x => x.path === path);
    if (p) { cardEl.outerHTML = photoCard(p); return; }
  }
}

// --- Stats bar ---
function updateStats() {
  const total   = state.groups.reduce((a, g) => a + g.count, 0);
  const bursts  = state.groups.filter(g => g.is_burst).length;
  const inBurst = state.groups.filter(g => g.is_burst).reduce((a,g) => a+g.count, 0);
  const nDel    = countDeleted();

  document.getElementById('stats').style.display = 'flex';
  document.getElementById('s-total').textContent  = total;
  document.getElementById('s-bursts').textContent = bursts;
  document.getElementById('s-inburst').textContent = inBurst;
  document.getElementById('s-del').textContent    = nDel;
  document.getElementById('toolbar-info').textContent =
    nDel === 0 ? 'Mark photos to delete, then click the button'
               : nDel + ' photo' + (nDel>1?'s':'') + ' marked — will move to burst_deleted/ subfolder';
}

// --- Confirm + send delete request ---
async function confirmDelete() {
  const paths = Object.entries(state.toDelete).filter(([,v])=>v).map(([k])=>k);
  if (!paths.length) { alert('No photos marked for deletion.'); return; }
  if (!confirm('Move ' + paths.length + ' photo(s) to burst_deleted/ folder?')) return;

  const res  = await fetch('/api/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ paths })
  });
  const data = await res.json();

  if (data.errors && data.errors.length) {
    alert('Some files could not be moved:\\n' + data.errors.map(e=>e.path+': '+e.error).join('\\n'));
  }
  alert('Done! ' + data.deleted.length + ' photo(s) moved to:\\n' + data.trash);

  // Refresh the scan
  state.toDelete = {};
  state.kept = {};
  await load();
}

// --- Boot ---
load();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Step 8 — Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Burst Photo Detector — groups similar photos by EXIF timestamp."
    )
    parser.add_argument(
        "--folder", required=True,
        help="Path to the folder containing your photos."
    )
    parser.add_argument(
        "--gap", type=float, default=3.0,
        help="Max seconds between photos to count as a burst. Default: 3"
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="Port to run the web UI on. Default: 8000"
    )
    args = parser.parse_args()

    # Validate folder
    if not os.path.isdir(args.folder):
        print(f"Error: folder not found: {args.folder}")
        sys.exit(1)

    # Attach config to server so handler can read it
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    server.folder = os.path.abspath(args.folder)
    server.gap_seconds = args.gap

    url = f"http://127.0.0.1:{args.port}"
    print(f"  Folder : {server.folder}")
    print(f"  Gap    : {args.gap}s")
    print(f"  Open   : {url}")
    print(f"  Stop   : Ctrl+C\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
