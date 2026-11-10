"""CLI: python -m modules.xcapture <url|id> ... --out <folder>  (also reads one URL per line from --file)."""
from __future__ import annotations

import argparse
import html
import os
import sys

from modules.xcapture.capture import render


def galeria(items: list[dict], out_dir: str) -> str:
    cards = "".join(
        f'<figure><img src="{html.escape(os.path.basename(i["png"]))}"><figcaption>@{i["user"]} · ♥ {i["likes"]:,}'
        f'<br><small>{html.escape(os.path.basename(i["png"]))}</small></figcaption></figure>' for i in items)
    page = ("<!doctype html><meta charset=utf-8><title>Capturas de X</title><style>body{background:#111;color:#ddd;"
            "font-family:Segoe UI,Arial;margin:20px}.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));"
            "gap:18px}figure{margin:0;background:#1b1b1b;border-radius:10px;padding:12px}img{width:100%;border-radius:8px}"
            "figcaption{margin-top:8px;font-size:14px}small{color:#888}</style>"
            f"<h2>Capturas de X ({len(items)})</h2><div class=g>{cards}</div>")
    path = os.path.join(out_dir, "galeria.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("refs", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    refs = list(a.refs)
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            refs += [l.strip() for l in f if l.strip() and not l.startswith("#")]
    done = []
    for r in refs:
        try:
            it = render(r, a.out)
            done.append(it)
            print(f"OK  {it['png']}")
        except Exception as e:
            print(f"ERR {r}: {e}")
    if done:
        print("galería:", galeria(done, a.out))
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
