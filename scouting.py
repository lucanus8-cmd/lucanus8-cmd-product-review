"""제품 검토 대시보드 (Streamlit)
탭: 제품 종합분석 / 제품 제안 / 매출 분석 / 특허 분석 / 약가 분석 / 상태
매출: IQVIA·UBIST · 허가/특허/임상: 식약처 · 약가: 심평원 통합본 + NHIS 협상완료(공식)
기존 app.py 는 건드리지 않는다. 실행: streamlit run scouting.py --server.port 8502
"""
import re
from pathlib import Path
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import scout_sources as ss

st.set_page_config(page_title="제품 검토", layout="wide", page_icon="🔎")

# ── 비밀번호 잠금 (배포 시 st.secrets["APP_PASSWORD"] 설정 시에만 작동, 로컬은 통과) ──
def _check_password():
    try:
        pw = st.secrets.get("APP_PASSWORD", None)
    except Exception:
        pw = None
    if not pw:
        return  # 비밀번호 미설정(로컬 개발) → 통과
    if st.session_state.get("_authed"):
        return
    st.title("🔒 제품 검토")
    entered = st.text_input("비밀번호를 입력하세요", type="password")
    if entered == "":
        st.stop()
    if entered == pw:
        st.session_state["_authed"] = True
        st.rerun()
    else:
        st.error("비밀번호가 올바르지 않습니다.")
        st.stop()
_check_password()

import bootstrap  # 배포 시 드라이브에서 데이터 확보 (로컬은 통과)
bootstrap.ensure_data()

DATA_DIR = Path(__file__).parent / "saved_data"
TODAY = pd.Timestamp.today().normalize()

@st.cache_data(show_spinner="IQVIA 로딩 중…")
def load_iqvia():
    df = pd.read_pickle(DATA_DIR / "iqvia.pkl")
    yr = sorted([c for c in df.columns if re.match(r"^\d{4}년$", str(c))])
    return df, yr

@st.cache_data(show_spinner="UBIST 로딩 중…")
def load_ubist():
    df = pd.read_pickle(DATA_DIR / "ubist.pkl")
    yr = sorted([c for c in df.columns if re.match(r"^처방조제액\(원\)_\d{4}년$", str(c))])
    return df, yr

def year_of(c):
    m = re.search(r"(\d{4})", str(c)); return m.group(1) if m else str(c)

def calc_cagr(values, labels, min_base=1e8):
    pairs = [(l, v) for l, v in zip(labels, values) if v and v > 0]
    starts = [(l, v) for l, v in pairs if v >= min_base]
    if not starts or len(pairs) < 2:
        return None
    sl, sv = starts[0]; el, ev = pairs[-1]
    n = int(year_of(el)) - int(year_of(sl))
    return None if n <= 0 or ev <= 0 else ((ev / sv) ** (1 / n) - 1) * 100

SRC = {
    "IQVIA": {"load": load_iqvia, "prod": "제품명", "maker": "회사명", "ing": "성분명", "pay": "급여구분",
              "atc1": "ATC 1(한글)", "atc2": "ATC 2(한글)", "etc": "ETC/OTC", "origin": "국내사/다국적사"},
    "UBIST": {"load": load_ubist, "prod": "제품명", "maker": "제조사명", "ing": "성분", "pay": "급여구분",
              "atc1": "ATC", "atc2": None, "etc": None, "origin": None},
}

def get(src):
    cfg = SRC[src]; df, yr = cfg["load"](); return df, yr, cfg

def _nospace(sr):
    """시리즈의 공백 제거(띄어쓰기 차이 흡수)."""
    return sr.astype(str).str.replace(r"\s+", "", regex=True)

def contains(df, cols, q):
    # 띄어쓰기 차이 흡수: 검색어·대상 모두 공백 제거 후 리터럴 검색
    # (regex=False: 품목명에 괄호/대괄호 등 특수문자가 있어도 정규식 오류 없이 검색)
    qn = re.sub(r"\s+", "", str(q))
    m = pd.Series(False, index=df.index)
    for c in cols:
        if c in df.columns:
            m |= _nospace(df[c]).str.contains(qn, case=False, na=False, regex=False)
    return df[m]

def sales_search(df, cfg, q):
    """제품명/성분명 + (한글↔영문 성분키) 매칭. 띄어쓰기 차이는 무시."""
    qn = re.sub(r"\s+", "", str(q))
    m = _nospace(df[cfg["prod"]]).str.contains(qn, case=False, na=False, regex=False) | \
        _nospace(df[cfg["ing"]]).str.contains(qn, case=False, na=False, regex=False)
    keys = ss.resolve_keys(q)
    if keys:
        m = m | df[cfg["ing"]].astype(str).map(ss.ing_key).isin(keys)
    return df[m]

def msel(container, label, series, key, default=None):
    opts = sorted({str(x) for x in series.dropna() if str(x).strip()})
    return container.multiselect(label, opts, default=default or [], key=key)

def isin(df, col, sel):
    return df if not sel else df[df[col].astype(str).isin(sel)]

def sales_agg(df, yr, key, maker):
    g = df.groupby(key, dropna=True)
    out = g[yr].sum()
    out["시장규모"] = out[yr[-1]]
    out["CAGR"] = out.apply(lambda r: calc_cagr(r[yr].tolist(), yr), axis=1)
    out = out.join(g[maker].nunique().rename("제조사수")).join(g.size().rename("품목수"))
    return out.reset_index()

def yearly_matrix(price_df, codes, name_map):
    """제품코드별 연도말 상한금액 매트릭스(삭제 이후 0). 반환: 제품명 + 연도열."""
    sub = price_df[price_df["제품코드"].isin(codes)].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["연도"] = sub["적용일자"].dt.year
    lp = sub.sort_values("적용일자").groupby(["제품코드", "연도"]).tail(1)
    pv = lp.pivot(index="제품코드", columns="연도", values="금액")
    tv = lp.pivot(index="제품코드", columns="연도", values="급여구분")
    yrs = list(range(int(sub["연도"].min()), 2027))
    pv = pv.reindex(columns=yrs).ffill(axis=1)
    tv = tv.reindex(columns=yrs).ffill(axis=1)
    pv = pv.where(tv != "삭제", 0)
    pv.insert(0, "제품명", [name_map.get(c, c) for c in pv.index])
    return pv.reset_index(drop=True)

HAS_REG = ss.available()
HAS_PRICE = ss.price_available()

_ing_core = ss.ing_core   # 성분명 정규화(코드/염/수화물 제거) — scout_sources 공용

@st.cache_data(show_spinner="허가 목록 준비 중…")
def _approval_opts(basis):
    """허가 제품목록에서 선택용 옵션. 제품명=품목명 전체, 주성분=코어(코드/염 제거·중복 제거)."""
    if not HAS_REG:
        return []
    ap = ss.load_approval()
    if basis == "제품명":
        if "품목명" not in ap.columns:
            return []
        return sorted({str(x).strip() for x in ap["품목명"].dropna() if str(x).strip()})
    if "주성분" not in ap.columns:
        return []
    return sorted({c for c in (_ing_core(x) for x in ap["주성분"].dropna()) if len(c) >= 2})

def _filter_opts(opts, kw, cap=300):
    """검색어로 옵션을 좁혀 상위 cap개만 반환(대용량 목록 셀렉트박스 버벅임 방지). 띄어쓰기 무시."""
    if not kw:
        return opts[:cap]
    k = re.sub(r"\s+", "", str(kw)).lower()
    out = [o for o in opts if k in re.sub(r"\s+", "", o).lower()]
    return out[:cap]

def _sel_cores(basis, sel):
    """선택값 → 동일성분 코어 토큰 집합. 제품명이면 그 품목의 주성분들을 코어로."""
    if basis == "성분":
        return {_ing_core(sel)} if _ing_core(sel) else set()
    if not HAS_REG:
        return set()
    ap = ss.load_approval()
    row = ap[ap.get("품목명", pd.Series(dtype=str)).astype(str) == str(sel)]
    return {c for c in (_ing_core(x) for x in row.get("주성분", pd.Series(dtype=str)).dropna()) if len(c) >= 2}

def _price_trend_codes(pr, basis, term):
    """약가 변동 그래프 대상 제품코드 결정. 성분→오리지널(신약), 제품→해당 제품."""
    if basis == "제품":
        tn = re.sub(r"\s+", "", str(term))
        sub = pr[_nospace(pr["제품명"]).str.contains(tn, case=False, na=False, regex=False)]
        return sub["제품코드"].dropna().unique().tolist(), f"{term} 약가 변동"
    # 성분: 코어(염/코드 제거) 기준으로 오리지널 우선, 없으면 동일성분 전체
    core = _ing_core(term)
    keys = ss.resolve_keys(term) | ss.resolve_keys(core)
    origs = ss.original_products(keys) if keys else []
    pref = {re.split(r"[0-9]", re.sub(r"\s", "", o))[0] for o in origs}
    pref = {p for p in pref if len(p) >= 2}
    if pref:
        sub = pr[pr["제품명"].astype(str).apply(
            lambda n: any(re.sub(r"\s", "", str(n)).startswith(p) for p in pref))]
    else:
        sub = pr[pr["주성분명"].astype(str).map(_ing_core) == core] if core else pr.iloc[0:0]
    return sub["제품코드"].dropna().unique().tolist(), f"{term} 오리지널(신약) 약가 변동"

