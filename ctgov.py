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
    "derivedSection.conditionBrowseModule.meshes",
    "protocolSection.eligibilityModule",
    "protocolSection.armsInterventionsModule.interventions",
    "resultsSection.outcomeMeasuresModule",
])
MAX_PAGES = 5            # 성분 하나당 최대 500개 임상
SIDE_A, SIDE_B, SIDE_D = "A vs 비교군", "B vs 비교군", "A vs B (직접)"
PLACEBO = "위약"
MAX_COMPS = 6            # 비교약 목록은 임상이 많은 순으로 최대 6개까지 표를 만들어 둔다
MAX_EP = 15              # 한 임상에서 읽는 평가변수 묶음 수 상한
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


# ── 평가변수·질환 묶기 (AI 없음, 정해진 규칙) ─────────────────────────
# 같은 척도·같은 방식(변화량/변화율/최종값)·같은 시점끼리만 한 묶음으로 합친다.
# 척도 사전은 위에서부터 먼저 맞는 것을 쓴다(구체적인 것을 위에 둔다).
_SCALES = [
    ("DAS28-CRP", r"das\s*-?\s*28.{0,30}(crp|c-?\s*reactive)|disease activity score.{0,90}(crp|c-?\s*reactive)"
                  r"|crp.{0,12}das\s*-?\s*28"),
    ("DAS28-ESR", r"das\s*-?\s*28.{0,30}(esr|erythrocyte)|disease activity score.{0,90}(esr|erythrocyte)"
                  r"|esr.{0,12}das\s*-?\s*28"),
    ("DAS28 (CRP·ESR 미표기)", r"das\s*-?\s*28|disease activity score\s*-?\s*28"),
    ("CDAI (크론병)", r"crohn'?s disease activity index"),
    ("CDAI", r"\bcdai\b|clinical disease activity index"),
    ("SDAI", r"\bsdai\b|simplified disease activity"),
    ("HAQ-DI", r"\bhaq\b|haq-?di|health assessment questionnaire"),
    ("mTSS (관절 손상)", r"\bmtss\b|modified total sharp|sharp score"),
    ("SF-36", r"sf-?\s*36|short[- ]form.{0,8}36|36-item short"),
    ("아침 강직", r"morning (joint )?stiffness"),
    ("피로", r"fatigue|tiredness|facit"),
    ("환자 전반 평가", r"patient'?s?\s+global assessment|\bptga\b|\bpatga\b"),
    ("ACR20 반응", r"\bacr\s*-?\s*20\b|american college of rheumatology.{0,40}\b20\b"),
    ("ACR50 반응", r"\bacr\s*-?\s*50\b|american college of rheumatology.{0,40}\b50\b"),
    ("ACR70 반응", r"\bacr\s*-?\s*70\b|american college of rheumatology.{0,40}\b70\b"),
    ("HbA1c", r"hba1c|hb\s*a1c|\ba1c\b|glyc(at|osyl)ated\s+h(a)?emoglobin|h(a)?emoglobin\s+a1c"),
    ("공복 혈당", r"fasting (plasma |blood |serum )?glucose|\bfpg\b"),
    ("식후 혈당", r"post-?prandial (plasma )?glucose|\bppg\b"),
    ("BMI", r"body mass index|\bbmi\b"),
    ("허리둘레", r"waist circumference"),
    ("체중", r"body weight|\bweight\b"),
    ("Non-HDL-C", r"non-?\s*hdl"),
    ("LDL-C", r"\bldl|low[- ]density lipoprotein"),
    ("HDL-C", r"\bhdl|high[- ]density lipoprotein"),
    ("중성지방", r"triglyceride"),
    ("총콜레스테롤", r"total cholesterol"),
    ("ApoB", r"\bapo(lipoprotein)?\s*-?\s*b\b"),
    ("Lp(a)", r"lipoprotein\s*\(a\)|\blp\s*\(a\)"),
    ("수축기 혈압", r"systolic"),
    ("이완기 혈압", r"diastolic"),
    ("UACR", r"albumin[- ]to[- ]creatinine|\buacr\b"),
    ("eGFR", r"\begfr\b|glomerular filtration"),
    ("혈청 크레아티닌", r"serum creatinine"),
    ("칼륨", r"potassium"),
    ("요산", r"uric acid|serum urate|\bsua\b"),
    ("FEV1", r"\bfev\s*-?\s*1|forced expiratory volume"),
    ("FVC", r"\bfvc\b|forced vital capacity"),
    ("최대 호기 유량", r"peak expiratory flow|\bpef\b"),
    ("악화", r"exacerbation"),
    ("ACQ", r"\bacq\b|acq-?\d|asthma control questionnaire"),
    ("SGRQ", r"sgrq|st\.? george"),
    ("CAT", r"copd assessment test|\bcat\b score"),
    ("PASI 100", r"pasi\s*-?\s*100"),
    ("PASI 90", r"pasi\s*-?\s*90"),
    ("PASI 75", r"pasi\s*-?\s*75"),
    ("PASI", r"\bpasi\b|psoriasis area"),
    ("EASI 90", r"easi\s*-?\s*90"),
    ("EASI 75", r"easi\s*-?\s*75"),
    ("EASI 50", r"easi\s*-?\s*50"),
    ("EASI", r"\beasi\b|eczema area"),
    ("sPGA", r"\bspga\b|static physician"),
    ("IGA", r"\biga\b|investigator'?s? global assessment"),
    ("DLQI", r"dlqi|dermatology life quality"),
    ("가려움", r"\bitch|prurit"),
    ("MADRS", r"madrs|montgomery"),
    ("HAM-A", r"ham-?a\b|hamilton anxiety"),
    ("HAM-D", r"ham-?d\b|hamd|hamilton (rating scale for )?depression"),
    ("PANSS", r"panss|positive and negative syndrome"),
    ("CGI-S", r"cgi-?s\b|clinical global impression.{0,20}severity"),
    ("CGI-I", r"cgi-?i\b|clinical global impression.{0,20}improvement"),
    ("YMRS", r"ymrs|young mania"),
    ("ADAS-Cog", r"adas-?cog"),
    ("MMSE", r"mmse|mini-?mental"),
    ("CDR-SB", r"cdr-?sb|clinical dementia rating"),
    ("UPDRS", r"updrs"),
    ("WOMAC 통증", r"womac.{0,25}pain"),
    ("WOMAC 기능", r"womac.{0,30}function"),
    ("WOMAC", r"womac"),
    ("통증 강도", r"pain (intensity|score|nrs|vas)|\bnrs\b.{0,15}pain|visual analog(ue)? scale.{0,25}pain|average pain"),
    ("IPSS", r"ipss|international prostate symptom"),
    ("최대 요속", r"qmax|maximum (urinary )?flow"),
    ("배뇨 횟수", r"micturition"),
    ("요절박", r"urgency"),
    ("요실금", r"incontinence"),
    ("골밀도 요추", r"(bmd|bone mineral density).{0,40}(lumbar|spine)"),
    ("골밀도 고관절", r"(bmd|bone mineral density).{0,40}total hip"),
    ("골밀도 대퇴경부", r"(bmd|bone mineral density).{0,40}femoral neck"),
    ("골밀도", r"\bbmd\b|bone mineral density"),
    ("월 편두통 일수", r"migraine days"),
    ("월 두통 일수", r"headache days"),
    ("Mayo 점수", r"\bmayo\b"),
    ("자발 배변", r"spontaneous bowel movement|\bc?sbms?\b"),
    ("좌심실 박출률", r"ejection fraction|\blvef\b"),
    ("NT-proBNP", r"nt-?\s*pro-?\s*bnp"),
    ("6분 보행", r"6-?\s*minute walk|six[- ]minute walk|6mwd|6mwt"),
    ("혈색소", r"h(a)?emoglobin"),
    ("안압", r"intraocular pressure|\biop\b"),
    ("최대 교정 시력", r"bcva|best[- ]corrected visual acuity"),
    ("SVR12", r"\bsvr\s*-?\s*12|sustained virolog"),
    ("HIV RNA", r"hiv-?1?\s*rna"),
    ("안면홍조", r"hot flash|hot flush|vasomotor"),
    ("의사 전반 평가", r"physician'?s?\s+global assessment|\bphga\b"),
    ("CRP", r"\bhs-?crp\b|\bcrp\b|c-?\s*reactive protein"),
]
_SCALES_RE = [(lab, re.compile(rx, re.I)) for lab, rx in _SCALES]
_TIME_RE = re.compile(r"(?:(\d+(?:\.\d+)?)\s*-?\s*(weeks?|wks?|months?|years?|days?)\b"
                      r"|\b(weeks?|wks?|months?|years?|days?)\s*(\d+(?:\.\d+)?))", re.I)
