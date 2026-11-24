#!/usr/bin/env python3
"""One-time import of the publication list of the old Google Sites page into content/publications.bib.

Input : tools/oldsite_publications.txt (text of https://sites.google.com/site/oliviercoulon/publications)
        Only the sections "Journals, book chapters" and "Conferences with peer reviews" are imported;
        "Conference abstracts" are skipped.
Method: each citation is split heuristically (authors / year / rest / DOI). Entries with a DOI are completed
        from the Crossref REST API (works/{doi}); entries without one are looked up with a Crossref
        bibliographic query on the title+venue part only (no author names sent), and accepted only when
        the returned title matches the local one closely (difflib ratio >= 0.88) and the year is within 1.
        Everything else is written from the heuristic split and marked "% CHECK".
Output: content/publications.bib (overwritten) + a short report on stdout.

Usage: python3 tools/import_oldsite.py [--offline] [--force]
"""
import difflib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "oldsite_publications.txt"
OUT = ROOT / "content" / "publications.bib"
CACHE = ROOT / "tools" / ".crossref_cache.json"
UA = "personal-website-builder/0.1 (bibliography import script)"

# Equal-contribution notes present in the old list (Crossref does not carry them).
NOTES = {
    "longitudinal mri template of the baboon brain": "* co-last authors",
    "dynamic reconfiguration of visuomotor": "* equal contribution",
    "marsatlas": "* equal contribution",
    "central sulcus depth and sulcal profile differences": "* equal senior author contribution",
    "early childhood development of the central sulcus": "* equal senior author contribution",
}
# Manual fixes of lines the heuristics cannot read.
FIXES = {
    "Nisan A, Boï J-M, Coulon O (2009). Fertil B, Cheaib N, Otmane S, ":
        "Nisan A, Boï J-M, Fertil B, Cheaib N, Otmane S, Coulon O (2009). ",
    "Frouin V 52017).": "Frouin V (2017).",
    "Arridge SR (200).": "Arridge SR (2000).",
}