# ── 재심사(PMS) 만료일 ─────────────────────────────────────────────────────
@st.cache_data(ttl=6 * 3600, show_spinner="재심사(PMS) 만료일 계산 중…")
def _pms_expiry_maps():
    """PMS 만료일 조회용 맵 3종.
    재심사 자료에 '종료일' 컬럼이 없으면 재심사시작일 + 재심사기간(년)으로 산출한다.
    반환: (품목명(공백제거)→만료일, 브랜드코어→만료일, 성분코어→만료일[오리지널 기준])"""
    try:
        rj = ss.load_rejdge()
    except Exception:
        return {}, {}, {}
    if rj is None or rj.empty or "ITEM_NAME" not in rj.columns:
        return {}, {}, {}

    def _dt(x):
        a = pd.to_datetime(x, format="%Y%m%d", errors="coerce")
        d = a.fillna(pd.to_datetime(x, errors="coerce"))
        # 비현실적 날짜는 버린다(이후 날짜 덧셈에서 표현범위 초과로 터지는 것 방지)
        return d.where((d > pd.Timestamp("1900-01-01")) & (d < pd.Timestamp("2200-01-01")))

    end_c = next((c for c in ["REEXAM_END_DATE", "REEXAM_END_DT"] if c in rj.columns), None)
    st_c = next((c for c in ["REEXAM_START_DATE", "REEXAM_START_DT"] if c in rj.columns), None)
    cd_c = next((c for c in ["REEXAM_CODE_NM", "REEXAM_CODE_NAME", "REEXAM_CD_NM"] if c in rj.columns), None)
    try:
        if end_c:
            exp = _dt(rj[end_c])
        elif st_c:
            # 재심사기간은 '재심사대상(6년)'처럼 괄호 안 값만 인정한다.
            # 그냥 (\d+)년 으로 잡으면 문장 속 '1998년' 같은 연도를 기간으로 오인해
            # 시작일+1998년이 되어 날짜 표현범위를 벗어난다.
            if cd_c:
                yrs = rj[cd_c].astype(str).str.extract(r"\(\s*(\d{1,2})\s*년")[0].astype(float)
            else:
                yrs = pd.Series(float("nan"), index=rj.index)
            yrs = yrs.where(yrs.between(1, 15), 6.0)   # 비정상/미확인은 기본 6년
            exp = _dt(rj[st_c]) + pd.to_timedelta(yrs * 365.25, unit="D")
        else:
            return {}, {}, {}
    except Exception:
        return {}, {}, {}

    d = pd.DataFrame({"nm": rj["ITEM_NAME"].astype(str), "exp": exp}).dropna(subset=["exp"])
    prod, brand = {}, {}
    for nm, e in zip(d["nm"], d["exp"]):
        k = re.sub(r"\s+", "", nm)
        if not k:
            continue
        if k not in prod or e > prod[k]:
            prod[k] = e
        b = re.split(r"[0-9(\[]", k)[0]
        if len(b) >= 2 and (b not in brand or e > brand[b]):
            brand[b] = e

    # 성분 기준 PMS = 그 성분 '오리지널(신약)' 품목의 재심사 만료일.
    # 동일성분에 나중에 붙은 별개 재심사(새 적응증·개량신약 등) 때문에 진입 가능 시점이
    # 과하게 늦게 잡히는 것을 막는다. 오리지널을 못 찾으면 동일성분 최댓값으로 폴백.
    core = {}
    if HAS_REG:
        try:
            ap = ss.load_approval()
            _nm = ap["품목명"].astype(str).str.replace(r"\s+", "", regex=True)
            nm2core = dict(zip(_nm, ap["주성분"].astype(str).map(_ing_core)))
            nm2new = (dict(zip(_nm, ap["신약구분"].astype(str).str.strip() == "신약"))
                      if "신약구분" in ap.columns else {})
            orig, allmax = {}, {}
            for k, e in prod.items():
                c = nm2core.get(k)
                if not c or len(c) < 2:
                    continue
                if c not in allmax or e > allmax[c]:
                    allmax[c] = e
                if nm2new.get(k) and (c not in orig or e > orig[c]):
                    orig[c] = e
            core = {c: orig.get(c, allmax[c]) for c in allmax}
        except Exception:
            pass
    return prod, brand, core

def _pms_lookup(value, unit, maps):
    """제품/성분 값 하나에 대한 PMS 만료일(없으면 None)."""
    prod, brand, core = maps
    s = str(value or "")
    if unit == "제품별":
        k = re.sub(r"\s+", "", s)
        if k in prod:
            return prod[k]
        b = re.split(r"[0-9(\[]", k)[0]
        return brand.get(b) if len(b) >= 2 else None
    c = _ing_core(s)
    return core.get(c) if len(c) >= 2 else None

# ── 한글 성분/제품명 → 영문 특허키 매핑 ────────────────────────────────────
@st.cache_data(show_spinner="성분 키 매핑 중…")
def _kor2engkey():
    """매출자료의 성분명·제품명은 한글이라 ing_key()가 빈 값이 되어 특허가 붙지 않는다.
    ① 허가자료(주성분 ↔ 주성분(영문)) ② 특허자료(INGR_NAME 한글 ↔ INGR_ENG_NAME)
    두 경로로 한글→영문 성분키를 만든다. 허가에 영문 성분명이 비어 있는 성분
    (예: 비베그론)은 ②로 연결된다.
    반환: (성분코어→영문키, 브랜드코어→영문키)"""
    if not HAS_REG:
        return {}, {}
    c2k, b2k = {}, {}

    def _add(kor_ing, kor_name, eng_key):
        """한글 성분코어/브랜드코어 → 영문키 등록 (먼저 넣은 쪽 우선)."""
        if not eng_key:
            return
        c = _ing_core(kor_ing)
        if len(c) >= 2:
            c2k.setdefault(c, eng_key)
        b = re.split(r"[0-9(\[]", re.sub(r"\s+", "", str(kor_name or "")))[0]
        if len(b) >= 2:
            b2k.setdefault(b, eng_key)

    # ① 허가자료 우선 (품목수가 많아 표기가 대표적)
    try:
        ap = ss.load_approval()
        _e = ap.get("주성분(영문)", pd.Series(dtype=str)).astype(str)
        for c, b, e in zip(ap.get("주성분", pd.Series(dtype=str)).astype(str),
                           ap.get("품목명", pd.Series(dtype=str)).astype(str), _e):
            if ss.is_single_ing(c, e):     # 조합제는 한글/영문 성분 순서가 엇갈려 제외
                _add(c, b, ss.ing_key(e))
    except Exception:
        pass

    # ② 특허자료의 한글 성분명으로 보완 (허가에 영문명이 비어 있는 성분 구제)
    try:
        pt = ss.load_patent()
        if "INGR_NAME" in pt.columns:
            for c, e, b, k in zip(pt["INGR_NAME"].astype(str),
                                  pt.get("INGR_ENG_NAME", pd.Series(dtype=str)).astype(str),
                                  pt.get("품목명", pd.Series(dtype=str)).astype(str),
                                  pt["_key"].astype(str)):
                if ss.is_single_ing(c, e):
                    _add(c, b, k)
    except Exception:
        pass

    return c2k, b2k

# 좌측 상단 제목
st.markdown(
    "<div style='color:#1f2a44;font-size:26px;font-weight:800;letter-spacing:-.01em'>🔎 제품 검토</div>"
    "<div style='color:#9aa3b2;font-size:12px;margin-bottom:8px'>매출 IQVIA·UBIST · 허가/특허/임상 식약처 · 약가 심평원+NHIS</div>",
    unsafe_allow_html=True)

PAGES = [
    ("🔍", "제품 종합분석", "search"),
    ("🎯", "제품 제안", "sugg"),
    ("💰", "매출 분석", "sales"),
    ("⚖️", "특허 분석", "patent"),
    ("🧪", "임상 분석", "clinical"),
    ("💊", "약가 분석", "price"),
    ("🤖", "AI 분석", "ai"),
    ("ℹ️", "상태", "status"),
]
# 페이지 상태는 URL 쿼리(?page=)로 관리 → 홈의 원형 아이콘(HTML 링크) 클릭으로도 전환됨
_qp = st.query_params.get("page")
PAGE = None if _qp in (None, "", "home") else _qp

# 홈 타일: (글자, 라벨, 키, 색) — UBIST 스타일 원형 아이콘
HOME_TILES = [
    ("종", "제품 종합분석", "search", "#5b6ef5"),
    ("제", "제품 제안", "sugg", "#e8842a"),
    ("매", "매출 분석", "sales", "#4e9e63"),
    ("특", "특허 분석", "patent", "#8b5cf6"),
    ("임", "임상 분석", "clinical", "#5fb0e8"),
    ("약", "약가 분석", "price", "#c9376b"),
    ("AI", "AI 분석", "ai", "#1c2030"),
    ("상", "상태", "status", "#6b7280"),
]

if PAGE is None:
    # st.button 기반(페이지 새로고침 없이 전환 → 로그인 상태 유지). 각 버튼을 원형 아이콘으로 스타일.
    _css = ["<style>",
            ".hlabel{text-align:center;color:#5b6472;font-size:15px;font-weight:600;margin:12px 0 26px;}"]
    for _ic, _lb, _k, _col in HOME_TILES:
        _kc = f"st-key-home_{_k}"
        _css.append(
            f".{_kc}{{width:132px;height:132px;border-radius:50%;background:#fff;border:1px solid #edeff3;"
            f"box-shadow:0 2px 10px rgba(20,30,55,.06);display:flex;align-items:center;justify-content:center;"
            f"margin:0 auto;transition:box-shadow .15s,border-color .15s,transform .15s;}}"
            f".{_kc}:hover{{border-color:#c7d2fe;box-shadow:0 10px 24px rgba(59,130,246,.22);transform:translateY(-2px);}}"
            f".{_kc} div[data-testid='stButton']{{width:100%;display:flex;justify-content:center;}}"
            f".{_kc} button{{background:{_col} !important;color:#fff !important;width:88px !important;height:88px !important;"
            f"min-height:88px !important;border:none !important;border-radius:22px !important;box-shadow:none !important;padding:0 !important;margin:0 auto !important;}}"
            f".{_kc} button:hover{{background:{_col} !important;filter:brightness(1.06);}}"
            f".{_kc} button p{{font-size:34px !important;font-weight:800 !important;line-height:1;margin:0;letter-spacing:-.02em;}}")
    _css.append("</style>")
    st.markdown("".join(_css), unsafe_allow_html=True)
    st.markdown("<div style='height:4vh'></div>", unsafe_allow_html=True)
    for _r in range(0, len(HOME_TILES), 4):
        _cols = st.columns(4)
        for _i, (_ic, _lb, _k, _col) in enumerate(HOME_TILES[_r:_r + 4]):
            with _cols[_i]:
                if st.button(_ic, key=f"home_{_k}"):
                    st.query_params["page"] = _k; st.rerun()
                st.markdown(f"<div class='hlabel'>{_lb}</div>", unsafe_allow_html=True)
    st.stop()

# ── 진입 후: 상단 텍스트 네비 (홈 + 8개, 글자만) ──
_nav = st.columns(len(PAGES) + 1)
if _nav[0].button("🏠 홈", key="nav_home", use_container_width=True):
    st.query_params["page"] = "home"; st.rerun()
for _i, (_icon, _label, _key) in enumerate(PAGES):
    if _nav[_i + 1].button(_label, key=f"nav_{_key}", use_container_width=True,
                           type=("primary" if PAGE == _key else "secondary")):
        st.query_params["page"] = _key; st.rerun()
