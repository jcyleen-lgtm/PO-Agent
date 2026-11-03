"""Compare two regression runs: text, API calls, layout, CSV downloads (exact) and screenshots (pixels).

    python tests/e2e/compare.py <before_dir> <after_dir> [--tolerance 0.0]

Exit code 1 when anything differs beyond the tolerance (share of changed pixels per screenshot, in %).
"""

import argparse
import glob
import json
import os
import sys

from PIL import Image, ImageChops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--tolerance", type=float, default=0.0)
    a = ap.parse_args()
    bad = 0
    A = {s["id"]: s for s in json.load(open(os.path.join(a.before, "states.json")))}
    B = {s["id"]: s for s in json.load(open(os.path.join(a.after, "states.json")))}
    if set(A) != set(B):
        print("checkpoints differ:", sorted(set(A) ^ set(B)))
        bad += 1
    for k in sorted(set(A) & set(B)):
        sa, sb = A[k].get("styles"), B[k].get("styles")
        if sa is not None and sb is not None and sa != sb:   # computed styles are captured on desktop-light only
            bad += 1
            d = [(x, y) for x, y in zip(sa, sb or []) if x != y]
            print(f"DIFF {k} computed styles: {len(d)} element(s){' + element count ' + str(len(sa)) + '->' + str(len(sb or [])) if len(sa) != len(sb or []) else ''}")
            for x, y in d[:5]:
                print(f"   {x[:110]}\n   -> {y[:110]}")
        for f in ("text", "calls", "theme", "activeNav", "scrollWidth", "canvases"):
            if A[k][f] != B[k][f]:
                bad += 1
                print(f"DIFF {k} {f}")
                if f == "text":
                    la, lb = A[k][f].split("\n"), B[k][f].split("\n")
                    for i, (x, y) in enumerate(zip(la, lb)):
                        if x != y:
                            print(f"   line {i}: {x[:120]!r}\n        -> {y[:120]!r}")
                            break
                else:
                    print(f"   {A[k][f]!r}\n   -> {B[k][f]!r}")
    for fa in sorted(glob.glob(os.path.join(a.before, "*.csv"))):
        fb = os.path.join(a.after, os.path.basename(fa))
        if not os.path.exists(fb) or open(fa, "rb").read() != open(fb, "rb").read():
            bad += 1
            print("DIFF csv", os.path.basename(fa))
    ea, eb = json.load(open(os.path.join(a.before, "errors.json"))), json.load(open(os.path.join(a.after, "errors.json")))
    if eb != ea:
        bad += 1
        print("errors before:", ea, "\nerrors after:", eb)
    shots = 0
    for pa in sorted(glob.glob(os.path.join(a.before, "*.png"))):
        pb = os.path.join(a.after, os.path.basename(pa))
        shots += 1
        if not os.path.exists(pb):
            bad += 1
            print("missing screenshot", os.path.basename(pa))
            continue
        ia, ib = Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB")
        if ia.size != ib.size:
            bad += 1
            print(f"DIFF size {os.path.basename(pa)} {ia.size} -> {ib.size}")
            continue
        diff = ImageChops.difference(ia, ib).convert("L").point(lambda v: 255 if v > 0 else 0)
        share = 100 * sum(diff.histogram()[255:]) / (ia.size[0] * ia.size[1])
        if share > a.tolerance:
            bad += 1
            print(f"DIFF pixels {os.path.basename(pa)} {share:.3f}% bbox={diff.getbbox()}")
    print(f"{len(A)} checkpoints, {shots} screenshots compared: {'IDENTICAL' if not bad else f'{bad} difference(s)'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
