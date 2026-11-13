"""Compare paper_kit/references.bib with arXiv API metadata (read-only).

  python -I review/scripts/k2_bib_check.py <api.xml> -> review/results/kit_bib_check.csv, kit_bib_abstracts.txt
The XML is the answer of http://export.arxiv.org/api/query?id_list=<all eprint ids of the bib> (fetched by the reviewer).
Compared: title (case and whitespace insensitive, LaTeX braces removed), author list (order, names with diacritics
normalised), year of the first version (the arXiv <published> field) against the bib `year`.
"""
from __future__ import annotations

import csv
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def delatex(s: str) -> str:
    s = re.sub(r"\\[`'^\"~c]\s*\{?\\?([A-Za-z])\}?", r"\1", s)  # accents: {\c{c}}, {\~a}, {\'e}, {\`e}
    s = s.replace("``", '"').replace("''", '"')
    return re.sub(r"[{}]", "", s)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_bib(text: str) -> dict[str, dict]:
    out = {}
    for m in re.finditer(r"@misc\{([^,]+),(.*?)\n\}", text, flags=re.S):
        key, body = m.group(1), m.group(2)
        f = {k: v for k, v in re.findall(r"^\s*(\w+)\s*=\s*\{(.*)\},?\s*$", body, flags=re.M)}
        out[f["eprint"]] = {"key": key, "title": delatex(f["title"]), "authors": [delatex(a) for a in f["author"].split(" and ")],
                            "year": f["year"], "class": f.get("primaryClass", ""), "note": f.get("note", "")}
    return out


def main(xml_path: str):
    bib = parse_bib((ROOT / "paper_kit" / "references.bib").read_text(encoding="utf-8"))
    root = ET.parse(xml_path).getroot()
    rows, abstracts = [], []
    seen = set()
    for e in root.findall("a:entry", NS):
        aid = re.sub(r"v\d+$", "", e.findtext("a:id", "", NS).rsplit("/abs/", 1)[-1])
        seen.add(aid)
        title = re.sub(r"\s+", " ", e.findtext("a:title", "", NS)).strip()
        authors = [a.findtext("a:name", "", NS).strip() for a in e.findall("a:author", NS)]
        published = e.findtext("a:published", "", NS)[:10]
        updated = e.findtext("a:updated", "", NS)[:10]
        pc = e.find("x:primary_category", NS)
        comment = re.sub(r"\s+", " ", e.findtext("x:comment", "", NS) or "").strip()
        b = bib.get(aid)
        if b is None:
            rows.append({"eprint": aid, "key": "NOT IN BIB"})
            continue
        t_ok = norm(b["title"]) == norm(title)
        a_ok = [norm(x) for x in b["authors"]] == [norm(x) for x in authors]
        y_ok = b["year"] == published[:4]
        rows.append({"eprint": aid, "key": b["key"], "title_ok": t_ok, "authors_ok": a_ok, "year_bib": b["year"],
                     "published": published, "updated": updated, "year_ok": y_ok,
                     "class_ok": b["class"] == (pc.get("term") if pc is not None else ""), "n_authors": len(authors),
                     "bib_note": b["note"], "arxiv_comment": comment[:160],
                     "arxiv_title": title if not t_ok else "", "arxiv_authors": "; ".join(authors) if not a_ok else "",
                     "bib_authors": "; ".join(b["authors"]) if not a_ok else ""})
        abstracts.append(f"### {aid} [{b['key']}] {title}\n{'; '.join(authors)} | published {published}, updated {updated} | {comment}\n"
                         + re.sub(r"\s+", " ", e.findtext("a:summary", "", NS)).strip() + "\n")
    for aid, b in bib.items():
        if aid not in seen:
            rows.append({"eprint": aid, "key": b["key"], "title_ok": "NOT RETURNED BY ARXIV"})
    cols = ["eprint", "key", "title_ok", "authors_ok", "year_ok", "year_bib", "published", "updated", "class_ok", "n_authors",
            "bib_note", "arxiv_comment", "arxiv_title", "arxiv_authors", "bib_authors"]
    out = ROOT / "review" / "results"
    with open(out / "kit_bib_check.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    (out / "kit_bib_abstracts.txt").write_text("\n".join(abstracts), encoding="utf-8")
    for r in rows:
        print({k: v for k, v in r.items() if k in cols[:12]})
    for r in rows:
        if r.get("arxiv_title") or r.get("arxiv_authors"):
            print("MISMATCH", r["eprint"], "| arXiv title:", r.get("arxiv_title"), "| arXiv authors:", r.get("arxiv_authors"), "| bib:", r.get("bib_authors"))


if __name__ == "__main__":
    main(sys.argv[1])