st.markdown("<hr style='border:none;border-top:2px solid #3b82f6;margin:.4rem 0 1rem'>", unsafe_allow_html=True)

# ══════════════════════════ 제품 종합분석 ══════════════════════════
if PAGE == "search":
    c0, c1, c2 = st.columns([1, 3, 1])
    basis = c0.radio("검색 기준", ["제품명", "주성분"], key="s_basis")
    _opts = _approval_opts(basis)
    _kw = c1.text_input(f"{basis} 검색어 입력(일부만)", key="s_kw", placeholder="예: 리피토 / 아토르바스타틴")
    _fopts = _filter_opts(_opts, _kw)
    _cap_note = f" · 상위 {len(_fopts)}개 표시" if len(_fopts) >= 300 else ""
    sel = c1.selectbox(f"허가 {basis} 선택 ({len(_opts):,}개 중 검색{_cap_note})",
                       ["(선택하세요)"] + _fopts, key="s_sel")
    src1 = c2.radio("매출 자료원", ["IQVIA", "UBIST"], key="s1")
    q = "" if sel == "(선택하세요)" else sel
    # 선택값 해석: 제품명이면 '브랜드 핵심어'(숫자/괄호 앞) + 그 품목의 동일성분,
    #            주성분(코어)이면 그 코어. 동일성분은 성분 '코어'(코드/염/수화물 제거)로 매칭한다.
    q_brand, ing_keys, cores = q, set(), set()
    if q and basis == "제품명":
        q_brand = re.split(r"[0-9(\[]", re.sub(r"\s+", "", q))[0] or q
        if HAS_REG:
            _r = ss.load_approval()
            _r = _r[_r.get("품목명", pd.Series(dtype=str)).astype(str) == q]
            ing_keys = {k for k in (ss.ing_key(x) for x in _r.get("주성분(영문)", pd.Series(dtype=str)).dropna()) if k}
            cores = {c for c in (_ing_core(x) for x in _r.get("주성분", pd.Series(dtype=str)).dropna()) if len(c) >= 2}
    elif q:  # 주성분(코어) 선택
        cores = {_ing_core(q)} if len(_ing_core(q)) >= 2 else set()
        ing_keys = ss.resolve_keys(q)
    # 동일성분 허가행에서 영문키 보강 + 보험코드(EDI) 수집(제품명/띄어쓰기와 무관한 확실한 조인키)
    edis_self, edis_same = set(), set()
    if HAS_REG and (cores or basis == "제품명"):
        _ap0 = ss.load_approval()
        _edic = next((c for c in ["보험코드(EDI)", "보험코드", "보험EDI코드", "EDI코드", "주보험코드"] if c in _ap0.columns), None)
        _digits = lambda s: re.sub(r"\D", "", str(s))
        if basis == "제품명" and _edic:
            edis_self = {_digits(x) for x in _ap0[_ap0.get("품목명", pd.Series(dtype=str)).astype(str) == q][_edic].dropna()}
        if cores:
            _same = _ap0[_ap0.get("주성분", pd.Series(dtype=str)).astype(str).map(_ing_core).isin(cores)]
            ing_keys |= {k for k in (ss.ing_key(x) for x in _same.get("주성분(영문)", pd.Series(dtype=str)).dropna()) if k}
            if _edic:
                edis_same = {_digits(x) for x in _same[_edic].dropna()}
        edis_self = {e for e in edis_self if len(e) >= 6}
        edis_same = {e for e in edis_same if len(e) >= 6}

    def _ing_union(df, base):
        """동일성분(영문 _key) 매칭 행을 기존 결과에 추가(있는 것만 더함, 제거 없음)."""
        if ing_keys and "_key" in df.columns:
            add = df[df["_key"].isin(ing_keys)]
            if not add.empty:
                return pd.concat([base, add]).drop_duplicates()
        return base

    def _kor_union(df, base, col):
        """동일성분 코어(col을 정규화해 비교) 매칭 행을 추가(약가 주성분명·임상 성분명용)."""
        if cores and col in df.columns:
            add = df[df[col].astype(str).map(_ing_core).isin(cores)]
            if not add.empty:
                return pd.concat([base, add]).drop_duplicates()
        return base

    if q:
        df, yr, cfg = get(src1)
        # 매출 매칭: ① 브랜드 코어 ② 제형어(정/캡슐/주 등) 제거한 더 짧은 브랜드 토큰으로 제품명 검색
        _bt = re.sub(r"(서방정|속붕정|장용정|설하정|츄어블정|구강붕해정|정제|정|연질캡슐|경질캡슐|캡슐|"
                     r"주사액|주사|프리필드시린지|건조시럽|시럽|과립|산제|산|점안액|점비액|점이액|"
                     r"현탁액|흡입액|외용액|액|크림|연고|겔|패치|좌제)$", "", q_brand)
        _terms = {t for t in {q_brand, _bt} if len(t) >= 2}
        _prodn = _nospace(df[cfg["prod"]])   # 띄어쓰기 차이 흡수(예: '가스모틴 에스알정' ↔ '가스모틴에스알정')
        # 매출 파일의 보험EDI 코드 컬럼(있으면 이름/띄어쓰기와 무관하게 조인)
        _secol = next((c for c in df.columns
                       if "EDI" in str(c).upper() or ("보험" in str(c) and "코드" in str(c))
                       or str(c).strip() in ("보험코드", "주보험코드", "약가코드", "코드")), None)
        _sedi = df[_secol].astype(str).str.replace(r"\D", "", regex=True) if _secol else None
        _pm = pd.Series(False, index=df.index)
        for _t in _terms:
            _pm = _pm | _prodn.str.contains(_t, case=False, na=False, regex=False)
        if _sedi is not None and edis_self:            # 선택 제품의 EDI 직접 조인
            _pm = _pm | _sedi.isin(edis_self)
        hit, _by_ing = df[_pm], False
        if hit.empty and (cores or edis_same):         # 제품명 매칭 실패 → 동일성분 매출로 폴백
            _fm = pd.Series(False, index=df.index)
            if cores and cfg["ing"] in df.columns:
                _ingc = df[cfg["ing"]].astype(str)
                _fm = _ingc.map(_ing_core).isin(cores) | _ingc.apply(lambda s: any(c in s for c in cores))
            if _sedi is not None and edis_same:
                _fm = _fm | _sedi.isin(edis_same)
            hit = df[_fm]; _by_ing = not hit.empty
        if not hit.empty:
            s = hit[yr].sum(); cg = calc_cagr(s.tolist(), yr)
            k = st.columns(4)
            k[0].metric(("동일성분 " if _by_ing else "") + f"최신연도({year_of(yr[-1])}) 매출", f"{s.iloc[-1]/1e8:,.1f} 억")
            k[1].metric("매출 CAGR", f"{cg:+.1f}%" if cg is not None else "—")
            k[2].metric("매출상 제조사", f"{hit[cfg['maker']].nunique()} 곳")
            k[3].metric("품목 수" if _by_ing else "성분 수",
                        f"{len(hit)} 품목" if _by_ing else f"{hit[cfg['ing']].nunique()} 종")
            if _by_ing:
                st.caption("※ 제품명 직접 매칭이 없어 '동일성분' 매출을 합산해 보여줍니다.")
        else:
            st.info(f"{src1} 매출 매칭 없음 — 이 제품/성분이 {src1} 매출 파일에 없을 수 있어요 "
                    f"(‘매출 분석’ 탭에서 직접 검색해 확인). 허가/특허/임상/약가는 아래 확인)")
            # 진단: 앞 두 글자가 같은 매출 제품명 후보를 보여줘(자료엔 있는데 이름 표기가 다른 경우 확인용)
            _stub = (_bt or q_brand)[:2]
            if len(_stub) >= 2:
                _cand = sorted(df.loc[_prodn.str.startswith(_stub, na=False), cfg["prod"]].astype(str).unique())[:12]
                if _cand:
                    st.caption(f"참고 · {src1}에서 ‘{_stub}…’로 시작하는 제품명: " + ", ".join(_cand))
        tabs = st.tabs(["📋 허가", "⚖️ 특허", "🧪 임상", "💊 약가·이벤트", "🔁 재심사(PMS)", "🧬 DMF(동일성분)"])
        with tabs[0]:
            if HAS_REG:
                ap = ss.load_approval()
                m = _kor_union(ap, _ing_union(ap, contains(ap, ["품목명", "주성분", "주성분(영문)"], q)), "주성분")
                st.caption(f"허가 {len(m):,}건 · 업체 {m['업체명'].nunique()}곳 · 신약 {int((m['신약구분']=='신약').sum())}건")
                cc = [c for c in ["품목명", "업체명", "허가일자", "전문/일반", "주성분", "신약구분", "상태", "보험코드(EDI)"] if c in m.columns]
                st.dataframe(m[cc].sort_values("허가일자", ascending=False), use_container_width=True, height=300, hide_index=True)
            else: st.info("허가 데이터 미연결")
        with tabs[1]:
            if HAS_REG:
                pt = ss.load_patent()
                m = _kor_union(pt, _ing_union(pt, contains(pt, ["품목명", "INGR_ENG_NAME", "INGR_NAME"], q)), "INGR_NAME").copy()
                m["만료D(년)"] = ((m["_exp"] - TODAY).dt.days / 365.25).round(1)
                st.caption(f"특허 {len(m):,}건 · 등록 {int(m['DOMESTIC_PATENT_STATUS'].str.contains('등록', na=False).sum())}건")
                cmap = {"품목명": "품목명", "PATENT_GB_CODE": "유형", "PATENTEE": "특허권자", "DOMESTIC_PATENT_NO": "특허번호",
                        "DOMESTIC_PATENT_STATUS": "상태", "DOMESTIC_END_DATE": "만료일", "만료D(년)": "만료D(년)"}
                st.dataframe(m.rename(columns=cmap)[list(cmap.values())].sort_values("만료일", ascending=False),
                             use_container_width=True, height=300, hide_index=True)
            else: st.info("특허 데이터 미연결")
        with tabs[2]:
            if HAS_REG:
                cl = ss.load_clinical()
                if "성분명" in cl.columns:
                    cl = cl.copy(); cl["_key"] = cl["성분명"].map(ss.ing_key)
                m = _kor_union(cl, _ing_union(cl, contains(cl, ["제품명", "성분명"], q)), "성분명")
                _step = m["CLINIC_STEP_NM"] if "CLINIC_STEP_NM" in m.columns else pd.Series([], dtype=str)
                st.caption(f"임상 {len(m):,}건 · 생동(제네릭 개발) {int(_step.astype(str).str.contains('생동', na=False).sum())}건")
                cmap = {"제품명": "제품명", "성분명": "성분명", "CLINIC_STEP_NM": "단계", "TRGT_DISS_NM": "대상질환",
                        "STATUS": "상태", "CLST_APRV_DT": "승인일", "원개발사": "개발사"}
                cc = {k2: v for k2, v in cmap.items() if k2 in m.columns}
                st.dataframe(m.rename(columns=cc)[list(cc.values())].sort_values("승인일", ascending=False),
                             use_container_width=True, height=300, hide_index=True)
            else: st.info("임상 데이터 미연결")
        with tabs[3]:
            if not HAS_PRICE:
                st.info("약가 데이터 미연결")
            else:
                pr = ss.load_price()
                mp = _kor_union(pr, contains(pr, ["제품명", "주성분명"], q_brand), "주성분명").copy()
                if mp.empty:
                    st.info("약가 매칭 없음")
                else:
                    keys = {k for k in mp["_key"].unique() if k} or set(ing_keys)
                    st.caption(f"약가 매칭 {len(mp):,}건 · 제품 {mp['제품코드'].nunique()}개 · 성분키 {len(keys)}개")
                    counts = mp[mp["급여구분"] == "급여"]["제품명"].value_counts()
                    if counts.empty:
                        counts = mp["제품명"].value_counts()
                    opts = list(counts.index)
                    # 오리지널(신약) 우선 기본 선택, 없으면 최초 등재 품목
                    default_idx = 0
                    rkeys = ing_keys or ss.resolve_keys(q)
                    origs = ss.original_products(rkeys) if rkeys else []
                    prefixes = {re.split(r"[0-9]", re.sub(r"\s", "", o))[0] for o in origs}
                    prefixes = {p for p in prefixes if len(p) >= 2}
                    found = next((i for i, p in enumerate(opts)
                                  if any(re.sub(r"\s", "", str(p)).startswith(pre) for pre in prefixes)), None)
                    if found is None:
                        fd = mp[mp["급여구분"] == "급여"].groupby("제품명")["적용일자"].min()
                        if not fd.empty and fd.idxmin() in opts:
                            found = opts.index(fd.idxmin())
                    default_idx = found or 0
                    st.caption("※ 성분 검색 시 기본값은 오리지널(신약) 기준입니다. 다른 품목은 아래에서 선택하세요.")
                    sel = st.selectbox("약가 추이를 볼 품목", opts, index=default_idx, key="price_prod")
                    one = mp[mp["제품명"] == sel].sort_values("적용일자")
                    one_gy = one[one["급여구분"] == "급여"]
                    sel_keys = {k for k in one["_key"].unique() if k} or keys
                    if not one_gy.empty:
                        f0, f1 = one_gy["금액"].iloc[0], one_gy["금액"].iloc[-1]
                        km = st.columns(4)
                        km[0].metric("최초 급여가", f"{f0:,.0f} 원")
                        km[1].metric("최신 급여가", f"{f1:,.0f} 원")
                        km[2].metric("누적 인하율", f"{(f1-f0)/f0*100:+.1f}%" if f0 else "—")
                        km[3].metric("이력 기간", f"{one_gy['적용일자'].min().year}~{one_gy['적용일자'].max().year}")
                    mat_dates, use_dates, appr, reg = [], [], None, None
                    if HAS_REG:
                        pt = ss.load_patent()
                        reg = pt[pt["_key"].isin(sel_keys) & pt["DOMESTIC_PATENT_STATUS"].str.contains("등록", na=False) & pt["_exp"].notna()]
                        mat_dates = sorted(reg[reg["PATENT_GB_CODE"].str.contains("물질", na=False)]["_exp"].dropna().unique())
                        use_dates = sorted(reg[reg["PATENT_GB_CODE"].str.contains("용도", na=False)]["_exp"].dropna().unique())
                        ap = ss.load_approval()
                        appr = ap[ap["_key"].isin(sel_keys) & (ap["상태"] == "정상")].copy()
                        appr["_hd"] = pd.to_datetime(appr["허가일자"], format="%Y%m%d", errors="coerce")
                        appr = appr[appr["_hd"].notna()]
                    fig = go.Figure()
                    for nm, g in mp[(mp["급여구분"] == "급여") & (mp["제품명"] != sel)].groupby("제품명"):
                        if len(g) >= 2:
                            fig.add_trace(go.Scatter(x=g["적용일자"], y=g["금액"], mode="lines", line=dict(width=1, color="lightgray"),
                                                     line_shape="hv", opacity=0.35, showlegend=False, hoverinfo="skip"))
                    fig.add_trace(go.Scatter(x=one_gy["적용일자"], y=one_gy["금액"], mode="lines+markers", name=sel[:26],
                                             line=dict(color="royalblue", width=2.5), line_shape="hv"))
                    dele = one[one["급여구분"] == "삭제"]
                    if not dele.empty:
                        fig.add_trace(go.Scatter(x=dele["적용일자"], y=[0] * len(dele), mode="markers",
                                      marker=dict(color="black", symbol="x", size=11), name="급여삭제"))
                    for d in mat_dates:
                        dt = pd.Timestamp(d); fig.add_vline(x=dt, line=dict(color="crimson", dash="dash"))
                        fig.add_annotation(x=dt, y=1.0, yref="paper", text="물질특허만료", showarrow=False,
                                           font=dict(color="crimson", size=9), textangle=-90, xanchor="left")
                    for d in use_dates:
                        dt = pd.Timestamp(d); fig.add_vline(x=dt, line=dict(color="darkorange", dash="dot"))
                        fig.add_annotation(x=dt, y=0.82, yref="paper", text="용도특허만료", showarrow=False,
                                           font=dict(color="darkorange", size=9), textangle=-90, xanchor="left")
                    if appr is not None and not appr.empty:
                        by = (one_gy["금액"].max() if not one_gy.empty else 1) * 0.03
                        fig.add_trace(go.Scatter(x=appr["_hd"], y=[by] * len(appr), mode="markers",
                                      marker=dict(color="green", symbol="triangle-up", size=8, opacity=0.5), name="동일성분 등재(허가)",
                                      text=appr["품목명"], hovertemplate="%{x|%Y-%m-%d} 등재<br>%{text}<extra></extra>"))
                    fig.update_layout(height=440, title=f"{sel} — 급여 상한금액 전체 이력 + 등재·특허 만료",
                                      yaxis_title="상한금액(원)", legend=dict(orientation="h", y=-0.25), hovermode="x unified")
                    st.plotly_chart(fig, use_container_width=True)
                    st.caption("파란선=선택 품목 · 회색=동일성분 타 품목 · 초록△=동일성분 등재(허가) · 빨강=물질특허만료 · 주황=용도특허만료 · ✕=급여삭제")
                    if not one_gy.empty:
                        nyears = ss.nego_years(sel) if hasattr(ss, "nego_available") and ss.nego_available() else []
                        if nyears:
                            st.caption(f"🏛️ 공단 약가협상 완료 연도(공식·NHIS): {', '.join(nyears)}")
                        gg = one_gy.sort_values("적용일자").reset_index(drop=True)
                        drows = []
                        for i in range(1, len(gg)):
                            p0, p1 = gg.loc[i - 1, "금액"], gg.loc[i, "금액"]
                            if p1 < p0:
                                d = gg.loc[i, "적용일자"]
                                gen = 0
                                if HAS_REG and appr is not None and not appr.empty:
                                    gen = int(((appr["_hd"] >= d - pd.Timedelta(days=455)) & (appr["_hd"] <= d + pd.Timedelta(days=90))).sum())
                                near_mat = [pd.Timestamp(x).date() for x in mat_dates if abs((pd.Timestamp(x) - d).days) <= 365]
                                near_use = [pd.Timestamp(x).date() for x in use_dates if abs((pd.Timestamp(x) - d).days) <= 365]
                                drows.append({"적용일자": d.date(), "이전가": int(p0), "인하가": int(p1),
                                              "인하율%": round((p1 - p0) / p0 * 100, 1),
                                              "동일성분 허가등록(±15개월)": gen,
                                              "물질특허 만료(±1년)": str(near_mat[0]) if near_mat else "-",
                                              "용도특허 만료(±1년)": str(near_use[0]) if near_use else "-",
                                              "공단 협상완료(해당연도·공식)": "○" if str(d.year) in nyears else ""})
                        if drows:
                            st.markdown("**📉 가격 인하 시점 전후 사실** (공식 데이터, 사유 해석 없음)")
                            st.dataframe(pd.DataFrame(drows), hide_index=True, use_container_width=True)
        with tabs[4]:
            rj = ss.load_rejdge()
            if rj is None or rj.empty:
                st.info("재심사(PMS) 데이터 미연결 (구글시트 '재심사' 탭이 서비스계정에 공유됐는지 확인)")
            else:
                name_cols = [c for c in ["ITEM_NAME", "ENTP_NAME"] if c in rj.columns] \
                    or [c for c in rj.columns if "NAME" in c.upper()]
                m = contains(rj, name_cols, q_brand) if name_cols else rj.iloc[0:0]
                st.caption(f"재심사 매칭 {len(m):,}건 (전체 {len(rj):,}건)")
                if m.empty:
                    st.info("재심사 매칭 없음")
                else:
                    label = {"ITEM_NAME": "품목명", "ENTP_NAME": "업체명",
                             "REEXAM_CODE_NAME": "재심사구분", "REEXAM_CD_NM": "재심사구분",
                             "REEXAM_START_DATE": "재심사시작", "REEXAM_END_DATE": "재심사종료",
                             "RESULT_DATE": "결과일", "CLASS_NO_NAME": "분류", "CLASS_NO": "분류",
                             "ITEM_SEQ": "품목기준코드", "ITEM_NO": "품목번호", "BIZRNO": "사업자번호"}
                    prefer = [c for c in ["ITEM_NAME", "ENTP_NAME"] if c in m.columns]
                    ordered = prefer + [c for c in m.columns if c not in prefer]
                    st.dataframe(m[ordered].rename(columns={c: label.get(c, c) for c in ordered}),
                                 use_container_width=True, height=320, hide_index=True)
        with tabs[5]:
            dmf = ss.load_dmf()
            if dmf is None or dmf.empty:
                st.info("DMF 데이터 미연결 (구글시트의 DMF 탭이 서비스계정에 공유됐는지 확인)")
            else:
                ing_col = ss._dmf_ing_col(dmf)
                name_cols = [c for c in [ing_col, "ENTP_NAME"] if c]
                m = contains(dmf, name_cols, q) if name_cols else dmf.iloc[0:0]
                if ing_col and cores:  # 동일성분: 코어 일치 또는 부분일치(염/영문 병기 등 흡수)
                    def _dmf_hit(s):
                        sc = _ing_core(s); sn = re.sub(r"\s+", "", str(s))
                        return any(c and (c == sc or c in sn or (len(c) >= 3 and c in sc)) for c in cores)
                    same = dmf[dmf[ing_col].astype(str).apply(_dmf_hit)]
                    m = pd.concat([m, same]).drop_duplicates()
                st.caption(f"동일성분 DMF {len(m):,}건 (전체 {len(dmf):,}건)"
                           + (f" · 매칭 성분: {', '.join(sorted(cores)[:6])}" if cores else ""))
                if m.empty:
                    st.info("동일성분 DMF 매칭 없음")
                    # 진단: DMF 성분 컬럼과 실제 표기 예시(왜 매칭 안 되는지 확인용)
                    if ing_col:
                        _samp = sorted({str(x).strip() for x in dmf[ing_col].dropna() if str(x).strip()})[:12]
                        st.caption(f"· DMF 성분 컬럼: **{ing_col}** · 표기 예시: {', '.join(_samp) if _samp else '(비어있음)'}")
                    else:
                        st.caption(f"· DMF 성분 컬럼을 못 찾음 · 전체 컬럼: {', '.join(map(str, dmf.columns))}")
                else:
                    label = {"INGR_KOR_NAME": "성분(한글)", "INGR_KOR_NA": "성분(한글)", "INGR_NAME": "성분",
                             "ENTP_NAME": "업체명(수입/제조)", "MNFCTR_NAME": "제조사", "MNFCTR_NAM": "제조사",
                             "MANUF_COUNTRY_CODE_NM": "제조국", "DMF_PERMIT_DATE": "등록일",
                             "DMF_PERMIT_NO": "DMF번호", "DMF_PERMIT_ID": "DMF번호"}
                    prefer = [c for c in [ing_col, "ENTP_NAME"] if c and c in m.columns]
                    ordered = prefer + [c for c in m.columns if c not in prefer]
                    st.dataframe(m[ordered].rename(columns={c: label.get(c, c) for c in ordered}),
                                 use_container_width=True, height=320, hide_index=True)