# Clean data for the lines the heuristics cannot split. Keyed by the start of the (normalised) guessed title.
# "search": try a Crossref bibliographic query on title + venue (no author names) to get the DOI/pages.
ISBI = "IEEE International Symposium on Biomedical Imaging (ISBI)"
EMBC = "Annual International Conference of the IEEE Engineering in Medicine and Biology Society (EMBC)"
MANUAL = {
    "an automated pipeline for processing and analysis of clinical fetal": dict(
        type="inproceedings", title="An automated pipeline for processing and analysis of clinical fetal diffusion MRI",
        booktitle=ISBI, address="London, UK", search=True,
        author=["Cazzolla, M.", "Manchon, A.", "Milh, M.", "Girard, N.", "Coulon, O.", "Rousseau, F.", "Auzias, G."]),
    "comprendre le plissement cortical": dict(
        type="article", title="Comprendre le plissement cortical : la piste développementale",
        journal="La Lettre des Neurosciences", number="69", author=["Coulon, O.", "Lefèvre, J.", "Manchon, A.", "Girard, N."]),
    "genetic determinants of individual variation": dict(type="article", journal="Cerebral Cortex", search=True),
    "automatic detection of plis de passage": dict(type="inproceedings", booktitle=ISBI, search=True),
    "dense and structured representations of u shape": dict(type="inproceedings", booktitle=ISBI, address="Washington, DC, USA", search=True),
    "sparse description of the cortical": dict(type="inproceedings", booktitle=ISBI, address="Washington, DC, USA", search=True,
        title="Sparse description of the cortical surface pediatric development: a sulcal pits study"),
    "central sulcus depth and sulcal profile": dict(type="inproceedings", booktitle=EMBC, search=True),
    "early childhood development of the central sulcus": dict(type="inproceedings", booktitle=EMBC, search=True),
    "isa an inverse surface based": dict(type="inproceedings", booktitle=ISBI, address="Melbourne, Australia", search=True),
    "a five years longitudinal magnetic resonance": dict(
        type="inproceedings", title="A five years longitudinal magnetic resonance imaging study of severe traumatic brain "
        "injury patients. Correlation with functional outcome", booktitle="ESICM LIVE 2016, Intensive Care Medicine Experimental",
        volume="4", note="Suppl. 1"),
    "a new human specific brain landmark": dict(type="article", title="A new human-specific brain landmark: the depth "
        "asymmetry of superior temporal sulcus", journal="Proceedings of the National Academy of Sciences of the USA",
        volume="112", number="4", pages="1208--1213", search=True),
    "sulci as landmarks": dict(type="incollection", booktitle="Brain Mapping: An Encyclopedic Reference",
                               publisher="Elsevier", search=True),
    "evolution of the central sulcus morphology": dict(type="article", journal="Brain, Behavior and Evolution",
                                                       volume="84", pages="19--30", search=True),
    "model driven harmonic parameterization of the cortical surface hip hop": dict(
        type="article", journal="IEEE Transactions on Medical Imaging", volume="32", number="5", pages="873--887", search=True),
    "structural analysis of fmri data": dict(type="article", title="Structural analysis of fMRI data: a surface-based "
        "framework for multi-subject studies", journal="Medical Image Analysis", volume="16", pages="976--990", search=True),
    "fast surface based measurements": dict(type="inproceedings", booktitle=ISBI, address="Barcelona, Spain",
                                            pages="1527--1530", search=True),
    "structural group analysis of cortical curvature": dict(type="inproceedings", booktitle=ISBI, address="Barcelona, Spain",
                                                            pages="422--425", search=True),
    "two new stable anatomical landmarks": dict(type="inproceedings", title="Two new stable anatomical landmarks on the "
        "central sulcus: definition, automatic detection, and their relationship with primary motor functions of the hand",
        booktitle=EMBC, address="Boston, USA", pages="7795--7798", search=True),
    "genetics of primary cerebral gyrification": dict(type="article", title="Genetics of primary cerebral gyrification: "
        "heritability of length, depth and area of primary sulci in an extended pedigree of Papio baboons",
        journal="NeuroImage", volume="53", number="3", pages="1126--1134", search=True),
    "structural group analysis of brain functional data": dict(type="inproceedings", booktitle="Medical Image Computing "
        "and Computer-Assisted Intervention (MICCAI)", title="Structural group analysis of brain functional data: "
        "assessing results significance", search=True),
    "une proposition de modelisation de terrains": dict(type="inproceedings", booktitle="22es Journées de l'Association "
        "Francophone d'Informatique Graphique (AFIG)", address="Arles, France"),
    "the central sulcus an observer independent": dict(type="article", journal="Cerebral Cortex", volume="18", number="9",
                                                       pages="1999--2009", search=True),
    "automatic segmentation of hip bone surface": dict(type="inproceedings", booktitle="Computer Assisted Radiology and "
        "Surgery (CARS), International Journal of Computer Assisted Radiology and Surgery", volume="1",
        pages="115--116", address="Osaka, Japan", note="Suppl. 1"),
    "diffusion tensor magnetic resonance image regularisation": dict(type="article", journal="Medical Image Analysis",
        volume="8", number="1", pages="47--67", search=True),
    "regularisation d": dict(type="inproceedings", title="Régularisation d'images de tenseurs de "
        "diffusion par résonance magnétique", booktitle="Reconnaissance des Formes et Intelligence Artificielle (RFIA)",
        address="Angers, France"),
    "tensor field regularisation for dt mr": dict(type="inproceedings", booktitle="Medical Image Understanding and "
        "Analysis (MIUA)", address="Birmingham, UK"),
    "a regularization scheme for diffusion tensor": dict(type="inproceedings", booktitle="Information Processing in "
        "Medical Imaging (IPMI), Lecture Notes in Computer Science", volume="2082", pages="92--105", search=True),
    "dual echo mr image processing": dict(type="inproceedings", title="Dual echo MR image processing using multi-spectral "
        "probabilistic diffusion coupled with shock filters", booktitle="Medical Image Understanding and Analysis (MIUA)"),
    "une description multi echelles": dict(type="article", title="Une description multi-échelles de la structure des "
        "images de différence TEP / Multiscale structure description of PET difference images",
        journal="Traitement du Signal", volume="15", number="5", pages="381--393"),
    "place de l": dict(type="article", journal="Annales de l'Institut Pasteur",
        volume="9", number="3", pages="243--258"),
}


