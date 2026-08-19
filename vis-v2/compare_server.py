"""compare_server.py — three-column slide comparison viewer.

Serves a page that shows, side by side, the ORIGINAL deck, the TARGET (edited
ground-truth) deck, and a study PARTICIPANT's response deck, with synced scroll
and diff modes that hide carbon-copy slides.

Run:
    python compare_server.py --decks DECKS_ROOT [--responses RESP_ROOT] \
        [--out compare_work] [--port 8009]

DECKS_ROOT holds one folder per deck (d1/, d3/, ...). Inside d3/ there is the
original d3.pptx (or d3.ppt) and the per-task targets d3-e1.pptx, d3-e2.pptx, …
RESP_ROOT (optional) is scanned recursively for participant response files whose
name contains "<deck>-<task>".
"""

from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import compare_core as core
import compare_struct as struct

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_static() -> str:
    for cand in (os.path.join(HERE, "compare_static"),
                 os.path.join(os.getcwd(), "compare_static")):
        if os.path.exists(os.path.join(cand, "index.html")):
            return cand
    return os.path.join(HERE, "compare_static")   # default (may not exist yet)


STATIC_DIR = _find_static()
CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".png": "image/png",
          ".json": "application/json; charset=utf-8"}
STATE: dict = {}


def _find(folder: str, names: list[str]) -> str | None:
    for n in names:
        p = os.path.join(folder, n)
        if os.path.exists(p):
            return p
    return None


def scan_decks(root: str) -> list[dict]:
    decks = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        orig = _find(d, [f"{name}.pptx", f"{name}.ppt"])
        if not orig:
            continue
        tasks = set()
        for f in os.listdir(d):
            m = re.match(rf"^{re.escape(name)}-(e\d+)\.(pptx|ppt)$", f, re.I)
            if m:
                tasks.add(m.group(1).lower())
        decks.append({"id": name, "dir": d, "orig": orig,
                      "tasks": sorted(tasks, key=lambda t: int(t[1:]))})
    return decks


def find_responses(root: str | None, deck: str, task: str) -> list[dict]:
    if not root:
        return []
    key = f"{deck}-{task}".lower()
    out = []
    for dp, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith((".pptx", ".ppt")) and key in f.lower():
                p = os.path.join(dp, f)
                out.append({"label": os.path.relpath(p, root), "path": p})
    return sorted(out, key=lambda r: r["label"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"), CTYPES[".json"])

    def _file(self, path):
        if not os.path.exists(path):
            return self._send(404, b"not found")
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as fh:
            self._send(200, fh.read(), CTYPES.get(ext, "application/octet-stream"))

    def do_GET(self):
        route = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        if route == "/":
            return self._file(os.path.join(STATIC_DIR, "index.html"))
        if route in ("/compare.js", "/compare.css"):
            return self._file(os.path.join(STATIC_DIR, route.lstrip("/")))
        if route.startswith("/images/"):
            rel = route[len("/images/"):]
            safe = os.path.normpath(rel).lstrip(os.sep)
            return self._file(os.path.join(STATE["work_dir"], safe))
        if route == "/api/decks":
            return self._json({"decks": [{"id": d["id"], "tasks": d["tasks"]}
                                         for d in STATE["decks"]]})
        if route == "/api/responses":
            deck = (qs.get("deck") or [""])[0]
            task = (qs.get("task") or [""])[0]
            return self._json({"responses": find_responses(
                STATE["responses_root"], deck, task)})
        if route == "/api/compare":
            return self._compare(qs)
        return self._send(404, b"not found")

    def _compare(self, qs):
        deck_id = (qs.get("deck") or [""])[0]
        task = (qs.get("task") or [""])[0]
        resp = (qs.get("response") or [""])[0]
        deck = next((d for d in STATE["decks"] if d["id"] == deck_id), None)
        if not deck:
            return self._json({"ok": False, "error": "unknown deck"}, 400)
        target = _find(deck["dir"], [f"{deck_id}-{task}.pptx", f"{deck_id}-{task}.ppt"])
        if not target:
            return self._json({"ok": False, "error": "unknown task"}, 400)
        try:
            cols = {"original": core.render_deck(deck["orig"], STATE["work_dir"]),
                    "target": core.render_deck(target, STATE["work_dir"])}
            resp_path = None
            if resp:
                real = os.path.realpath(resp)
                root = os.path.realpath(STATE["responses_root"] or "")
                if root and not real.startswith(root):
                    return self._json({"ok": False, "error": "response outside root"}, 400)
                cols["response"] = core.render_deck(real, STATE["work_dir"])
                resp_path = real
            else:
                cols["response"] = None
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, 500)

        # Structural diff + evaluation (best-effort; falls back to pixel diff).
        struct_by_col = {"original": None, "target": None, "response": None}
        ev = None
        try:
            so = struct.parse_struct(deck["orig"], STATE["work_dir"])
            st = struct.parse_struct(target, STATE["work_dir"])
            struct_by_col["original"] = struct.struct_payload(so)
            struct_by_col["target"] = struct.struct_payload(st)
            if resp_path:
                sr = struct.parse_struct(resp_path, STATE["work_dir"])
                struct_by_col["response"] = struct.struct_payload(sr)
                ev = struct.evaluate(so, st, sr)
        except Exception as exc:
            ev = {"error": str(exc)}

        def col_out(name):
            m, s = cols[name], struct_by_col[name]
            if m is None:
                return None
            return {"images": m["images"], "psigs": m["sigs"],
                    "ssigs": s["sigs"] if s else None,
                    "elements": s["elements"] if s else None}

        n = cols["original"]["n"]
        return self._json({"ok": True, "n": n,
                           "w": cols["original"]["w"], "h": cols["original"]["h"],
                           "original": col_out("original"),
                           "target": col_out("target"),
                           "response": col_out("response"),
                           "eval": ev})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decks", required=True, help="root folder of deck subfolders")
    ap.add_argument("--responses", default=None, help="root folder of response decks")
    ap.add_argument("--out", default="compare_work", help="render cache dir")
    ap.add_argument("--port", type=int, default=8009)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    if not os.path.exists(os.path.join(STATIC_DIR, "index.html")):
        print(f"WARNING: compare_static/index.html not found (looked in {STATIC_DIR}).")
        print("         Keep the compare_static/ folder next to compare_server.py.")
    STATE.update({
        "decks": scan_decks(args.decks),
        "responses_root": args.responses,
        "work_dir": os.path.abspath(args.out),
    })
    print(f"Found {len(STATE['decks'])} decks: "
          f"{', '.join(d['id'] for d in STATE['decks'])}")
    url = f"http://localhost:{args.port}"
    print(f"Open {url}")
    import threading
    import webbrowser
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