# ══════════════════════════ 제품 제안 (필터 기반) ══════════════════════════
if PAGE == "sugg":
    st.subheader("🎯 제품 제안 — 조건 필터")
    a, b = st.columns(2)
    src2 = a.radio("매출 자료원", ["IQVIA", "UBIST"], horizontal=True, key="s2")
    unit = b.radio("분석 단위", ["성분별", "제품별"], horizontal=True, key="u2")
    df, yr, cfg = get(src2)
    m = sales_agg(df, yr, cfg["ing"] if unit == "성분별" else cfg["prod"], cfg["maker"])
    name = unit.replace("별", "")
    m = m.rename(columns={m.columns[0]: name})
    # 매출자료의 성분/제품명은 한글이라 ing_key()가 비어 특허가 안 붙는다.
    # 허가자료로 한글→영문 성분키를 연결하고, 빈 키는 제외한다.
    # (빈 키를 그대로 두면 모든 후보가 '영문 성분명이 빈 특허' 한 덩어리에 조인되어
    #  전부 같은 만료일이 붙는다)
    _c2k, _b2k = _kor2engkey()
    # 제품별일 때는 매출자료의 성분명 열로 제품→성분을 알아내 성분키로 연결한다
    # (브랜드명 표기가 허가/특허 자료와 조금씩 달라 이름만으로는 잘 안 붙는다)
    _p2i = {}
    if unit == "제품별" and cfg.get("ing") in df.columns:
        _p2i = (df.groupby(cfg["prod"])[cfg["ing"]]
                  .agg(lambda x: next((str(v) for v in x if str(v).strip()
                                       and str(v).strip().lower() != "nan"), "")).to_dict())

    def _rowkey(v):
        s0 = str(v or "")
        k = ss.ing_key(s0)
        if k:
            return k
        if unit == "성분별":
            return _c2k.get(_ing_core(s0), "")
        b = re.split(r"[0-9(\[]", re.sub(r"\s+", "", s0))[0]
        k = _b2k.get(b, "")
        if not k and _p2i.get(v):                       # 브랜드명 미매칭 → 성분명으로
            k = ss.ing_key(_p2i[v]) or _c2k.get(_ing_core(_p2i[v]), "")
        return k
    m["_key"] = m[name].map(_rowkey)
    if HAS_REG:
        _pbk = ss.patent_by_key()
        _pbk = _pbk[_pbk["_key"].astype(str).str.len() > 0]
        m = m.merge(_pbk, on="_key", how="left")

    # ATC(매출자료 기준) · PMS 만료일 추가
    _keycol = cfg["ing"] if unit == "성분별" else cfg["prod"]
    _atc = cfg.get("atc1")
    if _atc and _atc in df.columns:
        _amap = (df.groupby(_keycol)[_atc]
                   .agg(lambda x: next((str(v).strip() for v in x
                                        if str(v).strip() and str(v).strip().lower() != "nan"), "")))
        m["ATC"] = m[name].map(_amap).fillna("")
    try:
        _pmaps = _pms_expiry_maps()
    except Exception:
        _pmaps = ({}, {}, {})
    if any(_pmaps):
        m["_pms"] = m[name].map(lambda v: _pms_lookup(v, unit, _pmaps))

    st.markdown("**필터 조건**")
    f1, f2, f3, f4 = st.columns(4)
    min_size = f1.number_input("① 최소 시장규모(억, 최신연도)", value=50, step=10, min_value=0)
    min_cagr = f2.number_input("② 최소 성장 CAGR(%)", value=0.0, step=5.0)
    cy = int(TODAY.year)
    with f3:
        ptypes = st.multiselect("③ 특허 만료 기준 유형", ["물질", "용도"], default=["물질"] if HAS_REG else [])
        sel_year = st.selectbox("이 연도까지 특허 만료", list(range(cy, cy + 16)), index=5,
                                help="선택한 유형 특허가 이 연도까지 만료(또는 없음)인 후보만")
    with f4:
        _atc_opts = sorted({str(x).strip() for x in m.get("ATC", pd.Series(dtype=str))
                            if str(x).strip() and str(x).strip().lower() != "nan"})
        atc_sel = st.multiselect("④ ATC 계열", _atc_opts, default=[],
                                 help="비우면 전체. 선택하면 해당 계열만")
        pms_sel = st.selectbox("⑤ 이 연도까지 PMS 만료",
                               ["적용 안 함"] + [str(y) for y in range(cy, cy + 16)], index=0,
                               help="선택 연도까지 재심사(PMS)가 만료됐거나 PMS 대상이 아닌 후보만. 성분별은 그 성분 오리지널(신약)의 재심사 기준")

    res = m[(m["시장규모"] >= min_size * 1e8) & (m["CAGR"].fillna(-1e9) >= min_cagr)].copy()
    if HAS_REG and ptypes:
        def clear_by(row):
            for t in ptypes:
                exp = row.get(f"특허만료_{t}")
                if pd.notna(exp) and pd.Timestamp(exp).year > sel_year:
                    return False
            return True
        res = res[res.apply(clear_by, axis=1)]

    if atc_sel and "ATC" in res.columns:
        res = res[res["ATC"].astype(str).str.strip().isin(atc_sel)]
    if pms_sel != "적용 안 함" and "_pms" in res.columns:
        _pe = pd.to_datetime(res["_pms"], errors="coerce")
        res = res[_pe.isna() | (_pe.dt.year <= int(pms_sel))]   # 만료했거나 PMS 없음

    res = res.sort_values("시장규모", ascending=False)
    res["시장규모(억)"] = (res["시장규모"] / 1e8).round(1)
    res["CAGR(%)"] = res["CAGR"].round(1)
    if HAS_REG:
        res["물질특허 만료"] = pd.to_datetime(res.get("특허만료_물질")).dt.date.astype("string")
        res["용도특허 만료"] = pd.to_datetime(res.get("특허만료_용도")).dt.date.astype("string")
    _pms_lbl = "PMS 만료(오리지널)" if unit == "성분별" else "PMS 만료"
    if "_pms" in res.columns:
        res[_pms_lbl] = pd.to_datetime(res["_pms"]).dt.date.astype("string")
    cols = ([name] + (["ATC"] if "ATC" in res.columns else [])
            + ["시장규모(억)", "CAGR(%)"]
            + (["물질특허 만료", "용도특허 만료"] if HAS_REG else [])
            + ([_pms_lbl] if _pms_lbl in res.columns else []))
    st.markdown(f"#### ✅ 조건 충족 후보 {len(res):,}개")
    st.dataframe(res[cols].head(200), use_container_width=True, height=430, hide_index=True)
    st.download_button("⬇️ CSV", res[cols].to_csv(index=False).encode("utf-8-sig"), file_name=f"제품제안_{src2}_{unit}.csv")
    st.caption("가중치 점수 없이, 큰 시장·고성장·특허만료·ATC·PMS 조건을 직접 필터링합니다. · "
               "ATC는 매출자료 기준 · PMS 만료=재심사시작일+재심사기간(식약처 자료에 종료일 항목이 없어 산출값), 성분별은 오리지널(신약) 기준")