def manual_for(title):
    t = norm(title)
    return next((v for k, v in MANUAL.items() if t.startswith(k)), None)


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def split_line(line):
    for bad, good in FIXES.items():
        line = line.replace(bad, good)
    m = re.search(r"\(\s*(\d{4})[a-z]?\s*\)\s*[.,]?\s*", line)
    if not m:
        return None
    authors, year, rest = line[:m.start()].strip(" ,"), m.group(1), line[m.end():].strip()
    doi = None
    dm = re.search(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)(10\.\d{4,9}/[^\s,]+)", rest, flags=re.I)
    if dm:
        doi = dm.group(1).rstrip(".")
        rest = (rest[:dm.start()] + rest[dm.end():])
    rest = re.sub(r",?\s*\burl\b\s*,?", ",", rest)
    rest = re.sub(r"\(\s*\*[^)]*\)", "", rest)
    rest = re.sub(r"[\s,.]+$", "", rest).strip()
    return {"authors_raw": authors, "year": year, "rest": rest, "doi": doi, "raw": line}


def guess_title(rest):
    """Title = text up to the first '. ' or '? ' (APA style), else up to the first ', ' (old comma style)."""
    m = re.search(r"(?<=[a-z0-9)\]])[.?]\s+(?=[A-Z])", rest)
    if m and m.start() > 15:
        title, venue = rest[:m.start() + (1 if rest[m.start()] == "?" else 0)], rest[m.end():]
    else:
        title, _, venue = rest.partition(", ")
    return title.strip(), venue.strip(" .,")


def parse_authors(raw):
    """Return a list of 'Last, Initials' from the three styles of the old list."""
    raw = raw.replace("…", ",").replace("&", ",").replace(" and ", ", ")
    raw = re.sub(r"\*", "", raw)
    out = []
    # APA style "Meunier, D., Loh, K.K., …" -> pairs
    if re.search(r"[A-Za-zÀ-ÿ'\- ]+,\s*(?:[A-Z]\.\s*-?)+\s*(,|$)", raw) and re.search(r",\s*[A-Z]\.", raw):
        for last, ini in re.findall(r"([^,]+?),\s*((?:[A-ZÀ-Ý][a-z]?\.\s*-?\s*)+)", raw):
            out.append(f"{last.strip()}, {ini.strip()}")
        return out
    for tok in [t.strip() for t in raw.split(",") if t.strip()]:
        parts = tok.split()
        if len(parts) >= 2 and re.fullmatch(r"(?:[A-Z]{1,3}|[A-Z]-[A-Z])\.?", parts[-1].replace(".", "")[:4]) \
                and parts[-1].replace(".", "").replace("-", "").isupper():
            out.append(f"{' '.join(parts[:-1])}, {parts[-1]}")        # "Auzias G" / "Mangin J-F"
        elif len(parts) >= 2:
            out.append(f"{parts[-1]}, {' '.join(parts[:-1])}")       # "Niharika Gajawelli"
        else:
            out.append(tok)
    return out


def load_cache():
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def http_json(url, cache):
    if url in cache:
        return cache[url]
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except Exception as exc:  # network error or 404: remember it, keep going
        data = {"error": str(exc)}
    cache[url] = data
    time.sleep(0.15)
    return data


def crossref_by_doi(doi, cache):
    d = http_json("https://api.crossref.org/works/" + urllib.parse.quote(doi), cache)
    return d.get("message") if "message" in d else None


def crossref_search(text, cache):
    q = urllib.parse.urlencode({"query.bibliographic": text[:300], "rows": 3,
                                "select": "DOI,title,author,container-title,issued,volume,issue,page,type,publisher,event"})
    d = http_json("https://api.crossref.org/works?" + q, cache)
    return (d.get("message") or {}).get("items", []) if "message" in d else []


