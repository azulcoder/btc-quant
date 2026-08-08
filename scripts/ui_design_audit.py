"""ui_design_audit.py — make "ugly" measurable for the orderflow terminal.

Read-only. This script never edits a dashboard file; it counts what is already there.

Taste is not measurable and is not measured here. What IS measurable is CONSISTENCY —
how many distinct colours, type sizes, spacing values, weights and radii a surface uses —
plus three defects that are functional rather than aesthetic:

* numbers that shift horizontally between frames because the font has proportional digits
  (`font-variant-numeric: tabular-nums` absent), which in a price grid means columns that
  visibly jitter on every tick;
* foreground/background pairs below the WCAG contrast floor;
* red/green used as the ONLY channel distinguishing bid from ask or up from down, which
  roughly 8 % of men cannot decode.

Benchmarks are quoted as [DIASUMSIKAN] and never as law: a healthy design system runs about
8-16 semantic colours, 5-7 type steps, 6-8 spacing steps and 2-3 font weights. Those are
conventions, not physics, and the report says so wherever it compares against them.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASH = REPO / "dashboard"

HEX = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
RGB = re.compile(r"rgba?\(\s*[\d.]+%?\s*[, ]\s*[\d.]+%?\s*[, ]\s*[\d.]+%?\s*(?:[,/]\s*[\d.%]+\s*)?\)")
HSL = re.compile(r"hsla?\(\s*[\d.]+(?:deg)?\s*[, ]\s*[\d.]+%\s*[, ]\s*[\d.]+%\s*(?:[,/]\s*[\d.%]+\s*)?\)")
FONT_SIZE = re.compile(r"font-size\s*:\s*([^;}\n]+)")
FONT_WEIGHT = re.compile(r"font-weight\s*:\s*([^;}\n]+)")
FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;}\n]+)")
RADIUS = re.compile(r"border-radius\s*:\s*([^;}\n]+)")
SPACING = re.compile(r"(?:padding|margin|gap|row-gap|column-gap)(?:-(?:top|right|bottom|left))?\s*:\s*([^;}\n]+)")


def files() -> list[Path]:
    return sorted(p for p in DASH.rglob("*")
                  if p.is_file() and p.suffix in (".css", ".js", ".html")
                  and "vendor" not in p.parts)


def norm_colour(tok: str) -> str:
    t = tok.strip().lower()
    if t.startswith("#") and len(t) == 4:                 # #abc -> #aabbcc
        t = "#" + "".join(c * 2 for c in t[1:])
    return re.sub(r"\s+", "", t)


def split_spacing(val: str) -> list[str]:
    """`padding: 4px 8px` is TWO distinct spacing decisions, not one."""
    v = val.strip().lower()
    if v.startswith("var(") or "calc(" in v:
        return [re.sub(r"\s+", " ", v)]
    return [p for p in re.split(r"\s+", v) if p and p not in ("auto", "0")]


def collect() -> dict:
    buckets = {k: collections.Counter() for k in
               ("colour", "font_size", "spacing", "font_weight", "radius", "font_family")}
    where = collections.defaultdict(collections.Counter)
    per_file = collections.Counter()
    for p in files():
        txt = p.read_text(encoding="utf-8", errors="ignore")
        rel = str(p.relative_to(REPO))
        found = []
        for rx in (HEX, RGB, HSL):
            for m in rx.findall(txt):
                found.append(("colour", norm_colour(m)))
        for rx, key in ((FONT_SIZE, "font_size"), (FONT_WEIGHT, "font_weight"),
                        (RADIUS, "radius"), (FONT_FAMILY, "font_family")):
            for m in rx.findall(txt):
                found.append((key, re.sub(r"\s+", " ", m.strip().lower())))
        for m in SPACING.findall(txt):
            for one in split_spacing(m):
                found.append(("spacing", one))
        for key, val in found:
            buckets[key][val] += 1
            where[key][rel] += 1
        per_file[rel] = len(found)
    return {"buckets": buckets, "where": where, "per_file": per_file}


# --------------------------------------------------------------------------- #
# contrast                                                                     #
# --------------------------------------------------------------------------- #
def to_rgb(tok: str):
    t = tok.strip().lower()
    if t.startswith("#"):
        h = t[1:]
        if len(h) in (6, 8):
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        return None
    m = re.match(r"rgba?\(\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*[, ]\s*([\d.]+)", t)
    if m:
        return tuple(min(255, int(float(x))) for x in m.groups())
    return None


def rel_lum(rgb):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = rel_lum(a), rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def css_vars() -> dict[str, str]:
    """Resolve `--name: value` declarations, following var() chains.

    Without this the contrast scan is blind: this codebase declares its palette as custom
    properties and almost never writes a literal colour next to a literal background, so a
    literal-only scan reports ZERO failures and that zero means "nothing looked", not
    "nothing wrong" (class B).
    """
    raw: dict[str, str] = {}
    for p in files():
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;}\n]+)", txt):
            raw.setdefault(m.group(1), m.group(2).strip())
    for _ in range(6):                                    # follow var() chains
        for k, v in list(raw.items()):
            m = re.fullmatch(r"var\((--[\w-]+)\)", v.strip())
            if m and m.group(1) in raw:
                raw[k] = raw[m.group(1)]
    return raw


def resolve(val: str, vars_: dict[str, str]) -> str:
    v = val.strip()
    for _ in range(6):
        m = re.search(r"var\((--[\w-]+)(?:\s*,[^)]*)?\)", v)
        if not m:
            break
        v = v[:m.start()] + vars_.get(m.group(1), "") + v[m.end():]
    return v.strip()


def contrast_pairs() -> list[dict]:
    """Pairs actually used together: a `color` and a `background` in the same CSS rule,
    with custom properties resolved first. Only real pairs are scored — enumerating every
    combination would manufacture failures no user can see."""
    out = []
    vars_ = css_vars()
    for p in files():
        if p.suffix not in (".css", ".html"):
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", txt):
            sel, body = m.group(1).strip(), m.group(2)
            fg = re.search(r"(?<!-)\bcolor\s*:\s*([^;}\n]+)", body)
            bg = re.search(r"background(?:-color)?\s*:\s*([^;}\n]+)", body)
            if not (fg and bg):
                continue
            a, b = to_rgb(resolve(fg.group(1), vars_)), to_rgb(resolve(bg.group(1), vars_))
            if not (a and b):
                continue
            fs = re.search(r"font-size\s*:\s*([\d.]+)px", body)
            fw = re.search(r"font-weight\s*:\s*(\d+)", body)
            px = float(fs.group(1)) if fs else None
            large = bool(px and (px >= 24 or (px >= 18.66 and fw and int(fw.group(1)) >= 700)))
            r = contrast(a, b)
            floor = 3.0 if large else 4.5
            if r < floor:
                out.append({"file": str(p.relative_to(REPO)),
                            "selector": re.sub(r"\s+", " ", sel)[:90],
                            "fg": fg.group(1).strip(), "bg": bg.group(1).strip(),
                            "ratio": round(r, 2), "floor": floor,
                            "font_px": px, "large": large})
    return sorted(out, key=lambda d: d["ratio"])


def tabular_numerals() -> dict:
    hits = collections.Counter()
    for p in files():
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for pat in ("tabular-nums", "font-variant-numeric", "font-feature-settings",
                    '"tnum"', "'tnum'"):
            n = txt.count(pat)
            if n:
                hits[f"{p.relative_to(REPO)}::{pat}"] = n
    return dict(hits)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    data = collect()
    b = data["buckets"]

    BENCH = {"colour": (8, 16, "semantic colours"), "font_size": (5, 7, "type steps"),
             "spacing": (6, 8, "spacing steps"), "font_weight": (2, 3, "font weights"),
             "radius": (2, 4, "radii"), "font_family": (1, 3, "families")}
    print(f"=== dashboard design inventory · {len(files())} files "
          f"(vendor/ excluded) ===\n")
    summary = {}
    for key, (lo, hi, label) in BENCH.items():
        c = b[key]
        n = len(c)
        mult = n / hi if hi else 0
        summary[key] = {"distinct": n, "total_uses": sum(c.values()), "bench_hi": hi}
        print(f"{key:<12} distinct={n:<5} uses={sum(c.values()):<6} "
              f"benchmark [DIASUMSIKAN] {lo}-{hi} {label} -> {mult:.0f}x the upper bound"
              if n > hi else
              f"{key:<12} distinct={n:<5} uses={sum(c.values()):<6} "
              f"benchmark [DIASUMSIKAN] {lo}-{hi} {label} -> within")
        for val, cnt in c.most_common(10):
            print(f"    {cnt:>6}  {val[:70]}")
        print()

    print("=== tabular numerals (item 1c) ===")
    tn = tabular_numerals()
    print(f"  occurrences of tabular-nums / font-variant-numeric / tnum: "
          f"{sum(tn.values())}")
    for k, v in tn.items():
        print(f"    {v:>4}  {k}")
    if not tn:
        print("    NONE FOUND — every numeric readout uses proportional digits")
    print()

    print("=== WCAG contrast failures on REAL fg/bg pairs (item 1d) ===")
    cp = contrast_pairs()
    print(f"  pairs scored from same-rule color+background; failures: {len(cp)}")
    for d in cp[:15]:
        print(f"    {d['ratio']:>5}:1 (floor {d['floor']}) {d['fg']} on {d['bg']}  "
              f"{d['file']} :: {d['selector'][:60]}")
    print()

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"summary": summary,
             "top": {k: b[k].most_common(25) for k in b},
             "tabular": tn, "contrast_failures": cp,
             "per_file": data["per_file"].most_common()}, indent=1))
        print(f"-> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
