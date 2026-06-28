from __future__ import annotations

import csv
import os
import re
from collections import defaultdict
from pathlib import Path

CAT_DIR = Path(__file__).resolve().parent / "categories"
CAT_FILE = CAT_DIR / "cat.csv"
BRANDS_FILE = CAT_DIR / "brands.csv"


def _load_brands() -> list[str]:
    with BRANDS_FILE.open(newline="") as f:
        reader = csv.reader(f)
        next(reader)
        return [row[0] for row in reader if row and row[0]]


BRANDS = _load_brands()


def _norm(s: str) -> str:
    return re.sub(r"[\s']+", "", s).lower()


def _build_pattern(brand: str) -> re.Pattern[str]:
    parts = re.split(r"[\s']+", brand)
    pat = r"\b" + r"[\s']*".join(re.escape(p) for p in parts if p)
    if "*" in brand:
        pat += r"(?:[^a-zA-Z0-9]|$)"
    return re.compile(pat, re.IGNORECASE)


def _prep(brands: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    sorted_brands = sorted(brands, key=lambda b: -len(_norm(b)))
    return [(b, _build_pattern(b)) for b in sorted_brands]


_BRANDS = _prep(BRANDS)


def _match_brand(remark: str) -> str | None:
    for b, pat in _BRANDS:
        if pat.search(remark):
            return b
    return None


def key(remark: str) -> str:
    b = _match_brand(remark)
    if b:
        return f"BRAND:{b}"
    if remark.startswith("SEND MONEY TO ") or remark.startswith("SEND EGIFT "):
        return remark
    if remark.startswith("PAYNOW "):
        s = remark
        i = s.find("...")
        if i != -1:
            s = s[:i]
        if "-" in s:
            return s.split("-", 1)[0].strip()
        return s.strip()
    s = re.sub(r"\s+Singapore\s*$", "", remark, flags=re.IGNORECASE).strip()
    if " - " in s:
        return s.split(" - ", 1)[0].strip()
    m = re.match(r"^\d+-\S+\s+\((.+)\)$", s)
    if m:
        return f"({m.group(1)})"
    if "-" in s:
        return s.rsplit("-", 1)[0].strip()
    return s


_META = set(r".^$*+?{}[]\|()")


def _escape(s: str) -> str:
    return "".join("\\" + c if c in _META else c for c in s)


def group_regex(remarks: list[str], brand: str | None = None) -> str:
    if len(remarks) == 1:
        return _escape(remarks[0])
    lcp = os.path.commonprefix(remarks)
    lcs = os.path.commonprefix([r[::-1] for r in remarks])[::-1]
    min_len = min(len(r) for r in remarks)
    overlap = len(lcp) + len(lcs) - min_len
    if overlap > 0:
        lcs = lcs[overlap:]
    if not lcp and brand:
        prefix = ".*" + _escape(brand)
    else:
        prefix = _escape(lcp)
    parts = [prefix, ".*"]
    if lcs:
        parts.append(_escape(lcs))
    return "".join(parts)


def _try_unescape(pat: str) -> str | None:
    """Reverse _escape. Returns None if pat contains real regex syntax."""
    out = []
    i = 0
    while i < len(pat):
        c = pat[i]
        if c == "\\":
            if i + 1 >= len(pat) or pat[i + 1] not in _META:
                return None
            out.append(pat[i + 1])
            i += 2
            continue
        if c in _META:
            return None
        out.append(c)
        i += 1
    return "".join(out)


def _sample(pattern: str) -> str | None:
    s = re.sub(r"\.[*+]", "", pattern)
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            if i + 1 >= len(s) or s[i + 1] not in _META:
                return None
            out.append(s[i + 1])
            i += 2
            continue
        if c == ".":
            out.append("a")
            i += 1
            continue
        if c in _META:
            return None
        out.append(c)
        i += 1
    return "".join(out)


def _drop_subsumed(parts: list[str]) -> list[str]:
    compiled: list[re.Pattern[str] | None] = []
    samples: list[str | None] = []
    for p in parts:
        try:
            compiled.append(re.compile(p))
        except re.error:
            compiled.append(None)
        samples.append(_sample(p))

    def covers(j: int, i: int) -> bool:
        return (compiled[j] is not None and samples[i] is not None
                and compiled[j].match(samples[i]) is not None)

    kept: list[int] = []
    for i in range(len(parts)):
        redundant = False
        for j in kept:
            if parts[i] == parts[j] or covers(j, i):
                redundant = True
                break
        if redundant:
            continue
        kept = [j for j in kept if not (covers(i, j) and not covers(j, i))]
        kept.append(i)
    return [parts[i] for i in kept]


def _group_pattern(literals: list[str], regexes: list[str],
                   brand: str | None) -> str:
    if not regexes:
        return group_regex(sorted(set(literals)), brand=brand)
    parts: list[str] = []
    seen: set[str] = set()
    for r in regexes:
        if r not in seen:
            seen.add(r)
            parts.append(r)
    for lit in sorted(set(literals)):
        esc = _escape(lit)
        if esc not in seen:
            seen.add(esc)
            parts.append(esc)
    parts = _drop_subsumed(parts)
    if len(parts) == 1:
        return parts[0]
    return "(?:" + "|".join(parts) + ")"


def regroup_cat_csv() -> None:
    """Rows sharing a key merge. Regex rows additionally pull in any
    literal row whose remark they match. Output is always valid regex."""
    with CAT_FILE.open(newline="") as f:
        reader = csv.reader(f)
        next(reader)
        raw = list(reader)

    items: list[dict] = []
    for row in raw:
        if len(row) != 3:
            continue
        pattern, cat_cell, src_cell = row
        cats = {int(x) for x in cat_cell.split(",") if x.strip().isdigit()}
        srcs = {int(x) for x in src_cell.split(",") if x.strip().isdigit()}
        if not cats:
            continue
        lit = _try_unescape(pattern)
        try:
            compiled = re.compile(pattern) if lit is None else None
        except re.error:
            compiled = None
        items.append({
            "pattern": pattern,
            "literal": lit,
            "compiled": compiled,
            "key_input": lit if lit is not None else pattern,
            "cats": cats,
            "srcs": srcs,
        })

    parent = list(range(len(items)))
    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    bucket: dict[str, int] = {}
    for i, it in enumerate(items):
        k = key(it["key_input"])
        it["key"] = k
        j = bucket.get(k)
        if j is None:
            bucket[k] = i
        else:
            union(i, j)

    for i, it in enumerate(items):
        if it["compiled"] is None:
            continue
        for j, jt in enumerate(items):
            if i == j or jt["literal"] is None:
                continue
            if it["compiled"].match(jt["literal"]):
                union(i, j)

    groups: defaultdict[int, list[int]] = defaultdict(list)
    for i in range(len(items)):
        groups[find(i)].append(i)

    out: list[tuple[int, str, str, list[int], set[int]]] = []
    for members in groups.values():
        literals = [items[i]["literal"] for i in members if items[i]["literal"] is not None]
        regexes = [items[i]["pattern"] for i in members if items[i]["literal"] is None]
        cats: set[int] = set()
        srcs: set[int] = set()
        brand: str | None = None
        for i in members:
            cats |= items[i]["cats"]
            srcs |= items[i]["srcs"]
            if brand is None and items[i]["key"].startswith("BRAND:"):
                brand = items[i]["key"][len("BRAND:"):]
        pattern = _group_pattern(literals, regexes, brand)
        cat_list = sorted(cats)
        out.append((cat_list[0], pattern.lower(), pattern, cat_list, srcs))

    out.sort(key=lambda r: (r[0], r[1]))
    with CAT_FILE.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["regex", "category", "source"])
        for _, _, pattern, cat_list, srcs in out:
            w.writerow([pattern,
                        ",".join(str(c) for c in cat_list),
                        ",".join(str(s) for s in sorted(srcs))])

    print(f"regrouped {CAT_FILE}: {len(items)} rows -> {len(out)} rules")