_TIME_WK = {"w": 1, "m": 52 / 12, "y": 52, "d": 1 / 7}
_EP_STRIP = re.compile(
    r"\b(?:(?:least[- ]squares?\s+)?mean\s+)?(?:percent(?:age)?\s+)?change(?:s)?\s+(?:from|since)\s+baseline"
    r"(?:\s+(?:in|to|of|at))?|\b(?:at|to|through|by|up to|over|after)?\s*(?:weeks?|wks?|months?|years?|days?)\s*\d+"
    r"(?:\.\d+)?|\b\d+(?:\.\d+)?\s*-?\s*(?:weeks?|wks?|months?|years?|days?)\b|\bbaseline\b", re.I)
_EP_STOP = {"in", "the", "of", "at", "to", "a", "an", "and", "with", "on", "for", "by", "from", "score", "scores",
            "total", "mean", "change", "changes", "percent", "percentage", "participants", "number", "proportion",
            "who", "had", "were", "was", "achieving", "achieved", "with", "least", "squares", "ls", "level",
            "levels", "value", "values", "measured", "as", "during", "period", "treatment", "versus", "vs"}


_RECALL_RE = re.compile(r"\b(?:prior|past|previous|last|preceding|over the)\s+\d+\s*-?\s*(?:days?|weeks?|hours?)", re.I)