def cr_year(w):
    for k in ("published-print", "issued", "published-online", "published"):
        dp = (w.get(k) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            return str(dp[0][0])
    return ""


def cr_authors(w):
    out = []
    for a in w.get("author", []):
        if a.get("family"):
            out.append(f"{a['family']}, {a.get('given', '')}".strip(", "))
        elif a.get("name"):
            out.append("{" + a["name"] + "}")
    return out


def bib_escape(s):
    return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")


def make_key(authors, year, title, used):
    last = norm(authors[0].split(",")[0]).replace(" ", "") if authors else "anon"
    word = next((w for w in norm(title).split() if len(w) > 3 and w not in
                 {"with", "from", "using", "based", "into", "their", "between", "towards", "toward"}), "x")
    key, n = f"{last}{year}{word}", 1
    while key in used:
        n += 1
        key = f"{last}{year}{word}{chr(96 + n)}"
    used.add(key)
    return key


def entry_from_crossref(w, base):
    t = w.get("type", "")
    etype = {"journal-article": "article", "proceedings-article": "inproceedings", "book-chapter": "incollection",
             "posted-content": "misc", "book": "book", "reference-entry": "incollection"}.get(t, "misc")
    containers = [html.unescape(c) for c in (w.get("container-title") or [""])]
    container = containers[-1] if etype in ("incollection", "book") else containers[0]
    title = html.unescape(html.unescape(re.sub(r"<[^>]+>", "", (w.get("title") or [base["title"]])[0])))  # some titles are escaped twice
    # year: keep the one of Olivier's list (Crossref's print year is often one year later than online)
    f = {"author": cr_authors(w) or base["authors"], "title": re.sub(r"\s+", " ", title).strip(),
         "year": base["year"] or cr_year(w), "doi": w.get("DOI", "").lower()}
    if etype in ("incollection", "book") and len(containers) > 1:
        f["series"] = containers[0]
    if etype == "article":
        f["journal"] = container or base["venue"]
    elif etype in ("inproceedings", "incollection"):
        f["booktitle"] = container or base["venue"]
    if t == "posted-content":
        f["publisher"] = w.get("publisher") or base["venue"]
        f["keywords"] = "preprint"
        if "biorxiv" in (base["venue"] + str(w.get("institution", ""))).lower() or w.get("DOI", "").startswith("10.1101"):
            f["journal"] = "bioRxiv"
    for k_src, k_dst in (("volume", "volume"), ("issue", "number"), ("page", "pages")):
        if w.get(k_src):
            f[k_dst] = w[k_src].replace("-", "--") if k_dst == "pages" else w[k_src]
    if etype in ("incollection", "book") and w.get("publisher"):
        f["publisher"] = w["publisher"]
    return etype, f


def entry_from_heuristics(base, section):
    venue = base["venue"]
    if base.get("preprint"):
        return "misc", {"author": base["authors"], "title": base["title"], "year": base["year"],
                        "journal": venue, "keywords": "preprint"}
    if section == "conf":
        return "inproceedings", {"author": base["authors"], "title": base["title"], "year": base["year"], "booktitle": venue}
    # journal style "Journal, 61:941-949" or "Journal 33, p. 127-133"
    f = {"author": base["authors"], "title": base["title"], "year": base["year"]}
    m = re.match(r"(.+?)[, ]+(?:vol\.\s*)?(\d+)\s*(?:\((\d+)\))?\s*[:,]\s*(?:pp?\.\s*)?(\d+\s*[-–]\s*\d+)", venue)
    if m:
        f.update(journal=m.group(1).strip(" ,"), volume=m.group(2), pages=re.sub(r"\s*[-–]\s*", "--", m.group(4)))
        if m.group(3):
            f["number"] = m.group(3)
    else:
        f["journal"] = venue
    return "article", f


def to_bib(etype, key, f, comment=None):
    lines = []
    if comment:
        lines.append(f"% {comment}")
    lines.append(f"@{etype}{{{key},")
    order = ["author", "title", "journal", "booktitle", "series", "publisher", "address", "year", "volume", "number", "pages",
             "doi", "note", "keywords"]
    for k in order:
        v = f.get(k)
        if not v:
            continue
        if k == "author":
            v = " and ".join(v)
        lines.append(f"  {k:<9} = {{{bib_escape(v) if k not in ('doi',) else v}}},")
    lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    return "\n".join(lines)


def main():
    offline = "--offline" in sys.argv
    if OUT.exists() and "--force" not in sys.argv:
        sys.exit(f"{OUT} exists and may have been edited by hand; rerun with --force to overwrite it.")
    text = SRC.read_text(encoding="utf-8").splitlines()
    i_j = text.index("Journals, book chapters")
    i_c = text.index("Conferences with peer reviews")
    i_a = text.index("Conference abstracts")
    items = [(l, "journal") for l in text[i_j + 1:i_c]] + [(l, "conf") for l in text[i_c + 1:i_a]]
    cache = load_cache()
    used, out, report = set(), [], {"crossref-doi": 0, "crossref-search": 0, "heuristic": 0, "skipped": []}
    for line, section in items:
        if not line.strip():
            continue
        p = split_line(line)
        if not p:
            report["skipped"].append(line[:90])
            continue
        title, venue = guess_title(p["rest"])
        base = {"authors": parse_authors(p["authors_raw"]), "year": p["year"], "title": title, "venue": venue,
                "preprint": "rxiv" in p["rest"].lower()}
        etype = f = None
        comment = None
        if p["doi"] and not offline:
            w = crossref_by_doi(p["doi"], cache)
            if w and abs(int(cr_year(w) or p["year"]) - int(p["year"])) <= 1:
                etype, f = entry_from_crossref(w, base)
                report["crossref-doi"] += 1
        if f is None and not p["doi"] and not offline and not manual_for(title):
            for w in crossref_search(p["rest"], cache):
                ct = html.unescape(re.sub(r"<[^>]+>", "", (w.get("title") or [""])[0]))
                ratio = difflib.SequenceMatcher(None, norm(ct), norm(title)).ratio()
                y = cr_year(w)
                if ratio >= 0.88 and y and abs(int(y) - int(p["year"])) <= 1:
                    etype, f = entry_from_crossref(w, base)
                    report["crossref-search"] += 1
                    comment = "DOI found by Crossref title search (title match %.2f)" % ratio
                    break
        man = manual_for(title) if f is None else None
        if man and p["doi"]:
            p["doi"] = None  # DOI of the old list did not resolve: search again
        if man:
            base_m = dict(base, title=man.get("title", title))
            if man.get("author"):
                base_m["authors"] = man["author"]
            if man.get("search") and not offline:
                venue_hint = man.get("journal") or man.get("booktitle") or ""
                for w in crossref_search(base_m["title"] + " " + venue_hint, cache):
                    ct = html.unescape(re.sub(r"<[^>]+>", "", (w.get("title") or [""])[0]))
                    ratio = difflib.SequenceMatcher(None, norm(ct), norm(base_m["title"])).ratio()
                    y = cr_year(w)
                    if ratio >= 0.85 and y and abs(int(y) - int(p["year"])) <= 1:
                        etype, f = entry_from_crossref(w, base_m)
                        report["crossref-search"] += 1
                        comment = "DOI found by Crossref title search on the cleaned title (match %.2f)" % ratio
                        break
            if f is None:
                etype = man["type"]
                f = {"author": base_m["authors"], "title": base_m["title"], "year": p["year"]}
                f.update({k: v for k, v in man.items() if k not in ("type", "search", "author", "title")})
                report["manual"] = report.get("manual", 0) + 1
                comment = "Cleaned by hand from the old site text (no DOI found)"
        if f is None:
            etype, f = entry_from_heuristics(base, section)
            if p["doi"]:
                f["doi"] = p["doi"].lower()
            report["heuristic"] += 1
            comment = "CHECK: parsed from the old site text, not verified against Crossref"
        for k, note in NOTES.items():
            if k in norm(f["title"]):
                f["note"] = note
        if any(norm(a).startswith("coulon") for a in f["author"]) is False:
            f["author"] = base["authors"]  # Crossref author list incomplete: keep the old one
        f["_key"] = make_key(f["author"], f["year"], f["title"], used)
        out.append((int(f["year"]), etype, f, comment))
    CACHE.write_text(json.dumps(cache))
    out.sort(key=lambda r: -r[0])
    header = ("% Publications of Olivier Coulon — the website's publication page is generated from this file.\n"
              "% To add a publication: paste a BibTeX entry anywhere in this file (order does not matter, the\n"
              "% page is sorted by year). Get the entry from the journal page (\"Export citation\"), Google Scholar,\n"
              "% Zotero, HAL, or https://doi.org/<DOI> (see README.md). Lines starting with % are comments.\n"
              "% Imported on " + time.strftime("%Y-%m-%d") +
              " from the old Google Sites page (journals + peer-reviewed conferences).\n\n")
    body = "\n\n".join(to_bib(e, f.pop("_key"), f, c) for _, e, f, c in out)
    OUT.write_text(header + body + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items()}, indent=1, ensure_ascii=False))
    print(len(out), "entries written to", OUT)


if __name__ == "__main__":
    main()
