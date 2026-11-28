"""약가 산정 (v2). 지금 사이트 scouting.py 의 약가 산정 부분을 그대로 옮겼다(계산 규칙·화면 같음).
이 화면은 아직 약가 표를 공유 로더(scout_sources, 한 벌만)로 읽는다 — 다음 단계에서 DuckDB 로 가볍게 바꾼다."""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import scout_sources as ss

TODAY = pd.Timestamp.today().normalize()
HAS_PRICE = ss.price_available()
HAS_REG = ss.available()

_WON_AXIS = dict(title="상한금액(원)", tickformat=",d", separatethousands=True)

# ── 약가 산정 ───────────────────────────────────────────────────────────────
# 산정 비율(제도 바뀌면 여기만 고치면 된다). 기준가는 '같은 제제 중 최고가'.
CALC_RATE = {"salt": .90, "salt_adj": 1.00, "dose": 1.00, "dose_adj": 1.10,
             "addon": .70, "improved": .70, "dataonly": .60, "after": .45,
             "gen_base": .45, "gen_innov": .60, "gen_semi": .50,
             "combo_base": .45, "combo_imp": .60, "combo_innov": .68}
# ── 2026.8 개정고시 ─────────────────────────────────────────────────────
# 기준요건(DMF·자체생동) 충족 수에 따른 산정률. 미충족 1개마다 20%씩 내린다
# (1개 = 45% × 0.8 = 36% · 0개 = 45% × 0.8 × 0.8 = 28.8%).
REQ_RATE = {2: .45, 1: .36, 0: .288}
STEP_RATE = .29          # 계단식은 기준요건과 무관하게 29% 기준
AFTER_CUT = .85          # 다품목 1년 후 · 계단식 모두 85%
AFTER_ADDON = .45        # 가산이 끝나면 요건률이 아니라 45% 로 유지된다
MP_TOTAL = 14            # 기등재 + 동시신청 합계가 이 수 이상이면 다품목 등재관리
STEP_PRIOR = 13          # 기등재가 이 수 이상이면 계단식 약가인하

# ⑤ 함량배수: 자사제품이 없으면 최근접 함량 최고가의 45%(마약·생물 70%)가 A.
DOSE_A = .45
DOSE_A_SPECIAL = .70

# ① 단일제 자료제출의 세 갈래 (오리지널의 조정 상태).
OSTATE = ["조정 전 — 이번 등재로도 오리지널 조정이 일어나지 않음",
          "동시 조정 — 이번 등재와 함께 오리지널이 조정됨",
          "이미 조정됨 — 오리지널이 전에 조정을 마친 상태"]

# ── ① 자료제출 산정 (2026.8 개정 · 펙소페나딘 구강붕해정 표로 검산) ──────────
ADJ_RATIO = .45       # 현행 조정 비율: 조정된 금액 = 조정 전 기준가(P) × 45%
REQ_CUT = .80         # 기준요건(DMF·자체생동) 미충족 1개마다 20% 인하
OLD_RATIO = .5355     # 재평가 시행 전 기존 품목의 비율 (이미 조정됨: 상한가 ÷ 이 값 = P)
# 기존 품목 재평가 — 상한가를 나누는 비율. [(적용 시작 (연, 월)), 비율] · 그 전은 53.55%.
# ※ 2027년 4월부터 적용 (1차 2027.4~ · 2차 2030.10~). 47·49% 구간은 특례.
REEVAL = {
    "1차": {"일반": [((2027, 4), .51), ((2028, 4), .49), ((2029, 4), .47), ((2030, 4), .45)],
            "준혁신형": [((2027, 4), .51), ((2028, 4), .49), ((2029, 4), .47), ((2033, 4), .45)],
            "혁신형": [((2027, 4), .51), ((2028, 4), .49), ((2033, 4), .45)]},
    "2차": {"일반": [((2030, 10), .51), ((2031, 10), .49), ((2032, 10), .47), ((2033, 10), .45)],
            "준혁신형": [((2030, 10), .51), ((2031, 10), .49), ((2032, 10), .47), ((2036, 10), .45)],
            "혁신형": [((2030, 10), .51), ((2031, 10), .49), ((2036, 10), .45)]},
}


def reeval_ratio(cha, co, when):
    """상한가를 나누는 비율. when 은 상한가의 시점(날짜). 차수 시행 전이면 53.55%."""
    r = OLD_RATIO
    try:
        ym = (int(when.year), int(when.month))
    except Exception:
        return r
    for start, v in REEVAL.get(cha, {}).get(co, []):
        if ym >= start:
            r = v
    return r


def low_cap(prev, new, cheap):
    """조정(인하) 때 저가의약품 기준금액 아래로 내려가지 않는다.
    조정 전 금액이 이미 기준금액 이하였으면 인하 없이 그 값을 유지한다."""
    if not cheap or new >= prev or new >= cheap:
        return new
    return cheap if prev > cheap else prev


def pct(x):
    """0.5355 → '53.55%', 0.45 → '45%', 0.288 → '28.8%'."""
    return f"{float(x) * 100:.2f}".rstrip("0").rstrip(".") + "%"

# 저가의약품 기준금액(원). 계단식에서 '이 값 이하면 인하 없이 최저가와 동일가'.
# 실제 고시 자료 22,062 품목 중 22,058 개(99.98%)가 투여·단위로 가려진다.
CHEAP = {"내복제(일반)": 77, "내복 액상제": 165, "외용제(일반)": 1100,
         "1회용 외용제(관포장)": 165, "주사제": 1000}

def cheap_kind(route, unit):
    """투여·단위로 저가의약품 분류를 고른다. 못 가리면 None."""
    r, u = str(route or "").strip(), str(unit or "")
    if r == "주사":
        return "주사제"
    if r == "내복":
        return "내복 액상제" if "mL" in u else "내복제(일반)"
    if r == "외용":
        return "1회용 외용제(관포장)" if "관" in u else "외용제(일반)"
    return None

def dose_b(mg_new, mg_ref):
    """함량배수 산식의 B = {(높은함량/낮은함량 - 1) × 0.5} + 1.
    늘 높은쪽/낮은쪽으로 잡으므로 B 는 항상 1 이상이고, 신청 함량이 많으면
    곱하고 적으면 나눈다. 함량 차이의 절반만 값에 반영되는 셈이다."""
    a, b = float(mg_new), float(mg_ref)
    if a <= 0 or b <= 0:
        return 1.0
    return ((max(a, b) / min(a, b)) - 1) * 0.5 + 1


def calc_route(prior, applying):
    """일반 / 다품목 / 계단식 중 어디에 해당하나. prior 는 오리지널을 포함해 센다."""
    if prior >= STEP_PRIOR:
        return "계단식"
    if prior + applying >= MP_TOTAL:
        return "다품목"
    return "일반"

_DOSE_RE = re.compile(r"([\d.]+)\s*(mg|mcg|ug|g|ml|iu|%)", re.I)


def dose_of(text):
    """성분명에서 함량과 단위를 뽑는다.
    'atorvastatin calcium (as atorvastatin 10mg)' → (10.0, 'mg')"""
    got = _DOSE_RE.findall(str(text or ""))
    if not got:
        return None, ""
    v, u = got[-1]
    try:
        return float(v), u.lower()
    except ValueError:
        return None, ""