def weeks_of(text):
    """'12 weeks'·'Week 12'·'Month 6' → 주. 여러 개면 마지막 값. 못 읽으면 None.
    'in the prior 7 days' 같은 회상 기간은 시점이 아니므로 뺀다."""
    hits = _TIME_RE.findall(_RECALL_RE.sub(" ", text or ""))
    if not hits:
        return None
    n1, u1, u2, n2 = hits[-1]
    n, u = (n1, u1) if n1 else (n2, u2)
    return round(float(n) * _TIME_WK[u[0].lower()], 1)


def _ep_core(title):
    """사전에 없는 지표: 군·시점·'change from baseline' 글자를 지운 핵심어 (순서 유지)."""
    t = re.split(r"\s+-\s+|\s+:\s+|\bversus\b|\bvs\.?\s", title or "", flags=re.I)[0]
    t = _EP_STRIP.sub(" ", t)
    words = []
    for w in re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", t):
        lw = w.lower()
        if lw in _EP_STOP or lw in [x.lower() for x in words] or drug_words(lw) or re.fullmatch(r"\d+(mg)?", lw):
            continue
        words.append(w)
    return words


def ep_group(o, kind):
    """평가변수 → (묶음 키, 묶음 이름). 같은 척도·방식·시점이면 같은 키."""
    title = o.get("title") or ""
    low = title.lower()
    scale = next((lab for lab, rx in _SCALES_RE if rx.search(title)), None)
    if scale:
        skey, sname = scale, scale
    else:
        core = _ep_core(title)
        skey = "~" + " ".join(sorted(w.lower() for w in core)) if core else "~" + low[:60]
        sname = " ".join(core)[:60] or title[:60] or "(이름 없음)"
    if kind == "연속형":
        if re.search(r"percent(age)?\s+change|%\s*change|percent(age)?\s+reduction|percent(age)? decrease", low):
            mode = "변화율(%)"
        elif re.search(r"change|reduction|decrease|increase|difference from baseline", low):
            mode = "변화량"
        else:
            mode = "최종값"
    elif kind == "이분형":
        mode = "건수"
    else:
        mode = "위험비"
    wk = weeks_of(title) or weeks_of(o.get("timeFrame"))
    wtxt = f"{wk:g}주" if wk is not None else "시점 미상"
    return f"{skey}|{mode}|{wtxt}", f"{sname} · {mode} · {wtxt}"