# ══════════════════════════ 매출 분석 (기존 app.py 4개 탭 그대로) ══════════════════════════
if PAGE == "sales":
    # 매출(IQVIA·UBIST)은 데이터가 커서 메모리를 많이 쓴다.
    # 무료 플랜 안정성을 위해 버튼을 눌렀을 때만 불러온다(기본은 미로딩).
    if st.session_state.get("_load_sales"):
        import sales_analysis
        sales_analysis.render()
        if st.button("🧹 매출 데이터 닫기(메모리 절약)", key="btn_unload_sales"):
            st.session_state["_load_sales"] = False
            st.cache_data.clear()
            st.rerun()
    else:
        st.info("매출 분석 데이터(IQVIA·UBIST)는 용량이 커서 필요할 때만 불러옵니다.\n\n"
                "아래 버튼을 누르면 매출 대시보드가 열립니다.")
        if st.button("📊 매출 분석 불러오기", key="btn_load_sales", type="primary"):
            st.session_state["_load_sales"] = True
            st.rerun()

# ══════════════════════════ 특허 분석 ══════════════════════════
if PAGE == "patent":
    st.subheader("⚖️ 특허 분석 — 조건별")
    if not HAS_REG:
        st.info("특허 데이터 미연결")
    else:
        pt = ss.load_patent().copy()
        f = st.columns(4)
        pt = isin(pt, "PATENT_GB_CODE", msel(f[0], "특허유형", pt["PATENT_GB_CODE"], "p_type"))
        pt = isin(pt, "DOMESTIC_PATENT_STATUS", msel(f[1], "상태", pt["DOMESTIC_PATENT_STATUS"], "p_stat", default=["등록"]))
        patee = f[2].text_input("특허권자 검색", key="p_ee")
        kw = f[3].text_input("품목/성분 검색", key="p_kw")
        if patee: pt = pt[pt["PATENTEE"].astype(str).str.contains(patee, case=False, na=False, regex=False)]
        if kw: pt = contains(pt, ["품목명", "INGR_ENG_NAME", "INGR_NAME"], kw)
        pt = pt[pt["_exp"].notna()]
        if pt.empty:
            st.warning("조건에 맞는 특허 없음")
        else:
            yrs = pt["_exp"].dt.year; lo, hi = int(yrs.min()), int(yrs.max())
            if lo >= hi:
                # 만료 연도가 한 해뿐이면 슬라이더(min==max) 대신 그대로 사용
                st.caption(f"만료 연도: {lo}년 (해당 연도 특허만 존재)")
                rng = (lo, hi)
            else:
                # 기본값은 검색 결과 전체 구간. (예전처럼 '올해±' 구간을 기본으로 두면
                #  품목을 검색해도 일부 특허가 잘려 보여 건수가 적게 나온다)
                # key를 구간에 묶어 검색이 바뀌면 이전 선택 범위가 따라오지 않게 한다.
                rng = st.slider("만료 연도 범위", lo, hi, (lo, hi), key=f"p_rng_{lo}_{hi}")
                if (rng[0], rng[1]) != (lo, hi):
                    st.caption(f"전체 {lo}~{hi}년 중 {rng[0]}~{rng[1]}년만 보는 중")
            pt = pt[(yrs >= rng[0]) & (yrs <= rng[1])]
            pt["만료D(년)"] = ((pt["_exp"] - TODAY).dt.days / 365.25).round(1)
            k = st.columns(3)
            k[0].metric("특허 건수", f"{len(pt):,}")
            k[1].metric("특허권자 수", f"{pt['PATENTEE'].nunique():,}")
            k[2].metric("향후 3년 내 만료", f"{int(((pt['만료D(년)']>=0)&(pt['만료D(년)']<3)).sum()):,}")
            cnt = pt.groupby(pt["_exp"].dt.year).size().reset_index(); cnt.columns = ["만료연도", "건수"]
            st.plotly_chart(px.bar(cnt, x="만료연도", y="건수", title="만료 연도별 특허 건수"), use_container_width=True)
            cmap = {"품목명": "품목명", "INGR_NAME": "성분", "PATENT_GB_CODE": "유형", "PATENTEE": "특허권자",
                    "DOMESTIC_PATENT_STATUS": "상태", "DOMESTIC_END_DATE": "만료일", "만료D(년)": "만료D(년)"}
            cc = {k2: v for k2, v in cmap.items() if k2 in pt.columns}
            st.dataframe(pt.rename(columns=cc)[list(cc.values())].sort_values("만료일"), use_container_width=True, height=360, hide_index=True)