def dose_core(text):
    """함량과 괄호를 떼어낸 성분 이름. 함량만 다른 묶음끼리 짝지을 때 쓴다."""
    s = re.sub(r"\(.*?\)", " ", str(text or ""))
    s = _DOSE_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


@st.cache_data(show_spinner="제품 목록 준비 중…")
def _price_products():
    """급여 품목의 최신 상한금액 목록. ⑤ 함량배수에서 '비교 제품'을 제품명으로
    찾게 하려고 쓴다. 신청 제품은 아직 없으므로, 찾아야 하는 것은 비교 제품이다."""
    pr = ss.load_slim("price", ["제품코드", "제품명", "제제코드", "업체명",
                                "급여구분", "적용일자", "금액"])
    if pr is None or "제제코드" not in pr.columns:
        return pd.DataFrame()
    sub = pr
    if "급여구분" in sub.columns:
        sub = sub[sub["급여구분"] == "급여"]
    if sub.empty:
        return pd.DataFrame()
    sub = sub.assign(적용일자=pd.to_datetime(sub["적용일자"], errors="coerce"))
    out = sub.dropna(subset=["적용일자"]).sort_values("적용일자").groupby("제품코드").tail(1)
    out["금액"] = pd.to_numeric(out["금액"], errors="coerce")
    ss.release_memory()
    return out.dropna(subset=["금액"])[
        ["제품코드", "제품명", "업체명", "제제코드", "금액"]].reset_index(drop=True)


@st.cache_data(show_spinner="같은 성분의 다른 함량 품목을 찾는 중…", max_entries=64)
def _dose_peers(code):
    """같은 성분·투여경로의 품목 목록(함량별). 함량배수 산식의 '비교 제품' 후보다.

    고시가 '가장 근접한 함량 제품의 상한금액'이라고 하므로 묶음이 아니라
    품목 단위로 고르게 한다. 자사제품이 있으면 그 품목을, 없으면 최근접
    함량 품목 중 최고가를 A 로 쓴다.
    """
    g = ss.price_groups()
    if g.empty:
        return pd.DataFrame()
    me = g[g["제제코드"].astype(str) == str(code)]
    if me.empty:
        return pd.DataFrame()
    core = dose_core(me["성분명"].iloc[0])
    route = str(me["투여"].iloc[0] or "")
    sib = g[g["성분명"].map(dose_core) == core]
    if route:
        sib = sib[sib["투여"].astype(str) == route]
    if sib.empty:
        return pd.DataFrame()
    codes = set(sib["제제코드"].astype(str))
    pr = ss.load_slim("price", ["제품코드", "제품명", "제제코드", "업체명",
                                "급여구분", "적용일자", "금액"])
    if pr is None or "제제코드" not in pr.columns:
        return pd.DataFrame()
    sub = pr[pr["제제코드"].astype(str).isin(codes)]
    if "급여구분" in sub.columns:
        sub = sub[sub["급여구분"] == "급여"]
    if sub.empty:
        return pd.DataFrame()
    sub = sub.assign(적용일자=pd.to_datetime(sub["적용일자"], errors="coerce"))
    latest = sub.dropna(subset=["적용일자"]).sort_values("적용일자").groupby("제품코드").tail(1)
    mg = {str(r["제제코드"]): dose_of(r["성분명"]) for _, r in sib.iterrows()}
    cc = latest["제제코드"].astype(str)
    out = latest.assign(함량=[mg.get(c, (None, ""))[0] for c in cc],
                        함량단위=[mg.get(c, (None, ""))[1] for c in cc])
    out = out.dropna(subset=["함량"])
    out["금액"] = pd.to_numeric(out["금액"], errors="coerce")
    ss.release_memory()
    return out.dropna(subset=["금액"])[
        ["제품코드", "제품명", "업체명", "함량", "함량단위", "금액"]].sort_values(
        ["함량", "금액"], ascending=[True, False]).reset_index(drop=True)


@st.cache_data(show_spinner="오리지널 약가 이력 보는 중…", max_entries=64)
def _orig_cuts(code, drop=0.20):
    """제제(성분·함량·제형) 안에서 오리지널 한 품목을 짚고, 그 품목의 인하 시점을 돌려준다.

    동일성분 품목을 전부 그리는 대신 오리지널 하나만 따라간다. 묶음 최고가 선은
    해마다 다른 품목이 꼭대기를 차지해(아토르바스타틴 10mg 은 14년에 7번 바뀐다)
    품목이 갈아탄 낙차를 인하로 잘못 읽는다.

    돌려주는 값: (품목명, 짚은 근거, 인하목록, 이력시작연도)
    인하목록은 [(적용일자, 이전가, 인하가, 인하율)] 로 drop 이상 떨어진 것만.
    짚지 못하면 (None, 사유, [], None).
    """
    cols = ["제품코드", "제품명", "제제코드", "급여구분", "적용일자", "금액"]
    pr = ss.load_slim("price", cols)
    if pr is None or "제제코드" not in pr.columns:
        return None, "약가 자료를 읽지 못했습니다", [], None
    sub = pr[pr["제제코드"].astype(str) == str(code)]
    if "급여구분" in sub.columns:
        sub = sub[sub["급여구분"] == "급여"]
    if sub.empty:
        return None, "이 제제의 약가 이력이 없습니다", [], None
    sub = sub.assign(적용일자=pd.to_datetime(sub["적용일자"], errors="coerce"))
    sub = sub.dropna(subset=["적용일자"]).sort_values("적용일자")
    y0 = int(sub["적용일자"].dt.year.min())
    codes = set(sub["제품코드"].astype(str))

    name, why = None, ""
    ap = ss.load_slim("approval", ["품목명", "허가일자", "신약구분", "보험코드(EDI)"])
    if ap is not None and "보험코드(EDI)" in ap.columns and not ap.empty:
        raw = ap["보험코드(EDI)"].astype(str)
        # 한 품목에 포장단위별 코드가 여럿이면 쉼표로 이어진다. 시트가 숫자로
        # 읽어 1,000 단위 쉼표가 찍힌 값은 앞 9자리(첫 코드)만 쓸 수 있다.
        #
        # 15만 행을 통째로 펼치면(explode) 30만 행짜리 표가 생겨 메모리를
        # 200MB 넘게 먹는다. 우리가 찾는 건 이 제제의 코드 수백 개뿐이므로,
        # 먼저 '앞 9자리'로 한 번에 거르고, 그래도 없을 때만 쉼표가 있는
        # 행들만 펼쳐 본다.
        ex = ap.assign(_e=raw.str.replace(r"[^0-9]", "", regex=True).str[:9])
        ex = ex[ex["_e"].isin(codes)]
        if ex.empty:
            multi = ap[raw.str.contains(",", na=False)]
            if not multi.empty:
                ex = multi.assign(
                    _e=multi["보험코드(EDI)"].astype(str).str.split(",")).explode("_e")
                ex["_e"] = ex["_e"].astype(str).str.strip()
                ex = ex[ex["_e"].isin(codes)]
        if not ex.empty:
            ex = ex.assign(_hd=pd.to_datetime(ex["허가일자"], format="%Y%m%d", errors="coerce"))
            ex = ex.dropna(subset=["_hd"]).drop_duplicates("_e")
            nov = ex[ex.get("신약구분", "").astype(str) == "신약"] if "신약구분" in ex.columns else ex.iloc[0:0]
            if not nov.empty:
                pickc = nov.sort_values("_hd").iloc[0]
                why = f"허가 자료에 **신약**으로 적힌 품목 (허가 {pickc['_hd'].date()})"
            else:
                pickc = ex.sort_values("_hd").iloc[0]
                why = f"이 제제에서 **가장 먼저 허가**받은 품목 ({pickc['_hd'].date()})"
            got = sub[sub["제품코드"].astype(str) == pickc["_e"]]
            if not got.empty:
                name = str(got["제품명"].iloc[-1])

    if name is None:
        # 허가로 못 짚으면 이력 첫 해에 가장 비쌌던 품목으로 본다.
        first = sub[sub["적용일자"].dt.year == y0]
        if first.empty:
            return None, "오리지널을 짚지 못했습니다", [], y0
        top = first.loc[pd.to_numeric(first["금액"], errors="coerce").idxmax()]
        name = str(top["제품명"])
        why = f"허가 자료로 못 짚어, **{y0}년에 가장 비쌌던 품목**으로 봤습니다"

    ss.release_memory()
    one = sub[sub["제품명"].astype(str) == name].copy()
    one["금액"] = pd.to_numeric(one["금액"], errors="coerce")
    one = one.dropna(subset=["금액"])
    cuts = []
    vals = one["금액"].tolist()
    days = one["적용일자"].tolist()
    for i in range(1, len(vals)):
        p0, p1 = float(vals[i - 1]), float(vals[i])
        if p0 > 0 and p1 < p0 * (1 - drop):
            cuts.append((days[i].date(), p0, p1, (p1 - p0) / p0))
    return name, why, cuts, y0