def diseases_of(study):
    """임상의 표준 병명(MeSH). 없으면 등록 질환명에서 괄호를 뗀 것."""
    meshes = (study.get("derivedSection", {}).get("conditionBrowseModule", {}).get("meshes") or [])
    terms = [m.get("term") for m in meshes if m.get("term")]
    if not terms:
        conds = study.get("protocolSection", {}).get("conditionsModule", {}).get("conditions") or []
        terms = [re.sub(r"\s*\([^)]*\)", "", c).strip().title() for c in conds]
    out = []
    for t in terms:
        if t and t not in out:
            out.append(t)
    return out or ["(질환 등록 없음)"]


def disease_options(rows):
    """[(대표 질환, A 임상 수, B 임상 수, 직접 비교 수)] — 대표 질환 = 임상의 첫 표준 병명. 많은 순."""
    first = {}
    for r in rows:
        first.setdefault(r["임상번호"], r["_dis"][0])
    out = []
    for d in dict.fromkeys(first.values()):
        cnt = {SIDE_A: set(), SIDE_B: set(), SIDE_D: set()}
        for r in rows:
            if d in r["_dis"]:
                cnt[r["비교"]].add(r["임상번호"])
        out.append((d, len(cnt[SIDE_A]), len(cnt[SIDE_B]), len(cnt[SIDE_D])))
    nfirst = Counter(first.values())
    out.sort(key=lambda x: (-nfirst[x[0]], -(x[1] + x[2] + x[3]), x[0]))
    return out


def group_options(rows, dis):
    """고른 질환 안의 평가변수 묶음 목록. 고를 수 있는 묶음(A·B 양쪽 또는 직접 비교가 있음)을 먼저,
    그 안에서는 1차 평가변수 줄이 많은 순."""
    by = {}
    for r in rows:
        if dis is not None and dis not in r["_dis"]:
            continue
        g = by.setdefault(r["_grp"], {"key": r["_grp"], "label": r["묶음"], SIDE_A: set(), SIDE_B: set(),
                                      SIDE_D: set(), "prim": 0, "ex": []})
        g[r["비교"]].add(r["임상번호"])
        g["prim"] += r.get("구분") == "1차"
        t = r.get("평가변수") or ""
        if t and t not in g["ex"]:
            g["ex"].append(t)
    out = []
    for g in by.values():
        na, nb, nd = len(g[SIDE_A]), len(g[SIDE_B]), len(g[SIDE_D])
        out.append({"key": g["key"], "label": g["label"], "a": na, "b": nb, "d": nd, "prim": g["prim"],
                    "ok": bool((na and nb) or nd), "ex": g["ex"]})
    out.sort(key=lambda g: (not g["ok"], -g["prim"], -(g["a"] + g["b"] + g["d"]), g["label"]))
    return out


def pick_rows(rows, dis, grp):
    return [r for r in rows if r["_grp"] == grp and (dis is None or dis in r["_dis"])]


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


