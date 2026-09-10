"""
Refreshable scraper that gives `rental_permit_required` (permis de louer /
autorisation préalable de mise en location, loi ALUR art. L635-1+) a
national-ish, re-pollable source.

There is NO official national ingestible register of permis-de-louer communes
(the decision is taken commune-by-commune / EPCI-by-EPCI). A prior research
pass concluded the least-bad refreshable spine is:

  A. LocService.fr's "Permis de louer" guide page — a single alphabetical
     <ul> of ~450 commune names (no INSEE codes, no département), updated
     roughly monthly. National-ish coverage, LOW trust (crowd-maintained,
     name-only). We geocode each name via geo.api.gouv.fr.
        https://www.locservice.fr/guides/guide-proprietaire/mettre-son-logement-location/permis-de-louer

  B. A handful of département / métropole open-data layers that ARE
     machine-readable and carry INSEE codes + their own as-of date. Higher
     trust than A. Polled here without any GIS dependency (WFS-GML parsed
     with regex; OpenDataSoft Explore API v2.1 returns plain JSON):
       - DDTM Pas-de-Calais (62) "Mise en place du Permis de louer" (WFS/GML)
       - DDTM Hérault (34) "Communes ayant mis en œuvre l'AMPL" (WFS/GML)
       - Métropole Aix-Marseille-Provence "Secteurs soumis au permis de
         louer" (OpenDataSoft)
       - Bordeaux Métropole "Permis de louer / diviser / déclaration" (ODS)
       - Métropole Européenne de Lille "permis de louer" — no public WFS /
         no data.gouv resource (the only data.gouv entries are Roubaix-only
         and resourceless; opendata.roubaix.fr has an expired TLS cert), so
         we scrape the 29-commune list off lillemetropole.fr/permis-de-louer.

  C. The 26 hand-curated prefecture rows already in rental-permit.csv
     (Loiret + Tarn). HIGHEST trust — kept verbatim, never overwritten.

MERGE / OUTPUT
  Rewrites csv/reglementation-locative/rental-permit.csv in place:
    * comment header (lines starting with '#') + ONE csv header line, then
    * the frozen prefecture rows (source_type=prefecture), then
    * scraped rows, deduped by insee_code with priority
        prefecture  >  datagouv layer  >  locservice scrape
  The file stays COMMA-delimited with an `insee_code` and a
  `rental_permit_required` column so seed_rent_regulation.py's DictReader
  contract is unchanged. Two audit columns are appended:
        source_type      prefecture | datagouv | locservice
        match_confidence  high | medium | low
  (The task described the scraped columns as
   "insee_code;rental_permit_required;zone_detail;source;as_of_date;match_confidence";
   the ';' there is field shorthand — a real ';'-delimited file would break
   seed_rent_regulation.load_flagged_communes, which is comma-only. `source`
   is written as the existing `source_url` column.)

  Names that don't geocode (quartiers, EPCI labels, foreign towns, typos) are
  NOT dropped: they go to stderr AND to
  csv/reglementation-locative/rental-permit-unresolved.csv for manual review.

RUN (writes CSV only, never touches the DB):
  python -m pipeline.scripts.scrape_rental_permit
  python -m pipeline.scripts.scrape_rental_permit --skip-locservice   # layers only

Re-run QUARTERLY (LocService moves monthly; the open-data layers ~half-yearly).
Then run `python -m pipeline.scripts.seed_rent_regulation --dry-run` to check
the enlarged CSV still parses, then the real seed.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "reglementation-locative"
PERMIT_CSV = CSV_DIR / "rental-permit.csv"
UNRESOLVED_CSV = CSV_DIR / "rental-permit-unresolved.csv"

TODAY = date.today().isoformat()
UA = {"User-Agent": "Mozilla/5.0 (immo-data-science scrape_rental_permit)"}

FINAL_COLUMNS = [
    "insee_code",
    "commune_name",
    "department_code",
    "zone_detail",
    "rental_permit_required",
    "source_url",
    "as_of_date",
    "source_type",
    "match_confidence",
]

LOCSERVICE_URL = (
    "https://www.locservice.fr/guides/guide-proprietaire/"
    "mettre-son-logement-location/permis-de-louer"
)
MEL_URL = "https://www.lillemetropole.fr/permis-de-louer"
GEO_API = "https://geo.api.gouv.fr/communes"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _get(url: str, timeout: int = 90) -> bytes:
    return urlopen(Request(url, headers=UA), timeout=timeout).read()


def _norm(s: str) -> str:
    """Loose commune-name key: lowercase, no accents, no separators, no
    leading article, œ/æ expanded, saint/sainte unified."""
    s = s.lower().replace("œ", "oe").replace("æ", "ae").replace("’", "'")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"^(le |la |les |l')", "", s)
    s = re.sub(r"\bsainte?\b", "st", s)
    s = re.sub(r"[\s'’\-._]", "", s)
    return s


def _api_name(name: str) -> str:
    """geo.api.gouv.fr's fuzzy `nom=` filter chokes on ’ and ligatures."""
    return name.replace("’", "'").replace("œ", "oe").replace("æ", "ae")


