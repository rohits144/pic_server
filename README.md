# Burst Photo Detector

Finds photos taken in quick succession (burst shots) by reading their EXIF
timestamps. Groups them visually in a browser UI so you can review each burst
and choose which photos to keep or move to a trash folder.

```
📁 Your photos folder
 ├── IMG_4021.jpg  ← 14:22:01  ┐
 ├── IMG_4022.jpg  ← 14:22:01  ├── Burst group (taken within 3s)
 ├── IMG_4023.jpg  ← 14:22:02  ┘
 ├── IMG_4031.jpg  ← 14:25:47  ── Unique shot (not a burst)
 └── IMG_4042.jpg  ← 14:31:10  ── Unique shot
```

---

## How it works

1. Reads every image file in the folder you specify.
2. Extracts the capture time from EXIF data (falls back to file modification
   time if EXIF is missing).
3. Sorts images by timestamp, then groups consecutive ones whose timestamps
   are within `--gap` seconds of each other.
4. Starts a local web server and opens a browser UI where you can review each
   group, mark photos to delete, and move them to a `burst_deleted/` subfolder.

Deleted files are **moved, not permanently deleted**, so you can recover them.

---

## Files

```
burst_detector/
└── server.py   ← the only file you need
```

The HTML, CSS, and JavaScript for the UI are embedded inside `server.py`.
No build step, no extra files, no framework needed.

---

## Dependencies

| Package  | What it does                        | Version  |
|----------|-------------------------------------|----------|
| Pillow   | Opens image files                   | ≥ 9.0    |
| piexif   | Reads EXIF metadata from JPEG/TIFF  | ≥ 1.1    |

Python's standard library covers everything else (`http.server`, `json`,
`shutil`, `argparse`, `base64`, `os`).

---

## Setup — step by step

### Step 1 — Check your Python version

```
python --version
```

You need Python 3.8 or newer. If you see `Python 2.x`, try `python3 --version`
and use `python3` / `pip3` in all commands below.

---

### Step 2 — (Optional but recommended) Create a virtual environment

A virtual environment keeps these packages isolated from your system Python.

```
python -m venv venv
```

Then activate it:

**macOS / Linux:**
```
source venv/bin/activate
```

**Windows (Command Prompt):**
```
venv\Scripts\activate.bat
```

**Windows (PowerShell):**
```
venv\Scripts\Activate.ps1
```

You will see `(venv)` at the start of your prompt when it is active.

---

### Step 3 — Install dependencies

```
pip install Pillow piexif
```

- `Pillow` — image library used to open photos and generate thumbnails.
- `piexif` — lightweight library to read EXIF metadata.

To install specific versions (for reproducibility):

```
pip install Pillow==10.3.0 piexif==1.1.3
```

---

### Step 4 — Run the tool

```
python server.py --folder /path/to/your/photos
```

**Arguments:**

| Argument   | Required | Default | Description                                            |
|------------|----------|---------|--------------------------------------------------------|
| `--folder` | Yes      | —       | Path to the folder containing your photos.             |
| `--gap`    | No       | `3`     | Max seconds between photos to count as a burst.        |
| `--port`   | No       | `8000`  | Port number for the local web UI.                      |

**Examples:**

```
# Scan a folder with the default 3-second gap
python server.py --folder ~/Pictures/Vacation2024

# Use a tighter 1-second gap (only very fast burst shots)
python server.py --folder ~/Pictures/Vacation2024 --gap 1

# Use a looser 10-second gap (groups photos taken close together)
python server.py --folder ~/Pictures/Vacation2024 --gap 10

# Run on a different port if 8000 is already in use
python server.py --folder ~/Pictures/Vacation2024 --port 9090

# All options together
python server.py --folder ~/Pictures/Vacation2024 --gap 2 --port 8080
```

---

### Step 5 — Open the UI

After running the command you will see:

```
  Folder : /Users/you/Pictures/Vacation2024
  Gap    : 3.0s
  Open   : http://127.0.0.1:8000
  Stop   : Ctrl+C
```

Open `http://127.0.0.1:8000` in your browser.

---

### Step 6 — Review and delete

- **Burst groups** (multiple photos) are expanded automatically.
- **Unique shots** are collapsed — click the header to expand.
- Click **Keep** or **Delete** on each photo.
- When done, click **Move selected to trash folder**.
- Files are moved to a `burst_deleted/` subfolder inside your photos folder —
  not permanently deleted.

To stop the server, press `Ctrl+C` in the terminal.

---

## Recovering deleted photos

Photos are moved (not deleted) to:

```
/path/to/your/photos/burst_deleted/
```

To restore them, just move them back.

---

## Supported image formats

`.jpg` `.jpeg` `.png` `.heic` `.heif` `.webp` `.tiff` `.tif`

Note: HEIC files (iPhone default format) require Pillow with HEIC support.
If thumbnails fail for HEIC, install the optional plugin:

```
pip install pillow-heif
```

---

## Troubleshooting

**Port already in use:**
```
python server.py --folder /your/folder --port 9090
```

**No EXIF data / wrong groupings:**
Some photos (screenshots, edited photos) lose EXIF data. The tool falls back
to file modification time and marks those photos with a yellow `mtime` badge.
Adjust `--gap` to get better groupings.

**Permission denied on delete:**
Make sure you have write permission on the photos folder.

**piexif error on HEIC files:**
HEIC files store metadata differently. EXIF read will silently fall back to
file modification time. Thumbnails may also fail — install `pillow-heif` above.