def dose_of(title):
    """군 이름에서 용량 글자 (예: '2 mg'). 없으면 ''."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(mg|mcg|µg|μg|g|iu|unit)s?\b", (title or "").lower())
    return f"{m.group(1)} {m.group(2)}" if m else ""


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


def _endpoint(study, o, primary):
    """표에 보여 줄 질환·평가변수 칸."""
    conds = (study.get("protocolSection", {}).get("conditionsModule", {}).get("conditions") or [])
    cond = ", ".join(conds[:3]) + (f" 외 {len(conds) - 3}" if len(conds) > 3 else "")
    return {"질환": cond[:120], "평가변수": (o.get("title") or "")[:200], "시점": (o.get("timeFrame") or "")[:120],
            "단위": (o.get("unitOfMeasure") or "")[:60], "구분": "1차" if primary else "2차"}


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


def side_rows(study, label, names, dose, kind, out_kw, week, other_names, comp_names=None, cand=None):
    """한 임상 · 한 성분 → (행, 군 정보) 또는 (None, 빠진 이유). cand=(평가변수, 1차여부)를 주면 그것을 쓴다."""
    nct, title, text, flags, sub = _study_info(study)
    if cand is None:
        cands, why = candidates(study, kind, out_kw, week)
        if not cands:
            return None, why
        cand = cands[0]
    o, primary = cand
    groups = o.get("groups") or []
    arms = find_arm(groups, names, dose, avoid=other_names)
    pls = find_comparator(groups, names, comp_names) if comp_names else find_placebo(groups, names)
    info = {"nct": nct, "title": title, "outcome": o.get("title", ""), "timeframe": o.get("timeFrame", ""),
            "unit": o.get("unitOfMeasure", ""), "groups": [(g.get("id"), g.get("title", "")) for g in groups],
            "o": _slim(o)}
    row = {"합침": True, "비교": label, "임상번호": nct, "제목": title, "시험군": "", "대조군": "",
           "시험군 용량": "", "대조군 용량": "",
           "소그룹": sub, "_flags": list(flags), "_unit": info["unit"], "_g1": "", "_g2": ""}
    row.update(_endpoint(study, o, primary))
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
    row.update(시험군=g1.get("title", ""), 대조군=g2.get("title", ""), _g1=g1.get("id"), _g2=g2.get("id"),
               **{"시험군 용량": dose_of(g1.get("title", "")), "대조군 용량": dose_of(g2.get("title", ""))})
    if comp_names and not (row["시험군 용량"] and row["대조군 용량"]):
        row["_flags"].append("용량 미표기")
    row.update(fill(kind, o, g1.get("id"), g2.get("id")))
    if row.pop("_pct", False):
        row["_flags"].append("비율로 건수 계산")
    if not _has_numbers(kind, row):
        row["합침"] = False
        row["_flags"].append("숫자 못 읽음")
    if "소아" in row["_flags"]:
        row["합침"] = False
    return row, info


def direct_rows(study, a_names, a_dose, b_names, b_dose, kind, out_kw, week):
    """같은 임상 안 A vs B → 평가변수 묶음마다 한 줄 [(행, 군 정보, 묶음 키, 묶음 이름)]."""
    out, seen = [], set()
    cands, _ = candidates(study, kind, out_kw, week)
    for o, primary in cands:
        gk, gl = ep_group(o, kind)
        if gk in seen or len(seen) >= MAX_EP:
            continue
        r = _direct_one(study, o, primary, a_names, a_dose, b_names, b_dose, kind)
        if r:
            seen.add(gk)
            out.append(r + (gk, gl))
    return out


def _direct_one(study, o, primary, a_names, a_dose, b_names, b_dose, kind):
    nct, title, text, flags, sub = _study_info(study)
    groups = o.get("groups") or []
    ga = find_arm(groups, a_names, a_dose, avoid=b_names)
    gb = find_arm(groups, b_names, b_dose, avoid=a_names)
    pairs = [(x, y) for x in ga for y in gb if x.get("id") != y.get("id")
             and {w for w in _others(x.get("title", ""), a_names) if not _same(w, b_names)}
             == {w for w in _others(y.get("title", ""), b_names) if not _same(w, a_names)}]
    if not pairs:
        return None
    ga, gb = [pairs[0][0]], [pairs[0][1]]
    row = {"합침": True, "비교": SIDE_D, "임상번호": nct, "제목": title,
           "시험군": ga[0].get("title", ""), "대조군": gb[0].get("title", ""), "소그룹": sub,
           "시험군 용량": dose_of(ga[0].get("title", "")), "대조군 용량": dose_of(gb[0].get("title", "")),
           "_flags": list(flags) + ([] if primary else ["2차 평가변수"]), "_unit": o.get("unitOfMeasure", ""),
           "_g1": ga[0].get("id"), "_g2": gb[0].get("id"), "_bg": []}
    row.update(_endpoint(study, o, primary))
    row.update(fill(kind, o, ga[0].get("id"), gb[0].get("id")))
    row.pop("_pct", None)
    if not _has_numbers(kind, row):
        row["합침"] = False
        row["_flags"].append("숫자 못 읽음")
    info = {"nct": nct, "title": title, "outcome": o.get("title", ""), "timeframe": o.get("timeFrame", ""),
            "unit": o.get("unitOfMeasure", ""), "groups": [(g.get("id"), g.get("title", "")) for g in groups],
            "o": _slim(o)}
    return row, info


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


def search_all(a_text, b_text, kind, out_kw, week, phases=("3상", "4상"), fetch=fetch_studies, cond=""):
    """두 성분 임상을 한 번 받아서, 공통 비교약 목록과 비교약마다의 입력표를 만든다.

    돌려주는 것:
      comparators: [(이름, A 임상 수, B 임상 수, 쓸 수 있나)] — 쓸 수 있는 것(양쪽 다 있음)부터, 임상 많은 순
      default: 기본으로 고를 비교약 (양쪽 다 있는 것 중 임상이 가장 많은 것, 같으면 위약)
      by: {비교약: {rows, infos, skipped}} — 같은 임상 안 직접 비교 줄은 모든 비교약 표에 같이 들어감
      found, capped, sec, a, b
    """
    t0 = time.time()
    a, a_dose = parse_drug(a_text)
    b, b_dose = parse_drug(b_text)
    side_studies, found, capped = {}, {}, False
    comp_count = {"A": Counter(), "B": Counter()}
    studies = {}
    for key, drug, dose in (("A", a, a_dose), ("B", b, b_dose)):
        ss, cut = fetch(drug, phases, out_kw, cond)
        capped = capped or cut
        found[key] = len(ss)
        side_studies[key] = ss
        for s in ss:
            nct = s.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
            studies.setdefault(nct, s)
            for c in comparators_of(s, aliases(s, drug), dose, kind, out_kw, week):
                if not _same(c, {a, b}):
                    comp_count[key][c] += 1
    names = set(comp_count["A"]) | set(comp_count["B"]) | {PLACEBO}
    comps = [(c, comp_count["A"][c], comp_count["B"][c]) for c in names]
    comps = [(c, na, nb, bool(na and nb)) for c, na, nb in comps]
    comps.sort(key=lambda x: (not x[3], -min(x[1], x[2]), x[0] != PLACEBO, -(x[1] + x[2]), x[0]))
    usable = [c for c, _, _, ok in comps if ok]
    default = usable[0] if usable else PLACEBO

    labels = {}

    def _tag(row, s, gk, gl):
        labels.setdefault(gk, gl)
        row["_grp"], row["묶음"], row["_dis"] = gk, labels[gk], diseases_of(s)

    direct, dinfos = [], {}
    for nct, s in studies.items():
        for row, info, gk, gl in direct_rows(s, aliases(s, a), a_dose, aliases(s, b), b_dose, kind, out_kw, week):
            _tag(row, s, gk, gl)
            direct.append(row)
            dinfos[f"{nct}|{SIDE_D}|{gk}"] = info

    by = {}
    for c in ([PLACEBO] if PLACEBO not in usable else []) + usable[:MAX_COMPS]:
        rows, infos, skipped = [], {}, {}
        for key, drug, dose, other, label in (("A", a, a_dose, b, SIDE_A), ("B", b, b_dose, a, SIDE_B)):
            skipped[key] = Counter()
            for s in side_studies[key]:
                cands, why = candidates(s, kind, out_kw, week)
                if not cands:
                    skipped[key][why] += 1
                    continue
                nm = aliases(s, drug)
                oth = aliases(s, other)
                comp_names = None if c == PLACEBO else aliases(s, c)
                seen, got, miss = set(), 0, None
                for o, primary in cands:
                    gk, gl = ep_group(o, kind)
                    if gk in seen:
                        continue
                    if len(seen) >= MAX_EP:
                        break
                    seen.add(gk)
                    row, info = side_rows(s, label, nm, dose, kind, out_kw, week, oth, comp_names, cand=(o, primary))
                    if "군 못 찾음" in row["_flags"]:
                        titles = [t for _, t in info["groups"]]
                        if not _has(" ".join(titles), nm):
                            miss = "그 성분 군이 없음"
                            continue
                        has_comp = (any(_PLACEBO_RE.search(t) for t in titles) if c == PLACEBO
                                    else any(_has(t, comp_names) and not _has(t, nm) for t in titles))
                        if not has_comp:
                            miss = f"{c} 군이 없음"
                            continue
                    _tag(row, s, gk, gl)
                    rows.append(row)
                    infos[f"{row['임상번호']}|{row['비교']}|{gk}"] = info
                    got += 1
                if not got:
                    skipped[key][miss or "군 못 찾음"] += 1
        rows += [dict(r, _flags=list(r["_flags"])) for r in direct]
        infos.update(dinfos)
        grp_rows = {}
        for r in rows:
            grp_rows.setdefault(r["_grp"], []).append(r)
        for rs in grp_rows.values():
            _mark(rs, kind)
        by[c] = {"rows": rows, "infos": infos, "skipped": {k: dict(v) for k, v in skipped.items()}}
    return {"comparators": comps, "default": default, "by": by, "found": found, "capped": capped,
            "sec": time.time() - t0, "a": a, "b": b, "v": 3}


def search(a_text, b_text, kind, out_kw, week, phases=("3상", "4상"), fetch=fetch_studies, cond="", comp=""):
    """비교약 하나만 골라 쓰는 예전 모양 (시험 로봇용). comp 를 비우면 기본 비교약."""
    res = search_all(a_text, b_text, kind, out_kw, week, phases, fetch, cond)
    c = (comp or "").strip().lower() or res["default"]
    if c not in res["by"]:
        c = res["default"]
    out = dict(res["by"][c])
    out.update({k: res[k] for k in ("comparators", "default", "found", "capped", "sec", "a", "b")}, comp=c)
    dopts = disease_options(out["rows"])
    dis = dopts[0][0] if dopts else None
    gopts = group_options(out["rows"], dis)
    grp = gopts[0]["key"] if gopts else None
    out.update(diseases=dopts, dis=dis, groups=gopts, grp=grp, all_rows=out["rows"],
               rows=pick_rows(out["rows"], dis, grp) if grp else [])
    return out


def _mark(rows, kind):
    """여러 임상을 같이 보고 붙이는 표시: A·B에 같이 쓰임, 단위 다름, 바탕 치료 다름."""
    sides = {}
    for r in rows:
        sides.setdefault(r["비교"], []).append(r)
    a_ncts = {r["임상번호"] for r in sides.get(SIDE_A, [])}
    b_ncts = {r["임상번호"] for r in sides.get(SIDE_B, [])}
    for r in rows:
        if r["비교"] == SIDE_A and r["임상번호"] in b_ncts:
            r["_flags"].append("B에도 쓰임")
        if r["비교"] == SIDE_B and r["임상번호"] in a_ncts:
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