def _strip_paren(name: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*$", "", name).strip()


_DEPT_RE = re.compile(r"^(?:d[ée]p(?:t|\.)?\s*)?(2[ab]|9[0-5]|97[1-6]|0?[1-9]|[1-8]\d)$", re.I)


def dept_from_note(note: str) -> str | None:
    """'(93)' / '(dépt 62)' in a LocService parenthesis is a département hint,
    not a zone note. Returns a zero-padded code or None."""
    m = _DEPT_RE.match(note.strip())
    if not m:
        return None
    code = m.group(1).upper()
    return code if len(code) >= 2 else code.zfill(2)


# --------------------------------------------------------------------------- #
# geocoding
# --------------------------------------------------------------------------- #
_geo_cache: dict[str, list[dict]] = {}


def _geo_query(q: str) -> list[dict]:
    url = (
        f"{GEO_API}?nom={quote(q)}"
        "&fields=code,nom,departement,population&boost=population&limit=15"
    )
    for attempt in range(3):
        try:
            return json.loads(_get(url, timeout=30))
        except (URLError, HTTPError, json.JSONDecodeError) as e:
            if attempt == 2:
                print(f"  geo.api.gouv.fr failed for {q!r}: {e}", file=sys.stderr)
                return []
            time.sleep(1.5)
    return []


def geo_lookup(name: str) -> list[dict]:
    key = _norm(name)
    if key in _geo_cache:
        return _geo_cache[key]
    variants = [_api_name(name)]
    alt = _api_name(name).replace("'", " ").replace("-", " ")
    if alt not in variants:
        variants.append(alt)
    data: list[dict] = []
    for v in variants:
        data = _geo_query(v)
        if data:
            break
        time.sleep(0.05)
    _geo_cache[key] = data
    time.sleep(0.05)
    return data


def resolve(raw_name: str, dept_hint: str | None) -> tuple[dict | None, str, str]:
    """
    Returns (chosen_candidate_or_None, match_confidence, reason).
    match_confidence: high (dept-filtered exact) | medium (unique exact) |
                      low (ambiguous / fuzzy, highest population kept).
    """
    name = _strip_paren(raw_name)
    cands = geo_lookup(name)
    if not cands:
        return None, "", "no geo.api.gouv.fr match"

    nkey = _norm(name)
    exact = [c for c in cands if _norm(c.get("nom", "")) == nkey]

    if dept_hint:
        dept_hits = [
            c for c in (exact or cands)
            if (c.get("departement") or {}).get("code") == dept_hint
        ]
        if len(dept_hits) == 1:
            return dept_hits[0], "high", ""
        if len(dept_hits) > 1:
            dept_hits.sort(key=lambda c: c.get("population") or 0, reverse=True)
            return dept_hits[0], "low", f"{len(dept_hits)} matches in dept {dept_hint}"
        return None, "", f"no match in dept {dept_hint}"

    if len(exact) == 1:
        return exact[0], "medium", ""
    if len(exact) > 1:
        exact.sort(key=lambda c: c.get("population") or 0, reverse=True)
        return exact[0], "low", f"{len(exact)} exact name matches, kept highest pop"

    # no exact — accept only a prefix/containment fuzzy match, else give up
    for c in sorted(cands, key=lambda c: c.get("population") or 0, reverse=True):
        ck = _norm(c.get("nom", ""))
        if ck.startswith(nkey) or nkey.startswith(ck):
            return c, "low", f"fuzzy match -> {c.get('nom')}"
    return None, "", f"no exact match (top: {cands[0].get('nom')})"


# --------------------------------------------------------------------------- #
# source A: LocService.fr
# --------------------------------------------------------------------------- #
def scrape_locservice() -> tuple[list[dict], str]:
    """Returns ([{raw_name, zone_note}], as_of_date)."""
    html = _get(LOCSERVICE_URL).decode("utf-8", "replace")

    m = re.search(
        r"instaur[ée]+ le permis de louer[^<]*?"
        r"mise [àa] jour le\s*(\d{2}/\d{2}/\d{4})",
        html,
    )
    as_of = TODAY
    if m:
        d, mo, y = m.group(1).split("/")
        as_of = f"{y}-{mo}-{d}"

    anchor = html.find("ayant instaur")
    if anchor == -1:
        anchor = html.find("Liste des communes concern", 200_000)
    ul = re.search(r"<ul[^>]*>(.*?)</ul>", html[anchor:], re.S)
    if not ul:
        raise RuntimeError("LocService: commune <ul> not found — page layout changed")

    out = []
    for li in re.findall(r"<li[^>]*>(.*?)</li>", ul.group(1), re.S):
        txt = re.sub(r"<[^>]+>", "", li)
        txt = (
            txt.replace("&rsquo;", "’").replace("&#39;", "'")
            .replace("&laquo;", "«").replace("&raquo;", "»")
            .replace("&nbsp;", " ").replace("&amp;", "&")
        )
        txt = txt.strip()
        if not txt:
            continue
        pm = re.search(r"\((.*?)\)", txt)
        note = pm.group(1).strip() if pm else ""
        dept = dept_from_note(note)
        out.append({
            "raw_name": txt,
            "zone_note": "" if dept else note,
            "dept_hint": dept,
        })
    return out, as_of


# --------------------------------------------------------------------------- #
# source B1/B2: geo-ide WFS layers (Pas-de-Calais, Hérault) — GML, regex-parsed
# --------------------------------------------------------------------------- #
def _wfs_members(map_url: str, layer: str) -> list[str]:
    url = (
        f"{map_url}&SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&typeName={layer}"
    )
    xml = _get(url, timeout=180).decode("utf-8", "replace")
    tag = layer.split(":", 1)[1]
    return re.findall(rf"<ms:{tag}>(.*?)</ms:{tag}>", xml, re.S)


def _tagval(member: str, tag: str) -> str:
    m = re.search(rf"<ms:{tag}>(.*?)</ms:{tag}>", member, re.S)
    if not m:
        return ""
    v = m.group(1).strip()
    return v.replace("&#39;", "'").replace("&amp;", "&")


def scrape_pas_de_calais() -> tuple[list[dict], str]:
    src = "https://www.data.gouv.fr/datasets/mise-en-place-du-permis-de-louer"
    map_url = (
        "https://ogc.geo-ide.developpement-durable.gouv.fr/wxs?map=/opt/data/stack/"
        "mapfiles/1.4/org_38066/26d3c37a-3d97-4dff-8745-ac826cbf6672.internet.map"
    )
    rows = []
    for mem in _wfs_members(map_url, "ms:L_PERMIS_LOUER_S_062"):
        autor = _tagval(mem, "autor_loca") == "1"
        decla = _tagval(mem, "decla_loca") == "1"
        if not (autor or decla):
            continue
        insee = _tagval(mem, "inseecom")
        if not insee:
            continue
        kind = "autorisation préalable" if autor else "déclaration"
        rows.append({
            "insee_code": insee,
            "commune_name": _tagval(mem, "nomcom"),
            "department_code": _tagval(mem, "inseedep") or "62",
            "zone_detail": kind,
            "source_url": src,
            "as_of_date": "2026-04-03",
        })
    return rows, "2026-04-03"


def scrape_herault() -> tuple[list[dict], str]:
    src = (
        "https://www.data.gouv.fr/datasets/fichier-des-communes-ayant-mis-en-oeuvre-"
        "lautorisation-prealable-a-la-mise-en-location-ampl-permis-de-louer-dans-lherault"
    )
    map_url = (
        "https://ogc.geo-ide.developpement-durable.gouv.fr/wxs?map=/opt/data/stack/"
        "mapfiles/1.4/org_38010/6c14bfe7-5a9b-4fe2-b4af-0bdd8a2ad4b7.internet.map"
    )
    rows = []
    for mem in _wfs_members(map_url, "ms:N_PERMIS_LOUER_COMMUNE_S_034"):
        perim = _tagval(mem, "PERIM")
        if not perim or perim.lower() == "pas de permis de louer":
            continue
        insee = _tagval(mem, "INSEE_COMM")
        if not insee:
            continue
        etat = _tagval(mem, "ETAT") or "autorisation préalable"
        zone = "commune entière" if perim.lower() == "commune" else "partiel"
        rows.append({
            "insee_code": insee,
            "commune_name": _tagval(mem, "NOM_COMM").title(),
            "department_code": "34",
            "zone_detail": f"{etat} ({zone})",
            "source_url": src,
            "as_of_date": "2025-12-31",
        })
    return rows, "2025-12-31"


# --------------------------------------------------------------------------- #
# source B3/B4: OpenDataSoft Explore API (Aix-Marseille, Bordeaux)
# --------------------------------------------------------------------------- #
def _ods_records(domain: str, dataset: str) -> list[dict]:
    out, offset = [], 0
    while True:
        url = (
            f"https://{domain}/api/explore/v2.1/catalog/datasets/{dataset}/records"
            f"?limit=100&offset={offset}&select=*"
        )
        d = json.loads(_get(url, timeout=60))
        res = d.get("results", [])
        out.extend(res)
        offset += len(res)
        if not res or offset >= d.get("total_count", 0) or offset >= 10_000:
            break
    return out


def scrape_aix_marseille() -> tuple[list[dict], str]:
    src = (
        "https://www.data.gouv.fr/datasets/"
        "observatoire-habitat-perimetre-des-secteurs-soumis-au-permis-de-louer"
    )
    rows, maxd = [], ""
    for r in _ods_records("data.ampmetropole.fr", "secteurs-soumis-au-permis-de-louer"):
        insee = str(r.get("codeinsee") or "").strip()
        if not insee:
            continue
        d = (r.get("date_deb") or "")[:10]
        maxd = max(maxd, d)
        rows.append({
            "insee_code": insee,
            "commune_name": r.get("commune") or "",
            "department_code": insee[:2],
            "zone_detail": f"secteur: {r.get('secteur') or 'n/a'}",
            "source_url": src,
            "as_of_date": d or TODAY,
        })
    return _dedup_layer(rows), (maxd or TODAY)


def scrape_bordeaux() -> tuple[list[dict], str]:
    src = "https://opendata.bordeaux-metropole.fr/explore/dataset/u_permis_location_s/"
    # APML / APML_30 = permis de louer ; DML = déclaration ; APD = permis de diviser
    keep = {"APML", "APML_30", "DML"}
    per_commune: dict[str, dict] = {}
    maxd = ""
    for r in _ods_records("opendata.bordeaux-metropole.fr", "u_permis_location_s"):
        typ = (r.get("type") or "").upper()
        insee = str(r.get("insee") or "").strip()
        if not insee or typ not in keep:
            continue
        d = (r.get("mdate") or r.get("cdate") or "")[:10]
        maxd = max(maxd, d)
        cur = per_commune.setdefault(insee, {
            "insee_code": insee,
            "commune_name": r.get("nom") or "",
            "department_code": insee[:2],
            "zone_detail": set(),
            "source_url": src,
            "as_of_date": d or TODAY,
        })
        cur["zone_detail"].add("autorisation préalable" if typ.startswith("APML") else "déclaration")
        cur["as_of_date"] = max(cur["as_of_date"], d or "")
    rows = []
    for cur in per_commune.values():
        cur["zone_detail"] = " + ".join(sorted(cur["zone_detail"]))
        cur["as_of_date"] = cur["as_of_date"] or TODAY
        rows.append(cur)
    return rows, (maxd or TODAY)


def _dedup_layer(rows: list[dict]) -> list[dict]:
    """Collapse multiple sectors of one commune into one row (keep 1st zone note)."""
    seen: dict[str, dict] = {}
    for r in rows:
        k = r["insee_code"]
        if k not in seen:
            seen[k] = r
        else:
            seen[k]["as_of_date"] = max(seen[k]["as_of_date"], r["as_of_date"])
    return list(seen.values())


# --------------------------------------------------------------------------- #
# source B5: Métropole Européenne de Lille (HTML list, names only, dept 59)
# --------------------------------------------------------------------------- #
def scrape_lille() -> tuple[list[dict], str]:
    html = _get(MEL_URL).decode("utf-8", "replace")
    m = re.search(
        r"communes?\s+(?:restent\s+)?concern[ée]+e?s?\s*:?\s*(.*?)</p>", html, re.S | re.I
    )
    if not m:
        raise RuntimeError("MEL: commune sentence not found — page layout changed")
    blob = re.sub(r"<[^>]+>", "", m.group(1))
    blob = blob.replace("&nbsp;", " ").replace("&#39;", "'").replace("&rsquo;", "’")
    parts = re.split(r",| et ", blob)
    out = []
    for p in parts:
        p = p.strip(" . ")
        if p and len(p) > 1:
            out.append({"raw_name": p, "zone_note": "APML/DML (périmètre communal MEL)"})
    return out, TODAY


# --------------------------------------------------------------------------- #
# existing CSV
# --------------------------------------------------------------------------- #
def read_existing() -> tuple[list[str], list[dict]]:
    """Returns (comment_header_lines, existing_data_rows_as_dicts)."""
    if not PERMIT_CSV.exists():
        return [], []
    raw = PERMIT_CSV.read_text(encoding="utf-8-sig").splitlines()
    comments = [ln for ln in raw if ln.lstrip().startswith("#")]
    data_lines = [ln for ln in raw if ln.strip() and not ln.lstrip().startswith("#")]
    rows = list(csv.DictReader(data_lines))
    norm = []
    for r in rows:
        norm.append({
            "insee_code": (r.get("insee_code") or "").strip().zfill(5),
            "commune_name": (r.get("commune_name") or "").strip(),
            "department_code": (r.get("department_code") or "").strip(),
            "zone_detail": (r.get("zone_detail") or "UNKNOWN").strip(),
            "rental_permit_required": (r.get("rental_permit_required") or "TRUE").strip().upper(),
            "source_url": (r.get("source_url") or r.get("source") or "").strip(),
            "as_of_date": (r.get("as_of_date") or "").strip(),
            # rows written before this script existed are prefecture-sourced
            "source_type": (r.get("source_type") or "prefecture").strip(),
            "match_confidence": (r.get("match_confidence") or "high").strip(),
        })
    return comments, norm


HEADER_COMMENT = f"""# rental-permit.csv - Permis de louer / autorisation prealable de mise en location (loi ALUR, art. L635-1+)
#
# COVERAGE IS PARTIAL. There is no official national list: each EPCI (or commune) decides
# unilaterally, often only for specific streets. This file is assembled from 3 tiers:
#
#   1. PREFECTURE rows (source_type=prefecture, match_confidence=high) - 26 hand-curated rows
#      for the Loiret (45) and Tarn (81), whose prefectures publish a readable list. Kept
#      VERBATIM by pipeline/scripts/scrape_rental_permit.py, never overwritten.
#        - https://www.loiret.gouv.fr/.../Permis-de-louer-Permis-de-diviser/Liste-des-communes
#        - https://www.tarn.gouv.fr/.../Que-faire-face-a-une-situation-d-habitat-indigne
#
#   2. DATAGOUV layers (source_type=datagouv) - machine-readable dept/metropole open data,
#      each with its own INSEE codes + as_of_date. Refreshed by scrape_rental_permit.py:
#        - DDTM Pas-de-Calais (62)  WFS  https://www.data.gouv.fr/datasets/mise-en-place-du-permis-de-louer
#        - DDTM Herault (34)        WFS  https://www.data.gouv.fr/datasets/fichier-des-communes-ayant-mis-en-oeuvre-lautorisation-prealable-a-la-mise-en-location-ampl-permis-de-louer-dans-lherault
#        - Metropole Aix-Marseille-Provence  ODS  https://data.ampmetropole.fr/explore/dataset/secteurs-soumis-au-permis-de-louer/
#        - Bordeaux Metropole                ODS  https://opendata.bordeaux-metropole.fr/explore/dataset/u_permis_location_s/
#        - Metropole Europeenne de Lille     HTML https://www.lillemetropole.fr/permis-de-louer  (no public WFS / no data.gouv resource)
#
#   3. LOCSERVICE scrape (source_type=locservice, match_confidence=medium|low) - names only,
#      geocoded via geo.api.gouv.fr. LOW trust; medium only when the name matched exactly
#      one commune. Names that did not resolve are in rental-permit-unresolved.csv.
#        - https://www.locservice.fr/guides/guide-proprietaire/mettre-son-logement-location/permis-de-louer
#
# Dedup priority: prefecture > datagouv > locservice. Every row carries its own source_url
# + as_of_date so the data stays auditable. Re-run scrape_rental_permit.py QUARTERLY.
#
# This file stays COMMA-delimited with `insee_code` + `rental_permit_required` columns so
# pipeline/scripts/seed_rent_regulation.py (csv.DictReader, comma) is unaffected. Only TRUE
# rows are ever listed (opt-in list; absence != FALSE).
#
# COLUMNS: insee_code,commune_name,department_code,zone_detail,rental_permit_required,source_url,as_of_date,source_type,match_confidence
# Verified / regenerated: {TODAY}"""


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-locservice", action="store_true",
                    help="only poll the datagouv layers (skip the LocService spine)")
    ap.add_argument("--permit-csv", default=str(PERMIT_CSV))
    args = ap.parse_args()

    out_csv = Path(args.permit_csv)

    _comments, existing = read_existing()
    prefecture_rows = [r for r in existing if r["source_type"] == "prefecture"]
    prefecture_codes = {r["insee_code"] for r in prefecture_rows}
    print(f"kept {len(prefecture_rows)} frozen prefecture rows "
          f"({sorted({r['department_code'] for r in prefecture_rows})})")

    unresolved: list[dict] = []
    # insee -> row dict, plus a rank for priority (2=datagouv, 1=locservice)
    scraped: dict[str, tuple[int, dict]] = {}

    def offer(code: str, rank: int, row: dict) -> None:
        code = code.strip().zfill(5)
        if code in prefecture_codes:
            return
        if code not in scraped or rank > scraped[code][0]:
            row["insee_code"] = code
            scraped[code] = (rank, row)

    # ---- B. datagouv layers -------------------------------------------------
    layer_counts: dict[str, int] = {}
    datagouv_layers = [
        ("Pas-de-Calais (62) WFS", scrape_pas_de_calais),
        ("Hérault (34) WFS", scrape_herault),
        ("Aix-Marseille-Provence ODS", scrape_aix_marseille),
        ("Bordeaux Métropole ODS", scrape_bordeaux),
    ]
    for label, fn in datagouv_layers:
        try:
            rows, as_of = fn()
        except (URLError, HTTPError, RuntimeError, json.JSONDecodeError) as e:
            print(f"  [WARN] {label}: {e} — skipped", file=sys.stderr)
            layer_counts[label] = 0
            continue
        for r in rows:
            offer(r["insee_code"], 2, {
                "commune_name": r["commune_name"],
                "department_code": r["department_code"],
                "zone_detail": r["zone_detail"] or "UNKNOWN",
                "rental_permit_required": "TRUE",
                "source_url": r["source_url"],
                "as_of_date": r["as_of_date"] or as_of,
                "source_type": "datagouv",
                "match_confidence": "high",
            })
        layer_counts[label] = len(rows)
        print(f"  {label}: {len(rows)} communes (as of {as_of})")

    # ---- B5. Métropole Européenne de Lille (names -> geocode, dept 59) ------
    try:
        mel, _ = scrape_lille()
        mel_ok = 0
        for item in mel:
            cand, conf, reason = resolve(item["raw_name"], dept_hint="59")
            if cand is None:
                unresolved.append({"raw_name": item["raw_name"], "source": "MEL",
                                   "department_hint": "59", "reason": reason})
                print(f"  [MEL unresolved] {item['raw_name']!r}: {reason}", file=sys.stderr)
                continue
            mel_ok += 1
            offer(cand["code"], 2, {
                "commune_name": cand["nom"],
                "department_code": (cand.get("departement") or {}).get("code", "59"),
                "zone_detail": item["zone_note"] or "UNKNOWN",
                "rental_permit_required": "TRUE",
                "source_url": MEL_URL,
                "as_of_date": TODAY,
                "source_type": "datagouv",
                "match_confidence": conf if conf == "high" else "medium",
            })
        layer_counts["Métropole Européenne de Lille (HTML)"] = mel_ok
        print(f"  Métropole Européenne de Lille (HTML): {mel_ok} communes resolved")
    except (URLError, HTTPError, RuntimeError) as e:
        print(f"  [WARN] MEL: {e} — skipped", file=sys.stderr)
        layer_counts["Métropole Européenne de Lille (HTML)"] = 0

    # ---- A. LocService spine ---------------------------------------------------
    ls_count = 0
    if not args.skip_locservice:
        try:
            entries, ls_as_of = scrape_locservice()
            print(f"\nLocService: {len(entries)} commune names (page updated {ls_as_of})")
            for item in entries:
                dh = item.get("dept_hint")
                cand, conf, reason = resolve(item["raw_name"], dept_hint=dh)
                if cand is None:
                    unresolved.append({"raw_name": item["raw_name"], "source": "LocService",
                                       "department_hint": dh or "", "reason": reason})
                    print(f"  [LocService unresolved] {item['raw_name']!r}: {reason}",
                          file=sys.stderr)
                    continue
                ls_count += 1
                offer(cand["code"], 1, {
                    "commune_name": cand["nom"],
                    "department_code": (cand.get("departement") or {}).get("code", ""),
                    "zone_detail": item["zone_note"] or "UNKNOWN",
                    "rental_permit_required": "TRUE",
                    "source_url": LOCSERVICE_URL,
                    "as_of_date": ls_as_of,
                    "source_type": "locservice",
                    "match_confidence": conf or "low",
                })
        except (URLError, HTTPError, RuntimeError) as e:
            print(f"  [WARN] LocService: {e} — skipped", file=sys.stderr)

    # ---- merge + write ------------------------------------------------------
    final_rows = list(prefecture_rows)
    for _rank, row in sorted(scraped.values(), key=lambda t: t[1]["insee_code"]):
        final_rows.append(row)

    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        f.write(HEADER_COMMENT.rstrip() + "\n")
        w = csv.DictWriter(f, fieldnames=FINAL_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in final_rows:
            w.writerow({k: r.get(k, "") for k in FINAL_COLUMNS})

    if unresolved:
        with open(UNRESOLVED_CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["raw_name", "source", "department_hint", "reason"])
            w.writeheader()
            for r in sorted(unresolved, key=lambda x: (x["source"], x["raw_name"])):
                w.writerow(r)

    # ---- report ----------------------------------------------------------------
    by_type: dict[str, int] = {}
    for r in final_rows:
        by_type[r["source_type"]] = by_type.get(r["source_type"], 0) + 1

    print("\n" + "=" * 60)
    print("SOURCES")
    print(f"  prefecture (frozen)          : {len(prefecture_rows)}")
    for label, n in layer_counts.items():
        print(f"  datagouv · {label:<28}: {n}")
    print(f"  LocService (resolved)        : {ls_count}")
    print("-" * 60)
    print("WRITTEN TO rental-permit.csv (deduped, priority prefecture>datagouv>locservice)")
    for t in ("prefecture", "datagouv", "locservice"):
        print(f"  source_type={t:<11}: {by_type.get(t, 0)}")
    print(f"  TOTAL UNIQUE COMMUNES        : {len(final_rows)}")
    print(f"  unresolved (-> {UNRESOLVED_CSV.name}): {len(unresolved)}")
    print("=" * 60)
    print(f"\nwrote {out_csv}")
    if unresolved:
        print(f"wrote {UNRESOLVED_CSV}")
    print("\nnext: python -m pipeline.scripts.seed_rent_regulation --dry-run")


if __name__ == "__main__":
    main()