def _ph_amt(base, ph):
    """단계의 금액. 4번째 칸에 금액이 있으면 그 값(①·② 저가 캡), 없으면 기준가 × 비율."""
    return int(ph[3]) if len(ph) > 3 else won(base * ph[2])


def _calc_dataonly(base, opt):
    """① 단일제 자료제출 — 2026.8 개정 (펙소페나딘 구강붕해정 표와 같은 값).

    P       = 이미 조정됨: 상한가 ÷ 나누는 비율(53.55% · 2027.4부터 재평가 비율) · 그 밖: 기준가
    조정된 금액 = P × 45% → 저가 기준금액 아래면 기준금액
    산정가    = 조정된 금액 × 유형배율(용법용량+이미 조정 1.1) × 0.8^(기준요건 미충족) → 저가 캡
    가산      = 기준요건 2개 충족 때만. 산정가 + 산정가 × (70/45 − 1)  ← 고시 '그 조정된 금액에 가산'
    """
    R, ph, tr = CALC_RATE, [], []
    opt["trace"] = tr
    if base <= 0:
        return [("산정", "이후", 0.0, 0)], "기준가를 넣어주세요."
    dose = str(opt.get("sub", "")).startswith("용법")
    imp = str(opt.get("imp", "")).startswith("개량")
    st3 = int(opt.get("ostate", 0))
    miss = max(0, 2 - int(opt.get("reqs", 2)))
    cut = REQ_CUT ** miss
    cheap = float(opt.get("cheap") or 0)
    cut_txt = f" × {cut:g} (기준요건 미충족 {miss}개)" if miss else ""

    if st3 == 0:
        init = (R["dose"] if dose else R["salt"]) * cut
        amt = won(base * init)
        tr.append(f"조정 전 유지: {base:,.0f}원 × {pct(R['dose'] if dose else R['salt'])}{cut_txt} = {amt:,}원")
        ph.append(("최초 등재 (조정 전 유지)", "이후", amt / base, amt))
        return ph, (f"오리지널이 조정 전이고 이번 등재로도 조정이 일어나지 않는 경우라 "
                    f"{pct(init)} 를 계속 적용합니다. 가산은 붙지 않습니다.")

    if st3 == 2:
        div = float(opt.get("div") or OLD_RATIO)
        P = base / div
        mult = 1.1 if dose else 1.0
        tr.append(f"P = 상한가 {base:,.0f}원 ÷ {pct(div)} = {P:,.2f}원")
    else:
        div, P, mult = None, float(base), 1.0
        tr.append(f"P = 기준가 {base:,.0f}원 (오리지널이 아직 조정 전 가격)")
    adj = won(P * ADJ_RATIO)
    adj_c = max(adj, cheap) if cheap else adj
    tr.append(f"조정된 금액 = P × {pct(ADJ_RATIO)} = {P * ADJ_RATIO:,.2f} → {adj:,}원"
              + (f" → 저가 기준 {adj_c:,.0f}원" if adj_c != adj else ""))
    calc = won(adj_c * mult * cut)
    calc_c = int(max(calc, cheap)) if cheap else calc
    tr.append(f"산정가 = {adj_c:,.0f}원" + (f" × {mult:g} (용법용량 개선)" if mult != 1 else "")
              + cut_txt + f" = {calc:,}원" + (f" → 저가 기준 {calc_c:,}원" if calc_c != calc else ""))

    addon = bool(opt.get("addon", True)) and miss == 0
    if not addon:
        why = ("기준요건 미충족이라 가산 없음" if miss else "가산 없음을 골랐습니다")
        tr.append(f"{why} → 산정가 {calc_c:,}원을 계속 적용")
        ph.append(("산정가 (가산 없음)", "이후", calc_c / base, calc_c))
        return ph, (f"{why}. 산정가 {calc_c:,}원을 처음부터 계속 적용합니다."
                    + (" 위탁 생산이면 '자체 생동시험'을 빼고 계산합니다." if miss else ""))

    add70 = won(calc_c * (0.70 / ADJ_RATIO - 1))
    a70 = calc_c + add70
    tr.append(f"가산 1년 = {calc_c:,} + {calc_c:,} × (70/{ADJ_RATIO*100:g} − 1) = {calc_c:,} + {add70:,} = {a70:,}원")
    ph.append(("최초등재 가산 (70%)", "1년", a70 / base, a70))
    if imp:
        ph.append(("개량신약 가산 유지 (70%)", "이후", a70 / base, a70))
        tr.append(f"개량신약: {a70:,}원 계속 (연장이 이어짐)")
        return ph, ("개량신약이라 70% 수준의 가산이 계속 이어지는 것으로 그렸습니다. "
                    "가산액은 고시대로 '조정된 금액에 (70/45 − 1)을 더하는' 방식입니다.")
    prev = a70
    if opt.get("ext", True):
        add60 = won(calc_c * (0.60 / ADJ_RATIO - 1))
        a60 = low_cap(prev, calc_c + add60, cheap)
        tr.append(f"가산 3년 = {calc_c:,} + {calc_c:,} × (60/{ADJ_RATIO*100:g} − 1) = {calc_c:,} + {add60:,} = {calc_c + add60:,}원"
                  + (f" → 저가 캡 {a60:,}원" if a60 != calc_c + add60 else ""))
        ph.append(("가산 연장 (60%)", "3년", a60 / base, a60))
        prev = a60
    after = low_cap(prev, calc_c, cheap)
    tr.append(f"이후 = 산정가 {calc_c:,}원" + (f" → 저가 캡 {after:,}원" if after != calc_c else ""))
    ph.append(("가산 종료 후 (산정가)", "이후", after / base, after))
    return ph, ("가산액은 고시대로 '조정된 금액에 (70/45 − 1)·(60/45 − 1)을 더하는' 방식입니다. "
                "3년 연장은 국내 생산 또는 동일제품 회사 수 3개 이하일 때만 붙습니다.")


