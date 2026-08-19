"""compare_core.py — render decks to per-slide PNGs and hash them for diffing.

A deck is converted to PDF via LibreOffice (`soffice`, handles both .ppt and
.pptx) and rasterized page-by-page with `pdftoppm`/`pdftocairo`. Each slide's
signature is a hash of its rendered pixels, so two slides count as "the same"
exactly when they render identically. Results are cached per source file (keyed
by path + mtime) so switching decks is cheap after the first pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional

DPI = 100


def _soffice() -> Optional[str]:
    return (shutil.which("soffice") or shutil.which("libreoffice")
            or "/Applications/LibreOffice.app/Contents/MacOS/soffice")


def _to_pdf(src: str, tmp: str) -> Optional[str]:
    soffice = _soffice()
    if not soffice or not os.path.exists(soffice):
        return None
    stem = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(tmp, stem + ".pdf")
    keep_hidden = ('pdf:impress_pdf_Export:'
                   '{"ExportHiddenSlides":{"type":"boolean","value":"true"}}')
    for arg in (keep_hidden, "pdf"):        # fall back if the filter is unsupported
        try:
            subprocess.run([soffice, "--headless", "--convert-to", arg,
                            "--outdir", tmp, src],
                           check=True, capture_output=True, timeout=180)
        except Exception:
            pass
        if os.path.exists(out):
            return out
    return None


def _rasterize(pdf: str, outdir: str, dpi: int = DPI) -> List[str]:
    tool = shutil.which("pdftoppm") or shutil.which("pdftocairo")
    if not tool:
        raise RuntimeError("need pdftoppm or pdftocairo to rasterize slides")
    prefix = os.path.join(outdir, "p")
    subprocess.run([tool, "-png", "-r", str(dpi), pdf, prefix],
                   check=True, capture_output=True, timeout=600)
    pages = [f for f in os.listdir(outdir)
             if f.startswith("p-") and f.endswith(".png")]
    return [os.path.join(outdir, f)
            for f in sorted(pages, key=lambda f: int(f[2:-4]))]


def _png_size(path: str) -> tuple[int, int]:
    with open(path, "rb") as fh:
        d = fh.read(24)
    return int.from_bytes(d[16:20], "big"), int.from_bytes(d[20:24], "big")


def _key(src: str) -> str:
    st = os.stat(src)
    raw = f"{os.path.abspath(src)}|{st.st_mtime_ns}|{DPI}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def render_deck(src: str, work_dir: str) -> Dict:
    """Render one deck (cached). Returns {key, n, images[], sigs[], w, h}.

    `images` are '<key>/p-<n>.png' paths relative to the images root; `sigs` are
    per-slide pixel hashes used to decide which slides differ.
    """
    key = _key(src)
    outdir = os.path.join(work_dir, key)
    manifest = os.path.join(outdir, "manifest.json")
    if os.path.exists(manifest):
        with open(manifest) as fh:
            return json.load(fh)

    os.makedirs(outdir, exist_ok=True)
    tmp = tempfile.mkdtemp()
    pdf = _to_pdf(src, tmp)
    if pdf is None:
        raise RuntimeError("LibreOffice (soffice) unavailable or conversion failed")
    pages = _rasterize(pdf, outdir)
    sigs = [hashlib.sha1(open(p, "rb").read()).hexdigest() for p in pages]
    w, h = _png_size(pages[0]) if pages else (16, 9)
    data = {"key": key, "n": len(pages), "w": w, "h": h, "sigs": sigs,
            "images": [f"{key}/{os.path.basename(p)}" for p in pages]}
    with open(manifest, "w") as fh:
        json.dump(data, fh)
    return data