# ══════════════════════════ 임상 분석 ══════════════════════════
if PAGE == "clinical":
    st.subheader("🧪 임상 분석 — 조건별")
    try:
        cl = ss.load_clinical().copy()
    except Exception:
        cl = None
    if cl is None or cl.empty:
        st.info("임상 데이터 미연결")
    else:
        def _cc(cands):
            for c in cands:
                if c in cl.columns:
                    return c
            return None
        step_c = _cc(["CLINIC_STEP_NM"])
        dis_c  = _cc(["TRGT_DISS_NM"])
        stat_c = _cc(["STATUS"])
        dev_c  = _cc(["원개발사"])
        date_c = _cc(["CLST_APRV_DT"])
        prod_c = _cc(["제품명"])
        ing_c  = _cc(["성분명"])

        f = st.columns(4)
        f_step = msel(f[0], "임상 단계", cl[step_c], "c_step") if step_c else []
        f_stat = msel(f[1], "상태", cl[stat_c], "c_stat") if stat_c else []
        dev_kw = f[2].text_input("개발사 검색", key="c_dev")
        kw = f[3].text_input("성분/제품 검색", key="c_kw")

        f2 = st.columns([3, 1])
        f_dis = msel(f2[0], "대상질환", cl[dis_c], "c_dis") if dis_c else []

        m = cl
        if step_c: m = isin(m, step_c, f_step)
        if stat_c: m = isin(m, stat_c, f_stat)
        if dis_c:  m = isin(m, dis_c, f_dis)
        if dev_c and dev_kw:
            m = m[m[dev_c].astype(str).str.contains(dev_kw, case=False, na=False, regex=False)]
        if kw:
            m = contains(m, [c for c in [prod_c, ing_c] if c], kw)

        # 요약 지표
        bio = int(m[step_c].astype(str).str.contains("생동", na=False).sum()) if step_c else 0
        ongoing = int(m[stat_c].astype(str).str.contains("승인|진행|모집", na=False).sum()) if stat_c else 0
        k = st.columns(4)
        k[0].metric("임상 건수", f"{len(m):,}")
        k[1].metric("생동(제네릭 개발) 건수", f"{bio:,}")
        k[2].metric("진행/승인 건수", f"{ongoing:,}")
        k[3].metric("개발사 수", f"{m[dev_c].nunique():,}" if dev_c else "—")

        if m.empty:
            st.info("조건에 맞는 임상 없음")
        else:
            g1, g2 = st.columns(2)
            if step_c:
                vc = m[step_c].astype(str).value_counts().head(15).reset_index()
                vc.columns = ["단계", "건수"]
                g1.plotly_chart(px.bar(vc, x="단계", y="건수", title="임상 단계별 건수"), use_container_width=True)
            if date_c:
                yr = pd.to_datetime(m[date_c], errors="coerce").dt.year.dropna()
                if not yr.empty:
                    yc = yr.astype(int).value_counts().sort_index().reset_index()
                    yc.columns = ["연도", "건수"]
                    g2.plotly_chart(px.bar(yc, x="연도", y="건수", title="연도별 임상 승인 추이"), use_container_width=True)

            cmap = {"제품명": "제품명", "성분명": "성분명", "CLINIC_STEP_NM": "단계",
                    "TRGT_DISS_NM": "대상질환", "STATUS": "상태", "CLST_APRV_DT": "승인일", "원개발사": "개발사"}
            cc = {k2: v for k2, v in cmap.items() if k2 in m.columns}
            view = m.rename(columns=cc)[list(cc.values())]
            if "승인일" in view.columns:
                view = view.sort_values("승인일", ascending=False)
            st.dataframe(view, use_container_width=True, height=380, hide_index=True)