def _calc_phases(ty, base, opt):
    """유형별 단계 목록 [(이름, 기간, 비율[, 금액])] 과 안내문."""
    R, ph = CALC_RATE, []
    if ty.startswith("①"):
        return _calc_dataonly(base, opt)
    if ty.startswith("②"):
        # 2026.8 개정고시: 기준요건(DMF·자체생동) 충족 수로 산정률이 갈린다.
        req = REQ_RATE[opt["reqs"]]
        way = calc_route(opt["prior"], opt["apply"])
        add = {"혁": R["gen_innov"], "준": R["gen_semi"], "해": None}[opt["co"][0]]

        if way == "계단식":
            # min(기등재 최저가, 기준가×29%) × 85%
            # 단, 기등재 최저가가 저가의약품 기준금액 이하면 인하 없이 최저가와 동일가.
            low, cheap = float(opt["low"]), float(opt["cheap"])
            if base <= 0:
                return [("계단식 산정", "이후", 0)], "기준가를 넣어주세요."
            if cheap and low <= cheap:
                ph.append(("저가의약품 — 최저가와 동일", "이후", low / base))
                return ph, (f"기등재 최저가 {low:,.0f}원이 저가의약품 기준금액 "
                            f"{cheap:,.0f}원 이하라, 인하 없이 최저가와 같은 값으로 산정합니다.")
            amt = min(low, base * STEP_RATE) * AFTER_CUT
            ph.append(("계단식 산정", "이후", amt / base))
            pick = "기등재 최저가" if low < base * STEP_RATE else "29% 산정액"
            return ph, (f"기등재 동일제제가 {opt['prior']}개라 계단식 약가인하 대상입니다. "
                        f"min(기등재 최저가 {low:,.0f}원, 29% 산정액 {base*STEP_RATE:,.0f}원)"
                        f" = {pick} → ×85%. 기준요건 충족 여부와 무관합니다.")

        if add:
            ph.append(("가산 1년", "1년", add))
            if opt["ext"]:
                ph.append(("가산 연장", "3년", add))

        cheap = float(opt.get("cheap") or 0)
        if way == "다품목":
            if add:
                # 가산 중에는 인하가 걸리지 않는다. 가산이 끝나면 45% 하락과 85% 인하가 한 번에.
                _new = low_cap(won(base * add), won(base * AFTER_ADDON * AFTER_CUT), cheap)
                ph.append(("가산 종료 — 45% × 85%", "이후", (_new / base) if base else 0, _new))
                note = ("다품목 등재관리 대상입니다. 가산을 받는 동안은 인하가 걸리지 않고, "
                        "가산이 끝나는 순간 45%로 떨어짐과 동시에 85%가 같이 걸립니다.")
            else:
                ph.append(("등재 ~ 1년", "1년", req))
                _new = low_cap(won(base * req), won(base * req * AFTER_CUT), cheap)
                ph.append(("1년 후 — 다품목 85%", "이후", (_new / base) if base else 0, _new))
                note = ("다품목 등재관리 대상입니다. 등재 1년 뒤 최초 산정가의 85%로 조정됩니다 "
                        f"({pct(req)} → {pct(req * AFTER_CUT)}).")
            note += f" 조정 때는 저가 기준금액 {cheap:,.0f}원 아래로 내려가지 않습니다." if cheap else ""
            return ph, (f"기등재 {opt['prior']}개 + 동시신청 {opt['apply']}개 = "
                        f"{opt['prior']+opt['apply']}개. " + note)

        if add:
            _new = low_cap(won(base * add), won(base * AFTER_ADDON), cheap)
            ph.append((f"가산 종료 후 ({AFTER_ADDON*100:.0f}%)", "이후", (_new / base) if base else 0, _new))
            return ph, ("가산이 끝나면 기준요건과 무관하게 45%로 유지됩니다."
                        + (f" 조정 때는 저가 기준금액 {cheap:,.0f}원 아래로 내려가지 않습니다." if cheap else ""))
        ph.append(("기본 산정", "이후", req))
        return ph, (f"기준요건 {opt['reqs']}개 충족 → {pct(req)}. "
                    f"기등재 {opt['prior']}개 + 동시신청 {opt['apply']}개라 "
                    "다품목·계단식에는 해당하지 않습니다.")
    if ty.startswith("④"):
        # 자사가 이미 등재한 것과 똑같은 품목이면 그 품목과 같은 금액으로 준다.
        return ([("자사 동일가", "이후", 1.0)],
                "자사가 이미 등재한 동일 품목과 같은 금액입니다. "
                "계단식·다품목 판정을 타지 않고, 가산도 붙지 않습니다.")
    if ty.startswith("⑤"):
        mg_new, mg_ref = float(opt["mg_new"]), float(opt["mg_ref"])
        if mg_new <= 0 or mg_ref <= 0 or base <= 0:
            return [("함량배수 산정", "이후", 0)], "함량과 기준가를 넣어주세요."
        b = float(opt["bval"]) if opt["bio"] else dose_b(mg_new, mg_ref)
        if b <= 0:
            return [("함량배수 산정", "이후", 0)], "B 값을 넣어주세요."
        # A: 자사제품이 있으면 자사 최근접 함량 상한금액, 없으면 최고가의 45%(마약·생물 70%)
        a_rate = 1.0 if opt["own"] else (DOSE_A_SPECIAL if opt["special"] else DOSE_A)
        rate = a_rate * b if mg_new > mg_ref else a_rate / b
        how = "×" if mg_new > mg_ref else ("÷" if mg_new < mg_ref else "=")
        src = ("자사 최근접 함량 품목의 상한금액" if opt["own"]
               else f"최근접 함량 최고가의 {a_rate*100:.0f}%"
                    f"{' (마약·생물의약품)' if opt['special'] else ''}")
        return ([("함량배수 산정", "이후", rate)],
                f"A = {src} = {won(base*a_rate):,}원 · B = {b:.4f} · "
                f"신청 {mg_new:g} {how} 비교 {mg_ref:g} → A {how if how != '=' else '×'} B."
                + (" 생물의약품이라 B 를 직접 넣은 값으로 계산했습니다." if opt["bio"] else ""))
    rate = {"제": R["combo_base"], "개": R["combo_imp"]}[opt["ct"][0]]
    if "혁신형" in opt["ct"]:
        rate = R["combo_innov"]
    if rate == R["combo_base"]:
        ph.append(("기본 산정", "이후", rate))
    else:
        ph.append(("가산 1년", "1년", rate))
        if opt["ext"]:
            ph.append(("가산 연장", "3년", rate))
        ph.append((f"가산 종료 후 ({R['combo_base']*100:.0f}%)", "이후", R["combo_base"]))
    return ph, "성분별 금액은 해당 단일제의 산정 기준가를 씁니다."

