"""ClinicalTrials.gov · PubMed 공개 API 에서 간이 메타분석 입력표 초안을 만든다.

- 키·비밀번호 필요 없음. 밖으로 나가는 것은 검색어(성분명·지표 이름·임상번호)뿐이다.
- 필요한 부분(평가변수·대상자·군)만 받고, 받자마자 작은 표(행 목록)로 줄인다.
- 사이트가 고른 것은 '초안'이다. 사람이 화면에서 확인·고친다.

화면(scouting.py)은 search() · regroup() · pubmed_*() 만 쓴다.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter

API = "https://clinicaltrials.gov/api/v2/studies"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
UA = {"User-Agent": "Mozilla/5.0 (product-review)"}
FIELDS = ",".join([
    "protocolSection.identificationModule",
    "protocolSection.designModule.phases",
    "protocolSection.conditionsModule",
    "protocolSection.eligibilityModule",
    "protocolSection.armsInterventionsModule.interventions",
    "resultsSection.outcomeMeasuresModule",
])
MAX_PAGES = 5            # 성분 하나당 최대 500개 임상
PHASE_CODE = {"1상": "PHASE1", "2상": "PHASE2", "3상": "PHASE3", "4상": "PHASE4"}
Z = 1.959963984540054
TOP_JOURNALS = ("N Engl J Med", "Lancet", "JAMA", "BMJ")

# 약 이름처럼 생긴 낱말 (다른 약이 섞인 군·바탕 치료를 알아보는 데 쓴다)
_DRUG_SUFFIX = ("gliflozin|gliptin|statin|sartan|pril|olol|dipine|mab|glutide|tide|formin|glitazone|"
                "mibe|prazole|tinib|ciclib|parib|vir|cillin|mycin|floxacin|conazole|lukast|tidine|setron|"
                "oxetine|azepam|triptan|profen|parin|xaban|gatran|grel|semide|thiazide|sterone|olone|"
                "asone|terol|tropium|lutamide|relin|fibrate|sartan|glinide|lisib|degib|rafenib|zomib")
_DRUG_RE = re.compile(rf"\b[a-z]{{3,}}(?:{_DRUG_SUFFIX})\b")
_DRUG_WORDS = {"metformin", "insulin", "aspirin", "warfarin", "niacin", "colesevelam", "ezetimibe",
               "bempedoic", "inclisiran", "glimepiride", "glipizide", "glyburide", "glibenclamide",
               "pioglitazone", "acarbose", "levothyroxine", "prednisone", "methotrexate"}
_PLACEBO_RE = re.compile(r"placebo|\bpbo\b|sham|dummy", re.I)
_PEDIA_RE = re.compile(r"pediatric|paediatric|child|adolescent|juvenile|infant", re.I)
_REGION = [("japan", "일본"), ("chinese|china", "중국"), ("korea", "한국"), ("taiwan", "대만"),
           ("india", "인도"), ("asian|asia", "아시아"), ("hispanic|latin", "중남미"),
           ("black|african", "흑인")]


# ── 받기 ─────────────────────────────────────────────────────────────
def _get(url, params, timeout=25):
    q = urllib.parse.urlencode(params, safe=",:|[]()\"")
    req = urllib.request.Request(url + "?" + q, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_studies(drug, phases=("3상", "4상"), out_kw="", cond="", timeout=25):
    """성분 하나로 결과가 올라온 임상을 받는다. 단계는 받은 뒤 거른다(필터 문법 차이에 안전)."""
    base = {"query.intr": drug, "pageSize": 100, "format": "json",
            "aggFilters": "results:with", "fields": FIELDS}
    if (cond or "").strip():
        base["query.cond"] = cond.strip()
    kw = [k for k in _kw_list(out_kw) if re.fullmatch(r"[A-Za-z0-9 \-]+", k)]
    if kw:
        base["query.outc"] = " OR ".join(kw)
    out, tok = [], None
    for _ in range(MAX_PAGES):
        p = dict(base)
        if tok:
            p["pageToken"] = tok
        try:
            j = json.loads(_get(API, p, timeout))
        except urllib.error.HTTPError as e:
            if e.code == 400 and "query.outc" in base and not out:
                base.pop("query.outc")          # 지표 검색 칸을 못 쓰면 빼고 다시
                continue
            raise
        out += j.get("studies", [])
        tok = j.get("nextPageToken")
        if not tok:
            break
    want = {PHASE_CODE[x] for x in phases if x in PHASE_CODE}
    if want:
        out = [s for s in out
               if set(s.get("protocolSection", {}).get("designModule", {}).get("phases") or []) & want]
    return out, bool(tok)


# ── 이름·군 다루기 ───────────────────────────────────────────────────
def parse_drug(text):
    """'empagliflozin 10' → ('empagliflozin', '10'). 용량 숫자는 없어도 된다."""
    t = " ".join((text or "").lower().replace("mg", " ").split())
    m = re.match(r"^(.*?)[\s]+(\d+(?:\.\d+)?)$", t)
    if m and m.group(1):
        return m.group(1).strip(), m.group(2)
    return t, None


def _kw_list(out_kw):
    return [k.strip() for k in re.split(r"[,|/]", out_kw or "") if k.strip()]


_NOT_DRUG = {"cholesterol", "ergosterol", "phytosterol", "sitosterol"}


def drug_words(t):
    t = (t or "").lower()
    return (set(_DRUG_RE.findall(t)) | {w for w in _DRUG_WORDS if w in t}) - _NOT_DRUG


def aliases(study, drug):
    """성분 이름 + 그 임상의 시험약 다른 이름(개발코드 등). 복합제 이름은 넣지 않는다."""
    names = {drug}
    for iv in study.get("protocolSection", {}).get("armsInterventionsModule", {}).get("interventions") or []:
        nm = (iv.get("name") or "").lower()
        others = [o.lower() for o in iv.get("otherNames") or []]
        allx = [nm] + others
        if not any(drug in x for x in allx):
            continue
        if any(re.search(r"[/+]|\bfdc\b|combination|fixed", x) for x in allx):
            continue
        if any(drug_words(x) - {drug} - set(drug.split()) for x in allx
               if not any(drug in y for y in [x])):
            continue
        for x in allx:
            x = re.sub(r"\s*\d+(\.\d+)?\s*mg.*$", "", x).strip()
            if len(x) >= 3 and not _PLACEBO_RE.search(x):
                names.add(x)
    return names


def _has(title, names):
    t = (title or "").lower()
    return any(n in t for n in names)


def _same(w, names):
    return any(w in n or n in w for n in names)


def _others(title, names):
    """군 이름에 든 다른 약 (그 성분 자신·다른 이름은 뺌)."""
    return {w for w in drug_words(title) if not _same(w, names)}


def _dose_ok(title, dose):
    if not dose:
        return True
    t = (title or "").lower()
    return re.search(rf"(?<![\d.]){re.escape(dose)}\s*mg", t) is not None or re.search(r"\d\s*mg", t) is None


def find_arm(groups, names, dose, avoid=()):
    """그 성분 군(다른 약이 '추가로' 섞이지 않은 군)을 찾는다. 용량이 여럿이면 첫 군 + 표시."""
    hits = []
    for g in groups:
        t = g.get("title", "")
        if not _has(t, names) or _PLACEBO_RE.match(t.strip()) or not _dose_ok(t, dose):
            continue
        if any(_same(x, avoid) for x in _others(t, names)):
            continue
        hits.append(g)
    return hits


def find_comparator(groups, names, comp_names):
    """공통 비교약 군 (그 성분이 섞이지 않은 군)."""
    return [g for g in groups if _has(g.get("title", ""), comp_names) and not _has(g.get("title", ""), names)
            and not _PLACEBO_RE.match(g.get("title", "").strip())]


def find_placebo(groups, names):
    return [g for g in groups if _PLACEBO_RE.search(g.get("title", "")) and not _has(g.get("title", ""), names)]


# ── 평가변수 고르기 ──────────────────────────────────────────────────
def _week_ok(o, week):
    if not week:
        return True
    tf = ((o.get("timeFrame") or "") + " " + (o.get("title") or "")).lower()
    w = str(int(week))
    return re.search(rf"(week|wk)s?\s*{w}\b|\b{w}\s*-?\s*(week|wk)", tf) is not None


def _kind_ok(o, kind):
    pt = (o.get("paramType") or "").upper()
    if kind == "연속형":
        return pt in ("MEAN", "LEAST_SQUARES_MEAN", "GEOMETRIC_MEAN", "GEOMETRIC_LEAST_SQUARES_MEAN")
    if kind == "이분형":
        return pt in ("COUNT_OF_PARTICIPANTS", "NUMBER", "COUNT_OF_UNITS")
    if kind == "생존분석":
        return any("hazard" in (a.get("paramType") or "").lower() for a in o.get("analyses") or [])
    return False


def candidates(study, kind, out_kw, week):
    """조건에 맞는 평가변수 (1차 먼저). [(o, 1차여부)]"""
    kws = [k.lower() for k in _kw_list(out_kw)]
    outs = study.get("resultsSection", {}).get("outcomeMeasuresModule", {}).get("outcomeMeasures") or []
    res, why = [], None
    for o in outs:
        text = ((o.get("title") or "") + " " + (o.get("description") or "")).lower()
        if kws and not any(k in text for k in kws):
            continue
        why = why or "시점 다름"
        if not _week_ok(o, week):
            continue
        why = "종류 다름"
        if not _kind_ok(o, kind):
            continue
        res.append((o, o.get("type") == "PRIMARY"))
    res.sort(key=lambda x: not x[1])
    return res, (why or "지표 이름 없음")


def _arm_stats(o, kind):
    """군 id → 숫자들."""
    try:
        n = {c["groupId"]: _f(c.get("value")) for c in o["denoms"][0]["counts"]}
    except (KeyError, IndexError, TypeError):
        n = {}
    meas = {}
    try:
        for m in o["classes"][0]["categories"][0]["measurements"]:
            meas[m["groupId"]] = m
    except (KeyError, IndexError, TypeError):
        pass
    disp = (o.get("dispersionType") or "").upper()
    unit = (o.get("unitOfMeasure") or "").lower()
    pt = (o.get("paramType") or "").upper()
    out = {}
    for gid, m in meas.items():
        k = n.get(gid)
        v = _f(m.get("value"))
        if kind == "연속형":
            sd = None
            s, lo, hi = _f(m.get("spread")), _f(m.get("lowerLimit")), _f(m.get("upperLimit"))
            if "DEVIATION" in disp:
                sd = s
            elif "ERROR" in disp and s is not None and k:
                sd = s * k ** .5
            elif "CONFIDENCE" in disp and None not in (lo, hi) and k:
                sd = (hi - lo) / (2 * Z) * k ** .5
            out[gid] = {"n": k, "m": v, "sd": sd}
        elif kind == "이분형":
            e = v
            if v is not None and k and (pt == "NUMBER" and ("%" in unit or "percent" in unit)):
                e = round(v / 100 * k)
            out[gid] = {"n": k, "e": e, "pct": pt == "NUMBER" and ("%" in unit or "percent" in unit)}
    return out


def _analysis(o, g1, g2, kind):
    for a in o.get("analyses") or []:
        if set(a.get("groupIds") or []) != {g1, g2}:
            continue
        pt = (a.get("paramType") or "").lower()
        if kind == "생존분석" and "hazard" not in pt:
            continue
        if kind == "연속형" and ("ratio" in pt or "hazard" in pt):
            continue
        v, lo, hi = _f(a.get("paramValue")), _f(a.get("ciLowerLimit")), _f(a.get("ciUpperLimit"))
        pct = _f(a.get("ciPctValue")) or 95
        if None in (v, lo, hi) or abs(pct - 95) > .1 or hi <= lo:
            continue
        return v, lo, hi
    return None


def _f(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fill(kind, o, g1, g2):
    """시험군 g1, 대조군 g2 의 숫자 칸."""
    st = _arm_stats(o, kind)
    a, b = st.get(g1, {}), st.get(g2, {})
    row = {}
    if kind == "연속형":
        row.update(n1=a.get("n"), m1=a.get("m"), sd1=a.get("sd"), n2=b.get("n"), m2=b.get("m"), sd2=b.get("sd"))
        an = _analysis(o, g1, g2, kind)
        if an:
            d, lo, hi = an
            if None not in (a.get("m"), b.get("m")):
                arm = a["m"] - b["m"]
                if arm * d < 0 and abs(arm) > max(abs(d) * .3, 1e-9):   # 분석이 반대 방향으로 적힌 경우
                    d, lo, hi = -d, -hi, -lo
            row.update(d=d, lo=lo, hi=hi)
    elif kind == "이분형":
        row.update(e1=a.get("e"), n1=a.get("n"), e2=b.get("e"), n2=b.get("n"))
        if a.get("pct") or b.get("pct"):
            row["_pct"] = True
    elif kind == "생존분석":
        an = _analysis(o, g1, g2, kind)
        if an:
            row.update(hr=an[0], lo=an[1], hi=an[2])
    return row


def _has_numbers(kind, row):
    if kind == "연속형":
        return (None not in (row.get("d"), row.get("lo"), row.get("hi"))
                or None not in (row.get(k) for k in ("n1", "m1", "sd1", "n2", "m2", "sd2")))
    if kind == "이분형":
        return None not in (row.get(k) for k in ("e1", "n1", "e2", "n2"))
    return None not in (row.get(k) for k in ("hr", "lo", "hi"))


# ── 한 임상 → 행 ─────────────────────────────────────────────────────
def _study_info(s):
    ps = s.get("protocolSection", {})
    idm = ps.get("identificationModule", {})
    nct = idm.get("nctId", "")
    title = idm.get("briefTitle", "")
    text = " ".join([title, idm.get("officialTitle", ""), " ".join(ps.get("conditionsModule", {}).get("conditions") or [])])
    flags, sub = [], ""
    el = ps.get("eligibilityModule", {})
    mx = re.match(r"(\d+)\s*year", (el.get("maximumAge") or "").lower())
    if _PEDIA_RE.search(text) or (mx and int(mx.group(1)) < 18):
        flags.append("소아")
    for pat, ko in _REGION:
        if re.search(pat, text, re.I):
            flags.append(f"지역 한정({ko})")
            sub = ko
            break
    return nct, title, text, flags, sub


def _background(text, groups, names):
    """제목에 나오지만 따로 군이 없는 다른 약 = 바탕 치료로 본다."""
    in_groups = set()
    for g in groups:
        in_groups |= drug_words(g.get("title", ""))
    words = {w for w in drug_words(text) if not _same(w, names)}
    common = None
    for g in groups:
        w = {x for x in drug_words(g.get("title", "")) if not _same(x, names)}
        common = w if common is None else common & w
    alone = {w for w in in_groups if any(drug_words(g.get("title", "")) == {w} for g in groups)}
    return sorted((words - alone) | (common or set()))


def side_rows(study, label, names, dose, kind, out_kw, week, other_names, comp_names=None):
    """한 임상 · 한 성분 → (행, 군 정보) 또는 (None, 빠진 이유)."""
    nct, title, text, flags, sub = _study_info(study)
    cands, why = candidates(study, kind, out_kw, week)
    if not cands:
        return None, why
    o, primary = cands[0]
    groups = o.get("groups") or []
    arms = find_arm(groups, names, dose, avoid=other_names)
    pls = find_comparator(groups, names, comp_names) if comp_names else find_placebo(groups, names)
    info = {"nct": nct, "title": title, "outcome": o.get("title", ""), "timeframe": o.get("timeFrame", ""),
            "unit": o.get("unitOfMeasure", ""), "groups": [(g.get("id"), g.get("title", "")) for g in groups],
            "o": _slim(o)}
    row = {"합침": True, "비교": label, "임상번호": nct, "제목": title, "시험군": "", "대조군": "",
           "소그룹": sub, "_flags": list(flags), "_unit": info["unit"], "_g1": "", "_g2": ""}
    if not primary:
        row["_flags"].append("2차 평가변수")
    if kind == "연속형" and "change" not in (o.get("title", "") + o.get("description", "")).lower():
        row["_flags"].append("최종값")
    bg = _background(text, groups, names)
    row["_bg"] = bg
    pairs = [(g, p) for g in arms for p in pls
             if {w for w in _others(g.get("title", ""), names) if not (comp_names and _same(w, comp_names))}
             == {w for w in _others(p.get("title", ""), names) if not (comp_names and _same(w, comp_names))}]
    if not pairs:
        row["합침"] = False
        row["_flags"].append("군 못 찾음")
        return row, info
    if len({g.get("id") for g, _ in pairs}) > 1:
        row["_flags"].append("용량·군 여러 개(첫 군)")
    g1, g2 = pairs[0]
    row.update(시험군=g1.get("title", ""), 대조군=g2.get("title", ""), _g1=g1.get("id"), _g2=g2.get("id"))
    row.update(fill(kind, o, g1.get("id"), g2.get("id")))
    if row.pop("_pct", False):
        row["_flags"].append("비율로 건수 계산")
    if not _has_numbers(kind, row):
        row["합침"] = False
        row["_flags"].append("숫자 못 읽음")
    if "소아" in row["_flags"]:
        row["합침"] = False
    return row, info


def direct_row(study, a_names, a_dose, b_names, b_dose, kind, out_kw, week):
    nct, title, text, flags, sub = _study_info(study)
    cands, _ = candidates(study, kind, out_kw, week)
    for o, primary in cands:
        groups = o.get("groups") or []
        ga = find_arm(groups, a_names, a_dose, avoid=b_names)
        gb = find_arm(groups, b_names, b_dose, avoid=a_names)
        pairs = [(x, y) for x in ga for y in gb if x.get("id") != y.get("id")
                 and {w for w in _others(x.get("title", ""), a_names) if not _same(w, b_names)}
                 == {w for w in _others(y.get("title", ""), b_names) if not _same(w, a_names)}]
        if not pairs:
            continue
        ga, gb = [pairs[0][0]], [pairs[0][1]]
        row = {"합침": True, "비교": "A vs B (직접)", "임상번호": nct, "제목": title,
               "시험군": ga[0].get("title", ""), "대조군": gb[0].get("title", ""), "소그룹": sub,
               "_flags": list(flags) + ([] if primary else ["2차 평가변수"]), "_unit": o.get("unitOfMeasure", ""),
               "_g1": ga[0].get("id"), "_g2": gb[0].get("id"), "_bg": []}
        row.update(fill(kind, o, ga[0].get("id"), gb[0].get("id")))
        row.pop("_pct", None)
        if not _has_numbers(kind, row):
            row["합침"] = False
            row["_flags"].append("숫자 못 읽음")
        info = {"nct": nct, "title": title, "outcome": o.get("title", ""), "timeframe": o.get("timeFrame", ""),
                "unit": o.get("unitOfMeasure", ""), "groups": [(g.get("id"), g.get("title", "")) for g in groups],
                "o": _slim(o)}
        return row, info
    return None, None


def _slim(o):
    """군 바꾸기에 필요한 것만 남긴 평가변수."""
    keep = ("title", "description", "timeFrame", "unitOfMeasure", "paramType", "dispersionType",
            "groups", "denoms", "classes", "analyses", "type")
    return {k: o.get(k) for k in keep if k in o}


def regroup(kind, info, g1, g2):
    """사람이 고른 군으로 숫자 칸을 다시 채운다."""
    row = fill(kind, info["o"], g1, g2)
    row.pop("_pct", None)
    return row


# ── 전체 ─────────────────────────────────────────────────────────────
def comparators_of(study, names, dose, kind, out_kw, week):
    """이 임상에서 그 성분 군과 맞붙은 비교약 이름들 (위약은 '위약')."""
    cands, _ = candidates(study, kind, out_kw, week)
    if not cands:
        return set()
    groups = cands[0][0].get("groups") or []
    if not find_arm(groups, names, dose):
        return set()
    out = set()
    for g in groups:
        t = g.get("title", "")
        if _has(t, names):
            continue
        if _PLACEBO_RE.search(t):          # '위약 + 메트포르민'처럼 바탕 치료가 붙어도 위약으로 센다
            out.add("위약")
            continue
        ws = drug_words(t) - drug_words(" ".join(x.get("title", "") for x in groups if _has(x.get("title", ""), names)))
        if len(ws) == 1:                   # 바탕 치료를 뺀 다른 약이 하나뿐인 군만 (병용군은 안 셈)
            out |= ws
    return out


def search(a_text, b_text, kind, out_kw, week, phases=("3상", "4상"), fetch=fetch_studies, cond="", comp=""):
    """두 성분 → 입력표 초안.

    돌려주는 것: rows(행 목록), infos(임상번호|비교 → 군 정보), skipped(성분별 빠진 이유 개수),
    found(성분별 받은 임상 수), capped(500개에서 끊겼는지), sec(걸린 시간)
    """
    t0 = time.time()
    a, a_dose = parse_drug(a_text)
    b, b_dose = parse_drug(b_text)
    rows, infos, skipped, found, capped = [], {}, {}, {}, False
    comp_count = {"A": Counter(), "B": Counter()}
    studies = {}
    for key, drug, dose, other in (("A", a, a_dose, b), ("B", b, b_dose, a)):
        ss, cut = fetch(drug, phases, out_kw, cond)
        capped = capped or cut
        found[key] = len(ss)
        skipped[key] = Counter()
        for s in ss:
            nct = s.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
            studies.setdefault(nct, s)
            names = aliases(s, drug)
            for c in comparators_of(s, names, dose, kind, out_kw, week):
                comp_count[key][c] += 1
            other_names = aliases(s, other)
            comp_names = (aliases(s, comp.lower().strip()) if comp.strip() else None)
            row, info = side_rows(s, f"{key} vs 위약", names, dose, kind, out_kw, week, other_names, comp_names)
            if row is None:
                skipped[key][info] += 1
                continue
            if "군 못 찾음" in row["_flags"] and not _has(" ".join(t for _, t in info["groups"]), names):
                skipped[key]["그 성분 군이 없음"] += 1
                continue
            rows.append(row)
            infos[f"{row['임상번호']}|{row['비교']}"] = info
    # 같은 임상 안 직접 비교
    for nct, s in studies.items():
        row, info = direct_row(s, aliases(s, a), a_dose, aliases(s, b), b_dose, kind, out_kw, week)
        if row:
            rows.append(row)
            infos[f"{nct}|A vs B (직접)"] = info
    _mark(rows, kind)
    both = {c: (comp_count["A"][c], comp_count["B"][c]) for c in set(comp_count["A"]) | set(comp_count["B"])
            if not _same(c, {a, b})}
    return {"comparators": dict(sorted(both.items(), key=lambda x: (-min(x[1]), -sum(x[1])))),
            "rows": rows, "infos": infos, "skipped": {k: dict(v) for k, v in skipped.items()},
            "found": found, "capped": capped, "sec": time.time() - t0, "a": a, "b": b}


def _mark(rows, kind):
    """여러 임상을 같이 보고 붙이는 표시: A·B에 같이 쓰임, 단위 다름, 바탕 치료 다름."""
    sides = {}
    for r in rows:
        sides.setdefault(r["비교"], []).append(r)
    a_ncts = {r["임상번호"] for r in sides.get("A vs 위약", [])}
    b_ncts = {r["임상번호"] for r in sides.get("B vs 위약", [])}
    for r in rows:
        if r["비교"] == "A vs 위약" and r["임상번호"] in b_ncts:
            r["_flags"].append("B에도 쓰임")
        if r["비교"] == "B vs 위약" and r["임상번호"] in a_ncts:
            r["_flags"].append("A에도 쓰임")
    for side, rs in sides.items():
        ok = [r for r in rs if r["합침"]]
        units = Counter(_unit_key(r["_unit"]) for r in ok if r["_unit"])
        if units and kind == "연속형":
            top = units.most_common(1)[0][0]
            for r in rs:
                if r["_unit"] and _unit_key(r["_unit"]) != top:
                    r["_flags"].append("단위 다름")
                    r["합침"] = False
        bgs = Counter(tuple(r.get("_bg") or ()) for r in ok)
        top_bg = bgs.most_common(1)[0][0] if bgs else ()
        for r in rs:
            bg = tuple(r.get("_bg") or ())
            if bg:
                r["_flags"].append("바탕: " + ", ".join(bg))
            if ok and bg != top_bg and len(bgs) > 1:
                r["_flags"].append("바탕 치료 다름")
    for r in rows:
        r["점검"] = " · ".join(dict.fromkeys(r.pop("_flags"))) or "맞음"
        r.pop("_bg", None)


def _unit_key(u):
    u = (u or "").lower()
    if "%" in u or "percent" in u:
        return "%"
    return re.sub(r"\s+", " ", u).strip()


# ── PubMed ───────────────────────────────────────────────────────────
def _eget(path, params, timeout=20):
    p = dict(params, tool="product-review")
    for i in range(3):
        try:
            return _get(EUTILS + path, p, timeout)
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == 2:          # 너무 자주 물으면 잠깐 쉬었다 다시
                raise
            time.sleep(1.2 * (i + 1))


_NOT_PRIMARY = {"Review", "Systematic Review", "Meta-Analysis", "Comment", "Editorial", "Letter",
                "News", "Published Erratum", "Retraction of Publication"}


def _medline(txt):
    """MEDLINE 글 → [{PMID, TA, DP, PT[], SI[]}]"""
    out = []
    for rec in re.split(r"\n\s*\n", txt):
        d, last = {}, None
        for line in rec.splitlines():
            if len(line) > 6 and line[4:6] == "- ":
                last = line[:4].strip()
                d.setdefault(last, []).append(line[6:].strip())
            elif last and line.startswith("      "):
                d[last][-1] += " " + line.strip()
        if d.get("PMID"):
            out.append(d)
    return out


def pubmed_for_ncts(ncts):
    """임상번호 → (학술지, 연도, PMID). 리뷰·논평을 빼고 가장 먼저 나온 논문(PMID 가 가장 작은 것)."""
    ncts = [n for n in dict.fromkeys(ncts) if re.fullmatch(r"NCT\d{8}", n or "")]
    if not ncts:
        return {}
    want = set(ncts)
    term = " OR ".join(f"{n}[si]" for n in ncts)
    j = json.loads(_eget("esearch.fcgi", {"db": "pubmed", "term": term, "retmax": 3000, "retmode": "json"}))
    ids = sorted({int(x) for x in j.get("esearchresult", {}).get("idlist", [])})
    best = {}
    for start in range(0, min(len(ids), 600), 200):
        time.sleep(0.35)
        txt = _eget("efetch.fcgi", {"db": "pubmed", "id": ",".join(map(str, ids[start:start + 200])),
                                    "rettype": "medline", "retmode": "text"}, 30).decode("utf-8", "replace")
        for d in _medline(txt):
            if set(d.get("PT", [])) & _NOT_PRIMARY:
                continue
            found = set(re.findall(r"NCT\d{8}", " ".join(d.get("SI", [])))) & want
            for n in found:
                if n not in best:
                    best[n] = ((d.get("TA") or [""])[0], (d.get("DP") or [""])[0][:4], d["PMID"][0])
        if len(best) == len(want):
            break
    return best


def pubmed_search(query, top_only=True, rct_only=True, n=20):
    """PubMed 에서 논문 찾기 → [{pmid, year, journal, title, pmc}]"""
    term = f"({query})"
    if top_only:
        term += " AND (" + " OR ".join(f'"{j}"[ta]' for j in TOP_JOURNALS) + ")"
    if rct_only:
        term += " AND randomized controlled trial[pt]"
    j = json.loads(_eget("esearch.fcgi", {"db": "pubmed", "term": term, "retmax": n, "retmode": "json"}))
    ids = j.get("esearchresult", {}).get("idlist", [])
    total = int(j.get("esearchresult", {}).get("count", 0))
    if not ids:
        return total, []
    res = json.loads(_eget("esummary.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "json"})).get("result", {})
    out = []
    for i in ids:
        d = res.get(i, {})
        pmc = next((a.get("value") for a in d.get("articleids", []) if a.get("idtype") == "pmc"), "")
        out.append({"pmid": i, "year": (d.get("pubdate") or "")[:4], "journal": d.get("source", ""),
                    "title": d.get("title", ""), "pmc": pmc})
    return total, out


def pubmed_link(nct=None, pmid=None):
    if pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    if nct:
        return f"https://pubmed.ncbi.nlm.nih.gov/?term={nct}%5Bsi%5D"
    return ""