# ══════════════════════════ 약가 분석 ══════════════════════════
if PAGE == "price":
    st.subheader("💊 약가 분석")
    if not HAS_PRICE:
        st.info("약가 데이터 미연결")
    else:
        pr = ss.load_price()
        # ── 검색 기반 약가 변동 그래프 (성분→오리지널 / 제품→해당 제품) ──
        st.markdown("**🔎 약가 변동 그래프** · 성분 선택 시 오리지널(신약) 기준, 제품 선택 시 해당 제품")
        _pc = st.columns([1, 1, 2])
        _pbasis = _pc[0].radio("기준", ["성분", "제품"], key="ptrend_basis", horizontal=True)
        _popts = _approval_opts("주성분" if _pbasis == "성분" else "제품명")
        _pkw = _pc[1].text_input(f"{_pbasis} 검색어", key="ptrend_kw", placeholder="일부만 입력")
        _pfo = _filter_opts(_popts, _pkw)
        _psel = _pc[2].selectbox(f"허가 {_pbasis} 선택 ({len(_popts):,}개 중 검색)",
                                 ["(선택하세요)"] + _pfo, key="ptrend_sel")
        if _psel and _psel != "(선택하세요)" and "급여구분" in pr.columns:
            _codes, _title = _price_trend_codes(pr, _pbasis, _psel)
            _h = pr[(pr["제품코드"].isin(_codes)) & (pr["급여구분"] == "급여")].sort_values("적용일자")
            if _h.empty:
                st.info("해당 급여 약가 이력이 없습니다.")
            else:
                _show = list(_h.groupby("제품명").size().sort_values(ascending=False).index[:30])
                _fig = go.Figure()
                for _nm, _g in _h[_h["제품명"].isin(_show)].groupby("제품명"):
                    _fig.add_trace(go.Scatter(x=_g["적용일자"], y=_g["금액"], mode="lines+markers",
                                              name=str(_nm)[:26], line_shape="hv"))
                _fig.update_layout(height=430, title=_title, yaxis_title="상한금액(원)",
                                   legend=dict(orientation="h", y=-0.3), hovermode="x unified")
                st.plotly_chart(_fig, use_container_width=True)
                st.caption(f"대상 품목 {_h['제품코드'].nunique()}개 · 그래프는 이력 많은 상위 30개 표시")
        st.divider()

        st.markdown("**📋 조건별 최신 약가 · 연도별**")
        def _ser(name):  # 컬럼이 없어도 안전하게 빈 시리즈 반환
            return pr[name] if name in pr.columns else pd.Series([], dtype=object)
        latest = pr.sort_values("적용일자").groupby("제품코드").tail(1).copy()
        f = st.columns(4)
        pay_def = ["급여"] if "급여구분" in pr.columns else []
        latest = isin(latest, "급여구분", msel(f[0], "급여구분", _ser("급여구분"), "pr_pay", default=pay_def))
        latest = isin(latest, "투여", msel(f[1], "투여", _ser("투여"), "pr_route"))
        latest = isin(latest, "분류", msel(f[2], "분류(코드)", _ser("분류"), "pr_cls"))
        ent = f[3].text_input("업체 검색", key="pr_ent")
        kw = st.text_input("제품/성분 검색", key="pr_kw")
        if ent and "업체명" in latest.columns:
            latest = latest[latest["업체명"].astype(str).str.contains(ent, case=False, na=False, regex=False)]
        if kw: latest = contains(latest, ["제품명", "주성분명"], kw)
        if latest.empty:
            st.warning("조건에 맞는 약가 없음")
        else:
            k = st.columns(3)
            gy = latest[latest["급여구분"] == "급여"]
            k[0].metric("품목 수", f"{len(latest):,}")
            k[1].metric("상한금액 (최고)", f"{gy['금액'].max():,.0f} 원" if not gy.empty else "—")
            k[2].metric("하한금액 (최저)", f"{gy['금액'].min():,.0f} 원" if not gy.empty else "—")
            # ⭐ 오리지널(신약) 분류 + 추이 그래프
            if HAS_REG and not gy.empty and len(latest) <= 2000:
                keys_p = {ss.ing_key(x) for x in latest["주성분명"].dropna() if ss.ing_key(x)}
                prefixes = {re.split(r"[0-9]", re.sub(r"\s", "", o))[0] for o in ss.original_products(keys_p)}
                prefixes = {p for p in prefixes if len(p) >= 2}
                is_orig = latest["제품명"].map(lambda n: any(re.sub(r"\s", "", str(n)).startswith(p) for p in prefixes)) \
                    if prefixes else pd.Series(False, index=latest.index)
                orig_latest = latest[is_orig & (latest["급여구분"] == "급여")]
                st.markdown(f"**⭐ 오리지널(신약) {len(orig_latest)}개** (전체 급여 {len(gy)}개 중)")
                if not orig_latest.empty:
                    ocodes = orig_latest["제품코드"].tolist()
                    if len(ocodes) <= 40:
                        oh = pr[(pr["제품코드"].isin(ocodes)) & (pr["급여구분"] == "급여")].sort_values("적용일자")
                        figo = go.Figure()
                        for nm, g in oh.groupby("제품명"):
                            figo.add_trace(go.Scatter(x=g["적용일자"], y=g["금액"], mode="lines+markers", name=nm[:24], line_shape="hv"))
                        figo.update_layout(height=360, title="오리지널(신약) 상한금액 추이", yaxis_title="상한금액(원)", legend=dict(orientation="h", y=-0.3))
                        st.plotly_chart(figo, use_container_width=True)
                    else:
                        st.caption(f"오리지널 {len(ocodes)}개 — 그래프는 40개 이하로 좁혀야 표시됩니다.")
                    st.dataframe(orig_latest[["제품명", "업체명", "주성분명", "적용일자", "금액"]].sort_values("금액", ascending=False),
                                 use_container_width=True, height=220, hide_index=True)
            elif HAS_REG and len(latest) > 2000:
                st.caption("⭐ 오리지널 분류·그래프는 필터를 좁히면(투여·분류·업체·제품/성분 검색) 표시됩니다.")
            st.markdown("**📅 연도별 상한금액** (연도말 기준, 0=급여삭제, 빈칸=미등재)")
            codes = latest["제품코드"].tolist()
            if len(codes) > 400:
                st.info(f"필터 결과 {len(codes):,}개 — 400개 이하로 좁히면 연도별 표가 표시됩니다. (업체/제품·성분 검색 활용)")
            else:
                ym = yearly_matrix(pr, codes, dict(zip(latest["제품코드"], latest["제품명"])))
                st.dataframe(ym, use_container_width=True, height=380)
                st.download_button("⬇️ 연도별 약가 CSV", ym.to_csv(index=False).encode("utf-8-sig"), file_name="연도별약가.csv")
            with st.expander("📉 가격 인하폭 Top 20 / 급여중지(삭제) 목록"):
                hist = pr[(pr["제품코드"].isin(codes)) & (pr["급여구분"] == "급여")].sort_values("적용일자")
                first = hist.groupby("제품코드").first()["금액"].rename("최초가")
                last = hist.groupby("제품코드").last()["금액"].rename("최신가")
                drop = pd.concat([first, last], axis=1).dropna()
                drop["인하율%"] = ((drop["최신가"] - drop["최초가"]) / drop["최초가"] * 100).round(1)
                _meta = [c for c in ["제품명", "업체명"] if c in latest.columns]
                drop = drop.join(latest.set_index("제품코드")[_meta]).reset_index()
                st.dataframe(drop.nsmallest(20, "인하율%")[[c for c in ["제품명", "업체명", "최초가", "최신가", "인하율%"] if c in drop.columns]],
                             use_container_width=True, height=240, hide_index=True)
                _dcols = [c for c in ["제품명", "업체명", "주성분명", "적용일자"] if c in latest.columns]
                deleted = latest[latest["급여구분"] == "삭제"][_dcols]
                st.dataframe(deleted.sort_values("적용일자", ascending=False).head(30), use_container_width=True, height=200, hide_index=True)

