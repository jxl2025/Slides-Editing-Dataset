"""server.py — zero-dependency local backend for the annotation UI.

Run:
    python server.py path/to/deck.pptx --annotator alice

Then open http://localhost:8000 in a browser. The backend parses the deck,
renders slide thumbnails, computes candidates, loads any existing relationships
file for this annotator, serves the static frontend, and saves on demand.

Stdlib only. To swap in FastAPI later, keep the same four routes.
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import annotation_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}

# Populated at startup.
STATE: dict = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):        # quieter console
        pass

    # -- helpers --------------------------------------------------------- #
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"),
                   CONTENT_TYPES[".json"])

    def _send_file(self, path):
        if not os.path.exists(path):
            return self._send(404, b"not found")
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as fh:
            self._send(200, fh.read(),
                       CONTENT_TYPES.get(ext, "application/octet-stream"))

    # -- routing --------------------------------------------------------- #
    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/":
            return self._send_file(os.path.join(STATIC_DIR, "index.html"))
        if route in ("/app.js", "/style.css"):
            return self._send_file(os.path.join(STATIC_DIR, route.lstrip("/")))
        if route.startswith("/images/"):
            name = os.path.basename(route)
            return self._send_file(os.path.join(STATE["images_dir"], name))
        if route == "/api/deck":
            return self._send_json({
                "deck_id": STATE["deck_id"],
                "annotator": STATE["annotator"],
                "content": STATE["content"],
                "candidates": STATE["candidates"],
                "relationships": STATE["relationships"],
                "attr_choices": core.ATTR_CHOICES,
                "color_attrs": sorted(core.COLOR_ATTRS),
                "participants": core.list_participants(
                    STATE["work_dir"], STATE["deck_id"]),
            })
        if route == "/api/review":
            qs = parse_qs(urlparse(self.path).query)
            who = (qs.get("annotator") or [""])[0]
            if not who:
                return self._send_json({"ok": False, "error": "no annotator"}, 400)
            rp = core.relationships_path(STATE["work_dir"], STATE["deck_id"], who)
            sp = core.styles_path(STATE["work_dir"], STATE["deck_id"], who)
            return self._send_json({
                "ok": True, "annotator": who,
                "relationships": core.load_relationships(rp),
                "styles": core.load_styles(sp),
            })
        return self._send(404, b"not found")

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/api/export_candidates":
            path = core.candidates_path(STATE["work_dir"], STATE["deck_id"])
            core.save_candidates(path, STATE["deck_id"], STATE["candidates"])
            return self._send_json({"ok": True, "path": path,
                                    "count": len(STATE["candidates"])})
        if route != "/api/save":
            return self._send(404, b"not found")
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send_json({"ok": False, "error": "bad json"}, 400)
        rels = data.get("relationships", [])
        styles = data.get("styles", [])
        rp = core.save_relationships(
            STATE["rel_path"], STATE["deck_id"], STATE["annotator"], rels)
        sp = core.save_styles(
            STATE["styles_path"], STATE["deck_id"], STATE["annotator"], styles)
        cp = core.save_content(STATE["content_path"], STATE["content"])
        STATE["relationships"] = rels
        return self._send_json({"ok": True, "relationships": rp, "styles": sp,
                                "content": cp,
                                "counts": {"relationships": len(rels),
                                           "styles": len(styles)}})


def build_state(pptx_path, work_dir, annotator, render):
    deck = core.parse_content(pptx_path, work_dir, render=render)
    rel_path = core.relationships_path(work_dir, deck.deck_id, annotator)
    STATE.update({
        "deck_id": deck.deck_id,
        "annotator": annotator,
        "work_dir": work_dir,
        "images_dir": os.path.join(work_dir, "images"),
        "rel_path": rel_path,
        "styles_path": core.styles_path(work_dir, deck.deck_id, annotator),
        "content_path": core.content_path(work_dir, deck.deck_id),
        "content": core.content_payload(deck),
        "candidates": core.make_candidates(deck),
        "relationships": core.load_relationships(rel_path),
    })
    return deck


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx", help="path to the .pptx deck")
    ap.add_argument("--annotator", default="anon")
    ap.add_argument("--out", default="annotation_work", help="work directory")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-render", action="store_true",
                    help="skip thumbnail rendering (no LibreOffice)")
    args = ap.parse_args()

    build_state(args.pptx, args.out, args.annotator, render=not args.no_render)
    n_slides = len(STATE["content"]["slides"])
    print(f"Loaded {STATE['deck_id']}: {n_slides} slides, "
          f"{len(STATE['candidates'])} candidates, "
          f"{len(STATE['relationships'])} saved relationships.")
    print(f"Annotating as '{args.annotator}'. "
          f"Saving to {STATE['rel_path']}")
    print(f"Open http://localhost:{args.port}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