def won(x):
    """원 단위 반올림. int() 로 버리면 고시 금액과 1원씩 어긋난다
    (예: 2,032 × 36% = 731.52 → 고시 732, 버리면 731).
    파이썬 round() 는 .5 에서 짝수로 가므로 사사오입을 직접 쓴다."""
    import math
    return math.floor(float(x) + 0.5)

def _calc_chart(base, phases):
    """단계별 예상 상한금액을 계단 그래프로. '이후' 구간은 2년 폭으로 그린다."""
    xs, ys, ticks, tlabels = [], [], [0], ["등재"]
    t = 0.0
    for ph in phases:
        name, term, rate = ph[0], ph[1], ph[2]
        dur = 2.0 if term == "이후" else float(str(term).replace("년", "") or 1)
        amt = _ph_amt(base, ph)
        xs += [t, t + dur]
        ys += [amt, amt]
        if term == "이후":
            # 끝에 붙이면 화면 밖으로 잘린다 → 구간 가운데에 적는다
            ticks.append(t + dur / 2); tlabels.append("이후")
        else:
            ticks.append(t + dur); tlabels.append(f"{t + dur:.0f}년")
        t += dur
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="예상 상한금액",
                             line=dict(color="#c9376b", width=3),
                             fill="tozeroy", fillcolor="rgba(201,55,107,.12)",
                             hovertemplate="%{y:,.0f}원<extra></extra>"))
    # 단계별 금액을 구간 가운데에 적어 준다
    t = 0.0
    for ph in phases:
        name, term, rate = ph[0], ph[1], ph[2]
        dur = 2.0 if term == "이후" else float(str(term).replace("년", "") or 1)
        fig.add_annotation(x=t + dur / 2, y=_ph_amt(base, ph), text=f"{_ph_amt(base, ph):,}원",
                           showarrow=False, yshift=14, font=dict(size=12, color="#c9376b"))
        t += dur
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=36, b=10), showlegend=False,
                      title="등재 후 예상 상한금액 변화", yaxis=_WON_AXIS,
                      xaxis=dict(title="등재 후 경과", tickmode="array",
                                 tickvals=ticks, ticktext=tlabels))
    return fig


def _cheap_pick(where, row, sfx):
    """저가의약품 기준금액 고르기. 투여·단위로 자동 판정하고 고쳐 쓸 수 있다."""
    _kind = cheap_kind(row.get("투여"), row.get("단위"))
    _opts = list(CHEAP) + ["직접 입력"]
    _idx = _opts.index(_kind) if _kind in _opts else len(_opts) - 1
    _sel = where.selectbox("저가의약품 기준", _opts, index=_idx, key=f"calc_cheapkind{sfx}")
    if _sel == "직접 입력":
        return float(where.number_input("기준금액(원)", value=0.0, step=1.0,
                                        format="%.0f", key=f"calc_cheapamt{sfx}"))
    where.caption(f"{CHEAP[_sel]:,}원 — 투여 {row.get('투여') or '?'} · "
                  f"단위 {row.get('단위') or '?'} 로 자동 판정 · 조정 때 이 금액 아래로 안 내려감")
    return float(CHEAP[_sel])


@st.cache_data(show_spinner=False, ttl=3600)
def _price_asof():
    """약가 자료의 가장 최근 적용일(= 고시 적용일). 못 읽으면 오늘."""
    try:
        d = pd.to_datetime(ss.load_price()["적용일자"], errors="coerce").max()
        if pd.notna(d):
            return d.date()
    except Exception:
        pass
    return pd.Timestamp.today().date()