# ══════════════════════════ AI 분석 (Gemini) ══════════════════════════
if PAGE == "ai":
    import gemini_ai
    st.subheader("🤖 AI 분석 (Gemini)")

    def _kor_ings(qterm):
        """검색어에 해당하는 허가 주성분 코어(코드/염 제거) 집합 (동일성분 매칭용)."""
        kor = set()
        try:
            if HAS_REG:
                ap = ss.load_approval(); hitap = contains(ap, ["품목명", "주성분", "주성분(영문)"], qterm)
                for v in hitap["주성분"].dropna().astype(str):
                    c = _ing_core(v)
                    if len(c) >= 2:
                        kor.add(c)
            c0 = _ing_core(qterm)
            if len(c0) >= 2:
                kor.add(c0)
        except Exception:
            pass
        return kor

    def _dmf_match(qterm):
        """DMF 동일성분 매칭 (DMF 탭과 동일 로직). 매칭 DF 또는 None."""
        try:
            if not (hasattr(ss, "dmf_available") and ss.dmf_available()):
                return None
            dmf = ss.load_dmf()
            if dmf is None or dmf.empty:
                return None
            ic = ss._dmf_ing_col(dmf)
            m = contains(dmf, [c for c in [ic, "ENTP_NAME"] if c], qterm)
            kor = _kor_ings(qterm)
            if ic and kor:
                same = dmf[dmf[ic].astype(str).map(_ing_core).isin(kor)]
                m = pd.concat([m, same]).drop_duplicates()
            return m
        except Exception:
            return None

    def _pms_match(qterm):
        """재심사(PMS) 매칭 DF 또는 None."""
        try:
            if not (hasattr(ss, "rejdge_available") and ss.rejdge_available()):
                return None
            rj = ss.load_rejdge()
            if rj is None or rj.empty:
                return None
            ncols = [c for c in ["ITEM_NAME", "ENTP_NAME"] if c in rj.columns] or [c for c in rj.columns if "NAME" in c.upper()]
            return contains(rj, ncols, qterm) if ncols else None
        except Exception:
            return None

    def build_ai_context(qterm):
        parts = []
        if not qterm:
            return "(제품/성분 미지정)"
        try:
            df, yr, cfg = get("IQVIA"); hit = sales_search(df, cfg, qterm)
            if not hit.empty:
                s = hit[yr].sum(); cg = calc_cagr(s.tolist(), yr)
                parts.append(f"[매출·IQVIA] 최신연도({year_of(yr[-1])}) {s.iloc[-1]/1e8:,.1f}억 · CAGR {('%+.1f%%'%cg) if cg is not None else 'N/A'} · 제조사 {hit[cfg['maker']].nunique()}곳 · 성분 {hit[cfg['ing']].nunique()}종")
        except Exception:
            pass
        if HAS_REG:
            ap = ss.load_approval(); am = contains(ap, ["품목명", "주성분", "주성분(영문)"], qterm)
            if not am.empty:
                parts.append(f"[허가] {len(am):,}건 · 업체 {am['업체명'].nunique()}곳 · 신약(오리지널) {int((am['신약구분']=='신약').sum())}건")
            pt = ss.load_patent()
            pm = contains(pt, ["품목명", "INGR_ENG_NAME", "INGR_NAME"], qterm)  # 특허 탭과 동일한 텍스트 매칭
            keys = ss.resolve_keys(qterm)
            if keys:
                pm = pd.concat([pm, pt[pt['_key'].isin(keys)]]).drop_duplicates()
            reg = pm[pm['DOMESTIC_PATENT_STATUS'].astype(str).str.contains('등록', na=False) & pm['_exp'].notna()]
            if not pm.empty:
                mat = reg[reg['PATENT_GB_CODE'].astype(str).str.contains('물질', na=False)]['_exp'].max()
                use = reg[reg['PATENT_GB_CODE'].astype(str).str.contains('용도', na=False)]['_exp'].max()
                parts.append(f"[특허] 매칭 {len(pm)}건 · 등록 {len(reg)}건 · 물질특허 만료 {mat.date() if pd.notna(mat) else '-'} · 용도특허 만료 {use.date() if pd.notna(use) else '-'}")
        if HAS_PRICE:
            pr = ss.load_price(); mp = contains(pr, ["제품명", "주성분명"], qterm)
            ls = mp[mp['급여구분'] == '급여'].sort_values('적용일자').groupby('제품코드').tail(1)
            if not ls.empty:
                parts.append(f"[약가] 급여 품목 {ls['제품코드'].nunique()}개 · 상한금액 최고 {ls['금액'].max():,.0f}원 · 최저 {ls['금액'].min():,.0f}원")
            ny = ss.nego_years(qterm) if hasattr(ss, 'nego_available') and ss.nego_available() else []
            if ny:
                parts.append(f"[공단 약가협상 완료 연도(공식)] {', '.join(ny)}")
        # 임상(동일성분)
        try:
            cl = ss.load_clinical(); cm = contains(cl, ["제품명", "성분명"], qterm)
            if not cm.empty:
                steps = ", ".join(cm["CLINIC_STEP_NM"].dropna().astype(str).value_counts().head(6).index) if "CLINIC_STEP_NM" in cm.columns else ""
                parts.append(f"[임상·동일성분] {len(cm)}건 · 단계: {steps or '미상'}")
        except Exception:
            pass
        # 재심사(PMS)
        try:
            rm = _pms_match(qterm)
            if rm is not None and not rm.empty:
                show = [c for c in rm.columns if any(k in c.upper() for k in ["REEXAM", "YEAR", "DATE"])][:4]
                parts.append(f"[PMS·재심사] 매칭 {len(rm)}건" + (f" · 예시 {rm.iloc[0][show].to_dict()}" if show else ""))
        except Exception:
            pass
        # DMF(동일성분)
        try:
            dm = _dmf_match(qterm)
            if dm is not None and not dm.empty and "ENTP_NAME" in dm.columns:
                ents = ", ".join(dm["ENTP_NAME"].dropna().astype(str).unique()[:15])
                parts.append(f"[DMF 등록업체(동일성분)] {len(dm)}건 · {ents}")
        except Exception:
            pass
        return "\n".join(parts) if parts else "(데이터 매칭 없음)"

    def build_review_context(qterm):
        """제품 검토서 양식용 데이터 수집(항목별)."""
        L = []
        try:
            if HAS_REG:
                ap = ss.load_approval(); am = contains(ap, ["품목명", "주성분", "주성분(영문)"], qterm)
                if not am.empty:
                    gubun = ", ".join(am["전문/일반"].dropna().astype(str).unique()[:3]) if "전문/일반" in am.columns else ""
                    ings = ", ".join(am["주성분"].dropna().astype(str).unique()[:6]) if "주성분" in am.columns else ""
                    newd = int((am["신약구분"] == "신약").sum()) if "신약구분" in am.columns else 0
                    reps = ", ".join(am["품목명"].dropna().astype(str).unique()[:8])
                    L.append(f"[허가] 매칭 {len(am)}건 · 업체 {am['업체명'].nunique()}곳 · 신약(오리지널) {newd}건")
                    L.append(f"  허가분류(전문/일반): {gubun or '미상'}")
                    L.append(f"  주성분: {ings or '미상'}")
                    L.append(f"  대표 품목명: {reps}")
        except Exception:
            pass
        try:
            if HAS_PRICE:
                pr = ss.load_price(); mp = contains(pr, ["제품명", "주성분명"], qterm)
                ls = mp[mp["급여구분"] == "급여"].sort_values("적용일자").groupby("제품코드").tail(1)
                if not ls.empty:
                    L.append(f"[약가] 급여 품목 {ls['제품코드'].nunique()}개 · 상한금액 {ls['금액'].min():,.0f}~{ls['금액'].max():,.0f}원")
        except Exception:
            pass
        try:
            rm = _pms_match(qterm)
            if rm is not None and not rm.empty:
                show = [c for c in rm.columns if any(k in c.upper() for k in ["REEXAM", "YEAR", "DATE", "CODE"])][:5]
                L.append(f"[PMS·재심사] 매칭 {len(rm)}건 · 예시: {rm.iloc[0][show].to_dict() if show else '컬럼확인필요'}")
        except Exception:
            pass
        try:
            if HAS_REG:
                pt = ss.load_patent()
                pm = contains(pt, ["품목명", "INGR_ENG_NAME", "INGR_NAME"], qterm)  # 특허 탭과 동일한 텍스트 매칭
                keys = ss.resolve_keys(qterm)
                if keys:
                    pm = pd.concat([pm, pt[pt["_key"].isin(keys)]]).drop_duplicates()
                reg = pm[pm["DOMESTIC_PATENT_STATUS"].astype(str).str.contains("등록", na=False) & pm["_exp"].notna()]
                if not pm.empty:
                    mat = reg[reg["PATENT_GB_CODE"].astype(str).str.contains("물질", na=False)]["_exp"].max()
                    use = reg[reg["PATENT_GB_CODE"].astype(str).str.contains("용도", na=False)]["_exp"].max()
                    pats = ", ".join(pm["PATENTEE"].dropna().astype(str).unique()[:5]) if "PATENTEE" in pm.columns else ""
                    L.append(f"[특허] 매칭 {len(pm)}건 · 등록 {len(reg)}건 · 물질특허 만료 {mat.date() if pd.notna(mat) else '-'} · 용도특허 만료 {use.date() if pd.notna(use) else '-'}")
                    if pats:
                        L.append(f"  특허권자: {pats}")
        except Exception:
            pass
        try:
            cl = ss.load_clinical(); cm = contains(cl, ["제품명", "성분명"], qterm)
            if not cm.empty:
                steps = ", ".join(cm["CLINIC_STEP_NM"].dropna().astype(str).value_counts().head(6).index) if "CLINIC_STEP_NM" in cm.columns else ""
                L.append(f"[임상·동일성분] {len(cm)}건 · 단계: {steps or '미상'}")
        except Exception:
            pass
        try:
            df, yr, cfg = get("IQVIA"); hit = sales_search(df, cfg, qterm)
            if not hit.empty and yr:
                last5 = yr[-5:]; s = hit[last5].sum()
                mm = " · ".join(f"{year_of(y)}년 {s[y] / 1e6:,.0f}" for y in last5)
                L.append(f"[매출·IQVIA 최근5개년(백만원)] {mm}")
        except Exception:
            pass
        try:
            dm = _dmf_match(qterm)
            if dm is not None and not dm.empty and "ENTP_NAME" in dm.columns:
                ents = ", ".join(dm["ENTP_NAME"].dropna().astype(str).unique()[:20])
                L.append(f"[DMF 등록업체(동일성분)] {len(dm)}건 · {ents}")
        except Exception:
            pass
        return "\n".join(L) if L else "(데이터 매칭 없음)"

    REVIEW_TEMPLATE = ("제품명 | 허가분류 | 약가 | 주성분/함량 | 효능/효과 | PMS | 용법/용량 | "
                       "관련 특허 | 임상시험 진행 현황(동일성분) | 매출액(최근 5개년 연간, 백만원) | DMF 등록 업체")

    if not gemini_ai.available():
        st.info("Gemini API 키가 없습니다. 배포 시 Secrets에 `GEMINI_API_KEY`를 설정하면 활성화됩니다.")
    else:
        st.caption(f"모델: {gemini_ai.model_name()}")

    mode = st.radio("모드", ["💬 자유 질문", "📋 제품 검토서(양식)"], horizontal=True, key="ai_mode")
    aq = st.text_input("제품/성분 (자유 질문은 비워도 됨 · 제품 검토서는 필수)", key="ai_term",
                       placeholder="예: 미라베그론 / atorvastatin")

    if mode == "💬 자유 질문":
        question = st.text_area("질문 (무엇이든)", height=100, key="ai_question",
            value="이 제품/성분의 개발(제네릭·개량신약) 관점 시장성·경쟁·특허·약가를 종합 검토해줘.")
        if st.button("AI 분석 실행", key="ai_run", type="primary"):
            ctx = build_ai_context(aq) if aq else ""
            if ctx:
                with st.expander("📎 AI에 전달된 데이터 근거", expanded=False):
                    st.text(ctx)
            prompt = (f"[연결된 데이터 근거]\n{ctx}\n\n" if ctx else "") + f"[질문]\n{question}"
            with st.spinner("Gemini 분석 중…"):
                ok, ans = gemini_ai.analyze(
                    prompt,
                    system="너는 제약 산업 전문 분석가다. 한국어로 정확하고 실용적으로 답하라. "
                           "연결된 데이터 근거가 있으면 우선 활용하고, 없거나 부족하면 너의 전문 지식(효능·기전·규제·시장 등)을 "
                           "자유롭게 활용해 답하라. 단, 데이터에 근거한 사실과 일반 지식·추정을 구분해서 표기하라.")
            st.markdown(ans) if ok else st.error(ans)
    else:
        st.caption("제품/성분을 입력하고 실행하면, 연결된 데이터를 종합해 아래 양식으로 검토서를 만듭니다.")
        if st.button("📋 제품 검토서 생성", key="ai_review", type="primary"):
            if not aq.strip():
                st.warning("제품/성분을 입력하세요.")
            else:
                data = build_review_context(aq)
                with st.expander("📎 검토서에 사용된 데이터 근거", expanded=False):
                    st.text(data)
                with st.spinner("Gemini 검토서 작성 중…"):
                    ok, ans = gemini_ai.analyze(
                        f"[대상] {aq}\n\n[연결된 데이터]\n{data}\n\n"
                        f"위 데이터로 아래 항목의 '제품 검토서'를 작성하라. 반드시 **마크다운 표**(항목 | 내용) 형식으로, "
                        f"아래 11개 항목을 순서대로 모두 포함하라:\n{REVIEW_TEMPLATE}\n\n"
                        "- 각 항목은 연결된 데이터를 우선 사용하고, 데이터에 없는 항목(효능/효과·용법/용량 등)은 "
                        "너의 의약품 전문 지식으로 채우되 '(참고)'라고 표기하라.\n"
                        "- PMS는 재심사 시작 기간 및 진행연도 중심으로.\n"
                        "- 매출액은 최근 5개년 연간(백만원)으로.\n"
                        "- 데이터가 전혀 없는 항목은 '자료 없음'으로.",
                        system="너는 제약 제품 검토 담당자다. 정확하고 간결한 한국어로 표를 작성하라. 없는 수치를 지어내지 마라.")
                if ok:
                    st.markdown("### 📋 제품 검토서")
                    st.markdown(ans)
                    st.download_button("⬇️ 검토서 다운로드(.md)", ans,
                                       file_name=f"제품검토_{aq}.md", mime="text/markdown", key="ai_dl")
                else:
                    st.error(ans)

# ══════════════════════════ 상태 ══════════════════════════
if PAGE == "status":
    st.subheader("데이터 연결 상태")
    reg = "✅ 연결" if HAS_REG else "⏳"
    prc = "✅ 연결" if HAS_PRICE else "⏳"
    nego = "✅ 연결" if (hasattr(ss, "nego_available") and ss.nego_available()) else "⏳"
    rows = [
        ("매출 · IQVIA", "✅ 연결", "saved_data/iqvia.pkl (66,320행)"),
        ("매출 · UBIST", "✅ 연결", "saved_data/ubist.pkl (52,217행)"),
        ("허가현황", reg, "Sheets API → approval.parquet (42,992행)"),
        ("특허현황", reg, "Sheets API → patent.parquet (120,525행)"),
        ("임상시험", reg, "Sheets API → clinical.parquet (8,096행)"),
        ("약가(상한금액 이력)", prc, "Drive API → price.parquet (177,562행)"),
        ("공단 약가협상 완료(공식)", nego, "NHIS → nego.parquet"),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["항목", "상태", "비고"]), hide_index=True, use_container_width=True)
    st.caption("성분 조인 키 = 영문 성분명 첫 단어. 한글 성분 검색은 허가데이터로 영문키 변환.")
