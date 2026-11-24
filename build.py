#!/usr/bin/env python3
"""Build Olivier Coulon's personal website into _site/.

Content lives in content/ only:
  site.yaml          identity, menu, contact details, profile links
  home.yaml          texts of the home page (research summary, themes, projects, software)
  contact.yaml       texts of the contact page
  publications.bib   the publication list (BibTeX). The publication page is generated from it, sorted by year.

Usage:
  python3 build.py            build into _site/
  python3 build.py --serve    build, then serve _site/ on http://127.0.0.1:8150

Publication entries:
  - author names "Coulon, O." / "Coulon, Olivier" are set in bold;
  - keywords = {selected} marks a selected publication (filter on the publication page);
  - keywords = {preprint} (or a bioRxiv/arXiv venue) adds a "Preprint" tag;
  - an entry that only has a DOI (e.g. @article{x, doi = {10.1016/...}}) is completed from Crossref at build time;
  - optional link fields: url, pdf, hal_id, code.
Text fields use [[...]] for placeholders still to be written (highlighted in yellow and counted at build time).
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import markdown as md_lib
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

ROOT = Path(__file__).resolve().parent
CONTENT, TEMPLATES, STATIC, OUT = ROOT / "content", ROOT / "templates", ROOT / "static", ROOT / "_site"
DOI_CACHE = ROOT / ".doi_cache.json"
PLACEHOLDERS = []
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


# --------------------------------------------------------------------------- messages
def warn(msg):
    """Print a warning; on GitHub Actions it is also shown on the summary page of the run."""
    print(f"  warning: {msg}", file=sys.stderr)
    if IN_ACTIONS:
        print(f"::warning title=Website build::{msg}")


def fail(msg):
    """Stop the build with a readable message (the online site then stays as it was)."""
    print(f"error: {msg}", file=sys.stderr)
    if IN_ACTIONS:
        print(f"::error title=Website build failed::{msg}")
    sys.exit(1)


# --------------------------------------------------------------------------- text helpers
def load_yaml(name):
    try:
        with open(CONTENT / name, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        fail(f"content/{name} is not valid YAML (check indentation and quotes): {' '.join(str(exc).split())}")


def placeholders_to_html(text):
    def rep(m):
        PLACEHOLDERS.append(m.group(1))
        return f'<span class="ph" title="To be completed">{m.group(1)}</span>'
    return re.sub(r"\[\[(.+?)\]\]", rep, str(text))


def md_block(text):
    return Markup(placeholders_to_html(md_lib.markdown(str(text or ""))))


def md_inline(text):
    out = md_lib.markdown(str(text or "")).strip()
    out = re.sub(r"^<p>(.*)</p>$", r"\1", out, flags=re.S)
    return Markup(placeholders_to_html(out))


def is_placeholder(value):
    return isinstance(value, str) and value.strip().startswith("[[")


# --------------------------------------------------------------------------- BibTeX (same parser as the BaboFet site)
ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303", "=": "\u0304", ".": "\u0307",
           "c": "\u0327", "u": "\u0306", "v": "\u030c", "H": "\u030b", "k": "\u0328"}
SPECIAL = {r"\o": "ø", r"\O": "Ø", r"\ss": "ß", r"\ae": "æ", r"\AE": "Æ", r"\oe": "œ", r"\l": "ł", r"\i": "ı"}


def latex_to_text(s):
    s = re.sub(r"\{\\([cuvHk])\s*\{?([A-Za-z])\}?\}", lambda m: m.group(2) + ACCENTS[m.group(1)], s)
    s = re.sub(r"\\([cuvHk])\s*\{([A-Za-z])\}", lambda m: m.group(2) + ACCENTS[m.group(1)], s)
    s = re.sub(r"\{?\\(['`^\"~=.])\s*\{?\\?([A-Za-z])\}?\}?", lambda m: m.group(2) + ACCENTS[m.group(1)], s)
    for k, v in SPECIAL.items():
        s = re.sub(r"\{?" + re.escape(k) + r"(?![A-Za-z])\}?", v, s)
    s = s.replace(r"\&", "&").replace(r"\_", "_").replace(r"\%", "%").replace("---", "—").replace("--", "–")
    s = s.replace("~", " ").replace("{", "").replace("}", "")
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", s).strip())


def _read_braced(s, k):
    depth, start = 0, k
    while k < len(s):
        if s[k] == "{":
            depth += 1
        elif s[k] == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1:k], k + 1
        k += 1
    raise ValueError("unbalanced braces in BibTeX entry")


def parse_bibtex(text):
    # comment lines are blanked, not removed, so that error messages give the right line number
    text = "\n".join("" if line.lstrip().startswith("%") else line for line in text.splitlines())
    entries, pos = [], 0
    for order, m in enumerate(re.finditer(r"@(\w+)\s*\{", text)):
        if m.start() < pos:
            continue
        etype = m.group(1).lower()
        line = text.count("\n", 0, m.start()) + 1
        start = text[m.start():m.start() + 60].split("\n")[0]
        try:
            body, pos = _read_braced(text, m.end() - 1)
            if etype in ("comment", "string", "preamble"):
                continue
            key, _, rest = body.partition(",")
            fields, k = {}, 0
            field_re = re.compile(r"\s*([A-Za-z_][\w-]*)\s*=\s*")
            while True:
                fm = field_re.match(rest, k)
                if not fm:
                    break
                name, k = fm.group(1).lower(), fm.end()
                if rest[k] == "{":
                    val, k = _read_braced(rest, k)
                elif rest[k] == '"':
                    end = rest.index('"', k + 1)
                    val, k = rest[k + 1:end], end + 1
                else:
                    vm = re.compile(r"[^,\s}]+").match(rest, k)
                    val, k = vm.group(0), vm.end()
                fields[name] = latex_to_text(val)
                k = re.compile(r"\s*,?").match(rest, k).end()
        except (ValueError, IndexError, AttributeError):
            fail(f"content/publications.bib, line {line}: cannot read the entry '{start}'. "
                 "Check that every { has its }, and that fields are separated by commas.")
        if not key.strip() or not re.fullmatch(r"[^\s,{}]+", key.strip()):
            fail(f"content/publications.bib, line {line}: the entry '{start}' has no valid key "
                 "(expected @article{someKey2027, ...}).")
        entries.append({"entrytype": etype, "key": key.strip(), "_order": order, "_line": line, **fields})
    return entries


# --------------------------------------------------------------------------- DOI-only entries -> Crossref
def complete_from_crossref(entries):
    """Fill title/authors/venue of entries that only give a DOI. Network errors leave the entry as is."""
    todo = [e for e in entries if e.get("doi") and not e.get("title")]
    if not todo:
        return
    cache = json.loads(DOI_CACHE.read_text()) if DOI_CACHE.exists() else {}
    for e in todo:
        doi = e["doi"].lower()
        if doi not in cache:
            req = urllib.request.Request("https://api.crossref.org/works/" + urllib.parse.quote(doi),
                                         headers={"User-Agent": "personal-website-builder/0.1"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    cache[doi] = json.loads(r.read().decode())["message"]
            except Exception as exc:
                warn(f"could not complete DOI {doi} from Crossref ({exc})")
                continue
        w = cache[doi]
        e["title"] = html.unescape(re.sub(r"<[^>]+>", "", (w.get("title") or [""])[0]))
        e["author"] = " and ".join(f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
                                   for a in w.get("author", []))
        container = (w.get("container-title") or [""])
        if w.get("type") == "journal-article":
            e["entrytype"], e["journal"] = "article", container[0]
        elif w.get("type") == "posted-content":
            e["entrytype"], e["journal"], e["keywords"] = "misc", w.get("publisher", "Preprint"), \
                (e.get("keywords", "") + " preprint").strip()
        else:
            e["entrytype"], e["booktitle"] = "inproceedings", container[-1]
        if not e.get("year"):
            for k in ("published-print", "issued", "published-online"):
                dp = (w.get(k) or {}).get("date-parts")
                if dp and dp[0] and dp[0][0]:
                    e["year"] = str(dp[0][0])
                    break
        for src, dst in (("volume", "volume"), ("issue", "number"), ("page", "pages")):
            if w.get(src) and not e.get(dst):
                e[dst] = w[src].replace("-", "–")
        print(f"  completed {e['key']} from Crossref (DOI {doi})")
    DOI_CACHE.write_text(json.dumps(cache))


# --------------------------------------------------------------------------- publication formatting
ME = ("coulon", "o")
MAX_AUTHORS = 12


def split_name(name):
    name = name.strip().strip(",").strip()
    if name.lower() == "others":
        return None, None
    if "," in name:
        last, first = [p.strip(" ,") for p in name.split(",", 1)]
    else:
        parts = name.split()
        last, first = parts[-1], " ".join(parts[:-1])
    initials = "".join(p[0] for p in re.split(r"[\s\-.,]+", first) if p and p[0].isalpha())
    return last, initials


def is_me(last, initials):
    norm = unicodedata.normalize("NFKD", last or "").encode("ascii", "ignore").decode().lower()
    return norm == ME[0] and (initials or "o").lower().startswith(ME[1])


def format_authors(raw):
    names = [split_name(a) for a in re.split(r"\s+and\s+", raw or "", flags=re.I) if a.strip()]
    etal = any(n[0] is None for n in names)
    names = [n for n in names if n[0] is not None]
    items = [(f"<b>{escape(l)} {escape(i)}</b>" if is_me(l, i) else f"{escape(l)} {escape(i)}".strip()) for l, i in names]
    if len(items) > MAX_AUTHORS:  # long consortium lists: first three, me, last
        mine = next((k for k, (l, i) in enumerate(names) if is_me(l, i)), None)
        keep = sorted({0, 1, 2, len(items) - 1} | ({mine} if mine is not None else set()))
        out, prev = [], -1
        for k in keep:
            if k - prev > 1:
                out.append("…")
            out.append(items[k])
            prev = k
        items = out
        total = f' <span class="na">({len(names)} authors)</span>'
    else:
        total = ""
    text = ", ".join(items) + (", et al" if etal else "")
    return Markup(text.replace(", …,", " …") + total)


def pub_type(e):
    kw = e.get("keywords", "").lower()
    where = (e.get("journal", "") + e.get("publisher", "") + e.get("howpublished", "")).lower()
    if "preprint" in kw or "rxiv" in where:
        return "preprint"
    return {"article": "article", "inproceedings": "conference", "conference": "conference",
            "incollection": "chapter", "inbook": "chapter", "book": "book"}.get(e["entrytype"], "other")


def format_venue(e, t):
    v = ""
    if t in ("article", "preprint", "other"):
        v = f'<i>{escape(e.get("journal") or e.get("publisher") or e.get("howpublished") or "")}</i>'
        if e.get("volume"):
            v += f' {escape(e["volume"])}' + (f'({escape(e["number"])})' if e.get("number") else "")
        elif e.get("number"):
            v += f' no. {escape(e["number"])}'
        if e.get("pages"):
            v += f':{escape(e["pages"])}' if e.get("volume") else f', {escape(e["pages"])}'
    elif t in ("conference", "chapter"):
        v = f'In <i>{escape(e.get("booktitle", ""))}</i>'
        if e.get("series") and e["series"] not in e.get("booktitle", ""):
            v += f' ({escape(e["series"])}' + (f' {escape(e["volume"])}' if e.get("volume") else "") + ")"
        elif e.get("volume"):
            v += f', vol. {escape(e["volume"])}'
        if e.get("address"):
            v += f', {escape(e["address"])}'
        if e.get("pages"):
            v += f', pp. {escape(e["pages"])}'
        if e.get("publisher") and t == "chapter":
            v += f'. {escape(e["publisher"])}'
    elif t == "book":
        v = escape(e.get("publisher", ""))
    return Markup(v)


def format_pub(e):
    t = pub_type(e)
    links = []
    if e.get("doi"):
        links.append({"label": "DOI", "url": "https://doi.org/" + e["doi"]})
    if e.get("url") and not e.get("doi"):
        links.append({"label": "Link", "url": e["url"]})
    if e.get("pdf"):
        links.append({"label": "PDF", "url": e["pdf"]})
    if e.get("hal_id"):
        links.append({"label": "HAL", "url": "https://hal.science/" + e["hal_id"]})
    if e.get("code"):
        links.append({"label": "Code", "url": e["code"]})
    title = e.get("title", "")
    title = title if re.search(r"[.?!]$", title) else title + "."
    year = (re.sub(r"\D", "", e.get("year", ""))[:4]) or "n.d."
    first = split_name(re.split(r"\s+and\s+", e.get("author", "") or "?")[0])[0] or ""
    n_auth = len([a for a in re.split(r"\s+and\s+", e.get("author", "")) if a.strip()])
    short = f'{first}{" et al." if n_auth > 2 else ""} {year}'
    search = " ".join([title, e.get("author", ""), e.get("journal", ""), e.get("booktitle", ""), year])
    return {"key": e["key"], "authors": format_authors(e.get("author", "")), "title": md_inline(title),
            "venue": format_venue(e, t), "year": year, "type": t, "links": links, "note": e.get("note", ""),
            "selected": "selected" in e.get("keywords", "").lower(), "short": short,
            "journal_short": e.get("journal") or e.get("booktitle", ""),
            "doi_url": ("https://doi.org/" + e["doi"]) if e.get("doi") else (e.get("url") or ""),
            "search": unicodedata.normalize("NFKD", search).encode("ascii", "ignore").decode().lower(),
            "_order": e["_order"]}


def load_publications():
    entries = parse_bibtex((CONTENT / "publications.bib").read_text(encoding="utf-8"))
    complete_from_crossref(entries)
    for e in [e for e in entries if not e.get("title")]:
        warn(f"publications.bib, line {e['_line']}: entry {e['key']} has no title "
             f"({'DOI not found on Crossref' if e.get('doi') else 'no title and no DOI'}); it is not shown")
        entries.remove(e)
    keys = [e["key"] for e in entries]
    dup = {k for k in keys if keys.count(k) > 1}
    if dup:
        warn("duplicate BibTeX keys in publications.bib: " + ", ".join(sorted(dup)))
    pubs = [format_pub(e) for e in entries]
    pubs.sort(key=lambda p: (-(int(p["year"]) if p["year"].isdigit() else 0), p["_order"]))
    for n, p in enumerate(pubs):
        p["number"] = len(pubs) - n
    by_year = []
    for p in pubs:
        if not by_year or by_year[-1][0] != p["year"]:
            by_year.append((p["year"], []))
        by_year[-1][1].append(p)
    return pubs, by_year


# --------------------------------------------------------------------------- build
def build():
    global PLACEHOLDERS
    PLACEHOLDERS = []
    site, home, contact = load_yaml("site.yaml"), load_yaml("home.yaml"), load_yaml("contact.yaml")
    pubs, by_year = load_publications()
    by_key = {p["key"]: p for p in pubs}
    for th in home.get("themes", []):
        th["refs"] = []
        for k in th.get("papers", []):
            if k in by_key:
                th["refs"].append(by_key[k])
            else:
                warn(f"home.yaml cites {k}, which is not in publications.bib")
    for sw in home.get("software", []):
        sw["refs"] = [by_key[k] for k in sw.get("papers", []) if k in by_key]
        for k in sw.get("papers", []):
            if k not in by_key:
                warn(f"home.yaml cites {k}, which is not in publications.bib")

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(STATIC, OUT)
    shutil.copy(CONTENT / "publications.bib", OUT / "publications.bib")
    (OUT / ".nojekyll").write_text("")

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["md"] = md_block
    env.filters["inline"] = md_inline
    env.tests["placeholder"] = is_placeholder
    today = dt.date.today()
    common = {"site": site, "build_date": today.strftime("%-d %B %Y"), "year_now": today.year}

    def render(page, template, title=None, **ctx):
        html_out = env.get_template(template).render(page=page, page_title=title, **common, **ctx)
        (OUT / f"{page}.html").write_text(html_out, encoding="utf-8")

    render("index", "index.html", c=home, recent=pubs[:home.get("recent_count", 5)], n_pubs=len(pubs))
    years = [y for y, _ in by_year]
    render("publications", "publications.html", "Publications", pubs=pubs, by_year=by_year, years=years,
           n_selected=sum(p["selected"] for p in pubs))
    render("contact", "contact.html", "Contact", c=contact)
    print(f"Built {OUT.relative_to(ROOT)}/: {len(pubs)} publications ({years[-1]}–{years[0]}), "
          f"{sum(p['selected'] for p in pubs)} selected.")
    if PLACEHOLDERS:
        print(f"{len(PLACEHOLDERS)} placeholder(s) still to complete:")
        for p in dict.fromkeys(PLACEHOLDERS):
            print("  -", p)
        if IN_ACTIONS:
            warn(f"{len(PLACEHOLDERS)} [[...]] placeholder(s) are still shown in yellow on the site")


def serve(port=8150):
    import functools
    import http.server
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
    print(f"Serving on http://127.0.0.1:{port}/")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--serve", action="store_true", help="serve _site/ after building")
    args = ap.parse_args()
    build()
    if args.serve:
        serve()