def sort_brands() -> None:
    with BRANDS_FILE.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [row for row in reader if row and row[0]]

    rows.sort(key=lambda r: _norm(r[0]))

    with BRANDS_FILE.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)

    print(f"sorted {BRANDS_FILE}: {len(rows)} brands")


def suggest_brands(min_keys: int = 3, min_len: int = 4, top: int = 30) -> None:
    """Print uppercase tokens appearing in >=min_keys distinct key buckets,
    excluding tokens already covered by BRANDS. Adding such a token as a
    brand collapses those groups into one .*TOKEN.* rule."""
    with CAT_FILE.open(newline="") as f:
        reader = csv.reader(f)
        next(reader)
        rows = list(reader)

    remarks: list[str] = []
    for row in rows:
        if len(row) != 3:
            continue
        lit = _try_unescape(row[0])
        if lit is None:
            continue
        remarks.append(lit)

    if not remarks:
        return

    existing = {_norm(b) for b in BRANDS}
    token_keys: defaultdict[str, set[str]] = defaultdict(set)
    token_examples: defaultdict[str, set[str]] = defaultdict(set)
    for r in remarks:
        k = key(r)
        if k.startswith("BRAND:"):
            continue
        for t in re.split(r"[^A-Za-z0-9'&]+", r):
            if len(t) < min_len or t.isdigit():
                continue
            if _norm(t) in existing:
                continue
            tok = t.upper()
            token_keys[tok].add(k)
            token_examples[tok].add(r)
    cands = sorted(((len(ks), tok) for tok, ks in token_keys.items()
                    if len(ks) >= min_keys), reverse=True)
    if not cands:
        return
    print("brand candidates (distinct-keys  token  e.g.):")
    for n, tok in cands[:top]:
        ex = sorted(token_examples[tok])[0]
        print(f"  {n:>3}  {tok:<24}  {ex}")