def _price_calc_page():
    """유형별 예상 상한금액. 기준가는 같은 제제(성분·함량·제형) 최고가에서 자동으로 채운다."""
    if not ss.price_group_ready():
        st.info("약가 자료에 '제제코드'가 아직 없습니다. 새벽 자동 갱신이 한 번 돌면 사용할 수 있습니다.\n\n"
                "(고시 엑셀에서 '주성분코드_동일제형'을 받아오도록 바뀌었고, 다음 갱신부터 채워집니다.)")
        return
    gsum = ss.price_groups()
    if gsum.empty:
        st.warning("묶을 수 있는 약가 자료가 없습니다.")
        return
    st.markdown("**🧮 유형별 예상 상한금액** · 기준가는 같은 성분·함량·제형 묶음의 "
                "**최고가**에서 자동으로 채웁니다 (고쳐 쓸 수 있습니다)")
    ty = st.radio("유형", ["① 단일제 자료제출", "② 제네릭", "③ 복합제",
                          "④ 자사 동일가", "⑤ 함량배수"],
                  horizontal=True, key="calc_ty")

    # ⑤ 함량배수는 '비교 제품'을 제품명으로 찾는 게 맞다. 신청 제품은 아직
    # 없고, 고시가 기준으로 삼으라는 것은 가장 근접한 함량의 '제품'이다.
    picked_prod = None
    if ty.startswith("⑤"):
        prods = _price_products()
        if prods.empty:
            st.warning("약가 자료를 읽지 못했습니다.")
            return
        c1, c2 = st.columns([1, 2])
        kw = c1.text_input("제품 검색", key="calc_pkw",
                           placeholder="제품명 일부 (예: 리피토 / 아토르바)")
        # 성분 묶음을 못 찾는 품목은 아예 목록에 넣지 않는다. 옛 이력에만 있고
        # 지금 고시에 없는 품목은 제제코드가 비어 있어, 고르면 함량·기준가를
        # 채울 수 없다(그 상태로 두면 아래 칸이 통째로 안 그려졌다).
        _known = set(gsum["제제코드"].astype(str))
        cand = prods[prods["제제코드"].astype(str).isin(_known)]
        if kw:
            cand = cand[cand["제품명"].astype(str).str.contains(
                kw, case=False, na=False, regex=False)]
        if cand.empty:
            st.warning("조건에 맞는 제품이 없습니다. 검색어를 지우거나 바꿔보세요.")
            return
        cand = cand.sort_values(["제품명", "금액"]).head(300).reset_index(drop=True)
        _plab = [f"{str(r['제품명'])[:42]} · {str(r['업체명'])[:12]} · {r['금액']:,.0f}원"
                 for _, r in cand.iterrows()]
        _psel = c2.selectbox(f"비교 제품 선택 ({len(cand):,}품목)", _plab, key="calc_psel")
        picked_prod = cand.iloc[_plab.index(_psel)]
        _hit = gsum[gsum["제제코드"].astype(str) == str(picked_prod["제제코드"])]
        if _hit.empty:
            # 여기서 멈추면 아래 칸이 통째로 안 그려진다. 묶음이 있는 다른
            # 후보로 넘어가고, 그마저 없으면 성분으로 고르게 돌려보낸다.
            _ok = cand[cand["제제코드"].astype(str).isin(set(gsum["제제코드"].astype(str)))]
            if _ok.empty:
                st.warning("고른 제품의 성분 묶음을 찾지 못했습니다. "
                           "검색어를 바꾸거나 다른 유형으로 계산해 주세요.")
                return
            picked_prod = _ok.iloc[0]
            st.warning(f"고른 제품의 성분 묶음이 없어 **{picked_prod['제품명']}** 으로 "
                       "대신 계산합니다. 다른 제품을 고르시면 바뀝니다.")
            _hit = gsum[gsum["제제코드"].astype(str) == str(picked_prod["제제코드"])]
        row = _hit.iloc[0]
    else:
        pool = gsum
        if ty.startswith("③"):
            pool = pool[pd.to_numeric(pool["성분수"], errors="coerce").fillna(1) > 1]
        c1, c2 = st.columns([1, 2])
        kw = c1.text_input("성분 검색", key="calc_kw", placeholder="성분명 일부 (예: atorvastatin)")
        if kw:
            pool = pool[pool["성분명"].astype(str).str.contains(kw, case=False, na=False, regex=False)]
        if pool.empty:
            st.warning("조건에 맞는 성분이 없습니다. 검색어를 지우거나 바꿔보세요.")
            return
        pool = pool.sort_values("품목수", ascending=False)
        opts = pool["라벨"].tolist()[:300]
        sel = c2.selectbox(f"성분 선택 ({len(pool):,}개 묶음 중 상위 {len(opts)}개)", opts, key="calc_sel")
        row = pool[pool["라벨"] == sel].iloc[0]

    st.markdown(f"**🔎 {row['대표품목']}** · {row['대표업체']}  ·  `{row['제제코드']}`")
    m = st.columns(5)
    m[0].metric("기준가 (묶음 최고가)", f"{row['최고가']:,.0f} 원")
    m[1].metric("등재 품목 수", f"{int(row['품목수'])} 품목")
    m[2].metric("최저가", f"{row['최저가']:,.0f} 원")
    m[3].metric("중앙값", f"{row['중앙']:,.0f} 원")
    m[4].metric("성분 수", f"{row['성분수'] or '—'} 성분")
    if int(row["단위종류"] or 1) > 1:
        st.warning(f"이 묶음은 단위가 {int(row['단위종류'])}가지 섞여 있습니다 — "
                   "최고가가 큰 포장일 수 있으니 확인하세요.")
    _note = str(row.get("특이사항") or "").strip()
    if _note.lower() in ("nan", "none", "<na>"):
        _note = ""
    if _note:
        # 고시 비고에서 '약가유연계약' 과 그 밖의 내용을 나눠서 보여준다
        _parts = [x.strip() for x in _note.split("/") if x.strip()]
        _flex = [x for x in _parts if "유연계약" in x]
        _etc = [x for x in _parts if "유연계약" not in x]
        if _flex:
            st.error("⚠️ **약가유연계약 적용 약제**입니다. 표면 상한금액과 실제 지급가가 "
                     "다를 수 있으니, 아래 산정 결과를 그대로 쓰기 전에 계약 조건을 확인하세요.")
        if _etc:
            st.warning("고시 비고: " + " / ".join(_etc))

    # 기준가가 '이미 조정된 가격'인지는 사람이 외워 둘 일이 아니라 이력에 남는다.
    # 최고가 계열(=오리지널로 보는 줄)이 내려온 적이 있는지로 기본값만 잡아 준다.
    adj_guess, adj_msg, adj_tail = 0, "", ""
    yr = ss.price_group_yearly()
    h = yr[yr["제제코드"] == row["제제코드"]].sort_values("연").copy()
    if h.empty:
        st.caption("이 제제의 약가 이력이 없습니다.")
    else:
        # 약가표는 '금액이 바뀐 시점'만 보관한다. 등재 후 한 번도 안 바뀐 품목은
        # 점이 하나뿐이라 선이 안 그려진다(예: 베오바정). 마지막 금액은 지금도
        # 유효하므로 올해까지 이어 붙여 '변동 없음'이 선으로 보이게 한다.
        _cur = pd.Timestamp.today().year
        if int(h["연"].iloc[-1]) < _cur:
            h = pd.concat([h, h.iloc[[-1]].assign(연=_cur)], ignore_index=True)
        _flat = h["최고"].nunique() == 1
        f2 = go.Figure()
        f2.add_trace(go.Scatter(x=list(h["연"]) + list(h["연"])[::-1],
                                y=list(h["최고"]) + list(h["최저"])[::-1],
                                fill="toself", fillcolor="rgba(201,55,107,.15)",
                                line=dict(width=0), hoverinfo="skip", name="동일제제 가격 범위"))
        f2.add_trace(go.Scatter(x=h["연"], y=h["최고"], mode="lines+markers", name="최고가 (기준가)",
                                line=dict(color="#c9376b", width=3, shape="hv")))
        f2.update_layout(height=300, margin=dict(l=10, r=10, t=36, b=10),
                         title="연도별 상한금액", yaxis=_WON_AXIS,
                         legend=dict(orientation="h", y=-0.2), hovermode="x unified")
        st.plotly_chart(f2, use_container_width=True)
        if _flat:
            st.caption("등재 이후 상한금액이 바뀐 적이 없습니다 — 선이 평평한 것이 정상입니다.")

    # 오리지널 한 품목을 짚어 그 품목의 인하 시점을 날짜로 보여준다.
    # 묶음 최고가 선은 해마다 꼭대기 품목이 바뀌어 낙차를 인하로 잘못 읽는다.
    #
    # 계산이 1초쯤 걸려서 평소에는 접어 둔다. expander 는 접혀 있어도 안쪽
    # 코드가 그냥 실행되므로, 체크 칸으로 막아야 실제로 미뤄진다.
    if st.checkbox("🔎 **오리지널의 인하 이력 보기** — 기준가가 이미 조정된 가격인지 확인합니다",
                   value=False, key=f"calc_orig_{row['제제코드']}"):
        _nm, _why, _cuts, _y0 = _orig_cuts(row["제제코드"])
        if _nm is None:
            st.info(f"오리지널을 짚지 못했습니다 — {_why}")
        else:
            _tail = (f" 이력은 {_y0}년부터라, 그 이전의 조정은 이 자료에 없습니다."
                     if _y0 else "")
            if _cuts:
                adj_guess = 2
                _big = [c for c in _cuts if c[3] <= -0.30]
                st.warning(f"**{_nm}** 에 20% 넘는 인하가 {len(_cuts)}번 있습니다"
                           + (f" (그중 30% 이상 {len(_big)}번)" if _big else "")
                           + f". 기준가가 이미 조정된 가격일 가능성이 큽니다.{_tail}")
                st.dataframe(pd.DataFrame(
                    [{"적용일자": d, "이전가": f"{p0:,.0f}원", "인하가": f"{p1:,.0f}원",
                      "인하율": f"{r*100:.1f}%", "": "30% 이상" if r <= -0.30 else ""}
                     for d, p0, p1, r in _cuts]),
                    hide_index=True, use_container_width=True,
                    height=min(38 * (len(_cuts) + 1) + 4, 220))
            else:
                st.info(f"**{_nm}** 에 20% 넘는 인하가 없습니다. "
                        f"이번 등재로 조정이 일어나는지는 아래에서 고르세요.{_tail}")
            st.caption(f"오리지널로 본 품목: {_why}")
    else:
        st.caption("펼치면 오리지널 한 품목을 짚어 20% 넘는 인하 시점을 날짜로 보여줍니다. "
                   "아래 **오리지널 조정 상태**의 기본값도 그때 맞춰집니다 "
                   "(접어 두면 '조정 전'에서 시작합니다). 1초쯤 걸립니다.")

    L, R = st.columns(2)
    with L:
        # 이름표(key)가 고정이면 성분을 바꿔도 앞서 입력한 숫자가 그대로 남는다.
        # 제제코드를 섞어 성분마다 다른 칸으로 만들어, 바꾸는 즉시 새 기준가가 들어오게 한다.
        # ⑤ 는 위에서 고른 제품이 곧 비교 제품이다. 함량은 그 제품이 속한
        # 묶음(성분·함량·제형)의 함량으로 자동으로 채운다.
        _peer = picked_prod if ty.startswith("⑤") else None
        _ref_mg, _ref_unit = dose_of(row["성분명"])
        if _peer is not None:
            st.caption(f"비교 제품: **{_peer['제품명']}** · "
                       f"{_ref_mg:g}{_ref_unit} · {_peer['업체명']} · "
                       f"{_peer['금액']:,.0f}원" if _ref_mg else
                       f"비교 제품: **{_peer['제품명']}** · {_peer['업체명']} · "
                       f"{_peer['금액']:,.0f}원 (함량을 못 읽어 직접 넣어 주세요)")
            _pe = _dose_peers(row["제제코드"])
            if not _pe.empty and _pe["함량"].nunique() > 1:
                _all = sorted(_pe["함량"].unique())
                st.caption("같은 성분에 있는 함량: "
                           + " · ".join(f"{v:g}{_ref_unit}" for v in _all)
                           + " — 고시는 **가장 근접한 함량**의 제품을 기준으로 삼습니다.")

        _blabel = {"④": "자사 기등재 동일 품목의 상한금액",
                   "⑤": "비교 제품의 상한금액 (A 의 기준 · 고쳐 쓸 수 있음)"}.get(
            ty[0], "기준 상한금액 (자동 채움 · 고쳐 쓸 수 있음)")
        _bval = float(_peer["금액"]) if _peer is not None else float(row["최고가"])
        _bkey = (f"calc_base_{row['제제코드']}_{_peer['제품코드']}"
                 if _peer is not None else f"calc_base_{row['제제코드']}")
        base = st.number_input(_blabel, value=_bval, step=1.0, format="%.0f", key=_bkey)
        opt = {"sub": "", "imp": "", "ostate": 0, "co": "혁", "ext": True, "ct": "제",
               "reqs": 2, "prior": int(row["품목수"]), "apply": 1,
               "low": float(row["최저가"]), "cheap": 0.0,
               "mg_new": 0.0, "mg_ref": 0.0, "own": True,
               "special": False, "bio": False, "bval": 1.0}
        if ty.startswith("①"):
            # 선택지 글자가 바뀌어 예전 화면이 남긴 값과 섞이지 않도록 key 를 새로 붙인다.
            opt["sub"] = st.radio("산정 유형",
                                  ["염·이성체 / 신규제형",
                                   "용법용량 개선 (이미 조정됨이면 × 1.1)"], key="calc_sub2")
            opt["imp"] = st.radio("개량신약 인정",
                                  ["개량신약 — 가산 70% 계속",
                                   "자료제출 — 1년 70% → 3년 60% → 이후"], index=1, key="calc_imp2")
            opt["ostate"] = OSTATE.index(st.radio(
                "**오리지널 조정 상태**", OSTATE, index=adj_guess,
                key=f"calc_ost2_{row['제제코드']}"))
            if opt["ostate"] == 2:
                with st.container(border=True):
                    st.markdown("**상한가 나누는 비율** — 재평가 차수 · 기업 유형")
                    d1, d2 = st.columns(2)
                    _cha = d1.radio("재평가 차수", ["1차", "2차"], horizontal=True, key="calc_cha")
                    _coty = d2.radio("기업 유형", ["일반", "준혁신형", "혁신형"],
                                     horizontal=True, key="calc_coty")
                    _when = st.date_input("상한가 시점 (기본: 약가 고시 적용일)",
                                          value=_price_asof(), key=f"calc_when_{row['제제코드']}")
                    opt["div"] = reeval_ratio(_cha, _coty, _when)
                    if base > 0:
                        st.caption(f"→ **{pct(opt['div'])}** 로 나눔 · P = {base:,.0f} ÷ "
                                   f"{pct(opt['div'])} = **{base / opt['div']:,.0f}원**")
                    st.caption("📝 상한가 나누는 비율은 **2027년 4월부터** 적용됩니다 "
                               "(1차 2027.4~ · 2차 2030.10~). 그 전에는 무엇을 골라도 53.55%입니다.")
            if opt["ostate"] in (1, 2):
                opt["addon"] = st.radio("최초등재 가산", ["가산 받음", "가산 없음"],
                                        horizontal=True, key="calc_addon").startswith("가산 받음")
                if opt["imp"].startswith("개량"):
                    st.caption("개량신약은 연장이 자동으로 이어집니다 (70% 계속).")
                else:
                    opt["ext"] = st.checkbox("+3년 연장 (60%) — 국내 생산 또는 동일제품 회사 수 3개 이하",
                                             value=True, key="calc_ext1")
            q1, q2 = st.columns(2)
            _dmf1 = q1.checkbox("DMF 등록 원료", value=True, key="calc_dmf1")
            _bio1 = q2.checkbox("자체 생동시험", value=True, key="calc_bio1")
            opt["reqs"] = int(_dmf1) + int(_bio1)
            st.caption("기준요건 미충족 1개마다 20% 인하 · 하나라도 미충족이면 가산 없음 · "
                       "위탁 생산이면 '자체 생동시험'을 빼세요 (일반의약품은 비교용출자료로 갈음 가능)")
            opt["cheap"] = _cheap_pick(st, row, "1")
        elif ty.startswith("②"):
            q1, q2 = st.columns(2)
            _dmf = q1.checkbox("DMF 등록 원료", value=True, key="calc_dmf")
            _bio = q2.checkbox("자체 생동시험", value=True, key="calc_bio")
            opt["reqs"] = int(_dmf) + int(_bio)
            st.caption(f"기준요건 **{opt['reqs']}개 충족 → 산정률 "
                       f"{pct(REQ_RATE[opt['reqs']])}** "
                       "(2개 45% · 1개 36% · 0개 28.8% — 미충족 1개마다 20% 인하)")

            opt["co"] = st.radio("기업 유형 (정책가산)",
                                 ["혁신형 (60%)", "준혁신형 (50%)", "해당 없음 (가산 없음)"],
                                 key="calc_co")
            opt["ext"] = st.checkbox("+3년 연장 요건 충족 가정", value=True, key="calc_ext")

            st.markdown("---")
            st.markdown("**등재 상황** — 어느 규칙에 걸리는지 여기서 정해집니다")
            n1, n2 = st.columns(2)
            opt["prior"] = int(n1.number_input(
                "기등재 동일제제 수 (오리지널 포함)", min_value=0, step=1,
                value=int(row["품목수"]), key=f"calc_prior_{row['제제코드']}"))
            opt["apply"] = int(n2.number_input(
                "동시 신청 품목 수", min_value=1, step=1, value=1, key="calc_apply"))
            _way = calc_route(opt["prior"], opt["apply"])
            st.caption(f"지금 이 제제의 등재 품목은 {int(row['품목수'])}개입니다 — "
                       "진입 시점에는 달라지니 직접 고쳐 쓰세요.")
            {"일반": st.info, "다품목": st.warning, "계단식": st.error}[_way](
                {"일반": f"**일반 산정** — 합계 {opt['prior']+opt['apply']}개. "
                         f"{pct(REQ_RATE[opt['reqs']])} 로 등재됩니다.",
                 "다품목": f"**다품목 등재관리** — 기등재 {opt['prior']} + 신청 {opt['apply']} = "
                          f"{opt['prior']+opt['apply']}개 (14개 이상). 1년 뒤 85%로 조정됩니다.",
                 "계단식": f"**계단식 약가인하** — 기등재가 {opt['prior']}개 (13개 이상)라 "
                          "min(기등재 최저가, 29% 산정액) × 85% 로 산정됩니다."}[_way])

            c1, c2 = st.columns(2)
            if _way == "계단식":
                opt["low"] = c1.number_input(
                    "기등재 동일제제 최저가", value=float(row["최저가"]), step=1.0,
                    format="%.0f", key=f"calc_low_{row['제제코드']}")
            # 저가 기준금액은 계단식 판정과, 가격이 내려가는 조정 단계의 하한(캡)에 쓴다.
            opt["cheap"] = _cheap_pick(c2, row, "")
        elif ty.startswith("③"):
            opt["ct"] = st.radio("복합제 유형", ["제네릭/자료제출 (45%)", "개량신약 복합제 (60%)",
                                             "개량신약 복합제 + 혁신형 (68%)"], key="calc_ct")
            opt["ext"] = st.checkbox("+3년 연장 요건 충족 가정", value=True, key="calc_ext2")
        elif ty.startswith("④"):
            st.caption("자사가 이미 등재한 것과 **똑같은 품목**을 허가받는 경우입니다. "
                       "위 칸에 그 자사 품목의 상한금액을 넣으면 같은 금액이 나옵니다. "
                       "입력할 것이 더 없습니다.")
        else:
            m1, m2 = st.columns(2)
            opt["mg_new"] = float(m1.number_input(
                f"신청 제품 함량 ({_ref_unit or '단위'})", min_value=0.0,
                value=0.0, step=1.0, format="%.4g", key="calc_mgnew"))
            opt["mg_ref"] = float(m2.number_input(
                f"비교 제품 함량 ({_ref_unit or '단위'})", min_value=0.0,
                value=float(_ref_mg or 0.0), step=1.0, format="%.4g",
                key=f"calc_mgref_{_bkey}"))
            st.caption("비교 제품의 함량과 상한금액은 위에서 고른 제품에서 자동으로 "
                       "채웁니다. 신청 함량만 넣으시면 됩니다.")
            opt["own"] = st.radio(
                "A 를 무엇으로 잡나", ["(가) 자사제품이 등재되어 있다 — 그 상한금액",
                                 "(나) 자사제품이 없다 — 최근접 함량 최고가의 45%"],
                key="calc_own").startswith("(가)")
            if not opt["own"]:
                opt["special"] = st.checkbox("마약 또는 생물의약품 (45% 대신 70%)",
                                             value=False, key="calc_special")
                # 고시는 (나)에서 '가장 근접한 함량 제품의 상한금액 중 최고가'를
                # 기준으로 한다. 고른 품목이 그 함량의 최고가가 아니면 A 가 낮게
                # 잡히므로, 그 사실과 최고가를 알려 준다.
                _top = float(row["최고가"]) if row.get("최고가") is not None else 0.0
                if _top > 0 and abs(base - _top) >= 1:
                    st.warning(f"(나)는 **그 함량 제품들 중 최고가**를 기준으로 합니다. "
                               f"이 함량({_ref_mg:g}{_ref_unit})의 최고가는 "
                               f"**{_top:,.0f}원**인데 지금은 {base:,.0f}원으로 잡혀 있습니다. "
                               f"위 '비교 제품의 상한금액'을 고쳐 쓰거나 최고가 품목을 고르세요."
                               if _ref_mg else
                               f"(나)는 그 함량 제품들 중 최고가를 기준으로 합니다. "
                               f"이 묶음의 최고가는 {_top:,.0f}원입니다.")
            opt["bio"] = st.checkbox("생물의약품이라 B 대신 B′ 를 쓴다",
                                     value=False, key="calc_bio2")
            if opt["bio"]:
                opt["bval"] = float(st.number_input(
                    "B′ 값 직접 입력", min_value=0.0001, value=1.0, step=0.01,
                    format="%.4f", key="calc_bval"))
                st.caption("고시에 B′ 식이 따로 있으면 알려주세요. 코드에 넣겠습니다.")
            elif opt["mg_new"] > 0 and opt["mg_ref"] > 0:
                _b = dose_b(opt["mg_new"], opt["mg_ref"])
                if opt["mg_new"] == opt["mg_ref"]:
                    st.caption("신청 함량과 비교 함량이 같습니다 — 함량배수가 아니라 "
                               "**A 를 그대로** 씁니다 (B = 1).")
                else:
                    _h = "×" if opt["mg_new"] > opt["mg_ref"] else "÷"
                    st.caption(f"B = {{({max(opt['mg_new'], opt['mg_ref']):g} ÷ "
                               f"{min(opt['mg_new'], opt['mg_ref']):g} − 1) × 0.5}} + 1 = "
                               f"**{_b:.4f}** → A {_h} B")

    phases, note = _calc_phases(ty, base, opt)
    with R:
        st.metric("등재 시 예상 상한금액", f"{_ph_amt(base, phases[0]):,} 원",
                  delta=f"기준가 대비 {phases[0][2]*100:.2f}%", delta_color="off")
        if base > 0:
            st.plotly_chart(_calc_chart(base, phases), use_container_width=True)
        st.dataframe(pd.DataFrame([{"단계": ph[0], "기간": ph[1], "기준가 대비": f"{ph[2]*100:.2f}%",
                                    "상한금액": f"{_ph_amt(base, ph):,} 원"} for ph in phases]),
                     use_container_width=True, hide_index=True)
        st.caption("산정 근거")
        if opt.get("trace"):
            st.code("\n".join(opt["trace"]), language=None)
        else:
            st.code("\n".join(f"{ph[0]}: {base:,.0f} × {ph[2]*100:.2f}% = {_ph_amt(base, ph):,}원"
                              + (" (저가 캡)" if len(ph) > 3 and ph[3] != won(base * ph[2]) else "")
                              for ph in phases), language=None)
    st.caption(note + "  산정 비율은 제도 변경 시 코드 상단 CALC_RATE 에서 고칩니다.")

