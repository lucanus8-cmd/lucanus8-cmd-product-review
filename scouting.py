"""제품 검토 대시보드 (Streamlit)
탭: 제품 종합분석 / 제품 제안 / 매출 분석 / 특허 분석 / 약가 분석 / 상태
매출: IQVIA·UBIST · 허가/특허/임상: 식약처 · 약가: 심평원 통합본 + NHIS 협상완료(공식)
기존 app.py 는 건드리지 않는다. 실행: streamlit run scouting.py --server.port 8502
"""
import os

# pandas 3 의 글자 열은 Arrow 가 들고 있는데, Arrow 기본 엔진(mimalloc)은
# 다 쓴 메모리를 OS 에 돌려주지 않는다. 표를 읽고 검색할 때마다 '찌꺼기'가
# 쌓여, 탭을 몇 개 돌면 무료 플랜 한도(약 1GB)에 닿아 앱이 강제 종료된다.
# 시스템 엔진은 돌려준다. pandas·pyarrow 를 불러오기 '전에' 정해야 한다.
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

import gc
import re
import time
from pathlib import Path
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import scout_sources as ss
import be_calc
import cost_calc

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

# 데이터 확보 실패(자격증명 만료·시트 공유 해제·네트워크 오류 등)로 앱 전체가
# 파이썬 오류 화면이 되면 안 된다. 원인을 사람이 읽을 수 있게 안내하고,
# 있는 자료만으로 계속 쓸 수 있게 한다(각 탭은 '미연결' 안내를 스스로 표시).
_DATA_ERR = None
try:
    bootstrap.ensure_data()
except Exception as _e:
    _DATA_ERR = _e

def _data_status_banner():
    if _DATA_ERR is None:
        return
    _sd = Path(__file__).parent / "scout_data"
    # 매출은 매출 화면을 열 때 받으므로(시작 때 받지 않음) 여기서 '미연결'로 세지 않는다
    _want = {"허가": _sd / "approval.parquet", "특허": _sd / "patent.parquet",
             "임상": _sd / "clinical.parquet", "약가": _sd / "price.parquet",
             "공단협상": _sd / "nego.parquet"}
    _missing = [k for k, v in _want.items() if not v.exists()]
    st.error("데이터를 새로 받아오지 못했습니다. 아래 자료는 지금 사용할 수 없습니다."
             + (f"\n\n**미연결:** {', '.join(_missing)}" if _missing else "")
             + "\n\n나머지 기능은 그대로 쓸 수 있습니다. 계속 이러면 구글 시트 공유 설정과 "
               "서비스 계정 키(Streamlit Secrets)를 확인해 주세요.")
    with st.expander("자세한 오류 내용 (개발자용)"):
        st.code(f"{type(_DATA_ERR).__name__}: {_DATA_ERR}")

DATA_DIR = Path(__file__).parent / "saved_data"
TODAY = pd.Timestamp.today().normalize()

@st.cache_data(show_spinner="IQVIA 로딩 중…")
def load_iqvia():
    bootstrap.ensure_sales()          # 매출 자료는 쓸 때 확보한다(시작 때 기다리지 않도록)
    df = pd.read_pickle(DATA_DIR / "iqvia.pkl")
    yr = sorted([c for c in df.columns if re.match(r"^\d{4}년$", str(c))])
    return df, yr

@st.cache_data(show_spinner="UBIST 로딩 중…")
def load_ubist():
    bootstrap.ensure_sales()
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

# 제형어(정/캡슐/액 등) — 브랜드 핵심어만 남길 때 제거한다. 긴 것이 앞에 와야 한다.
_FORM_RE = (r"(서방정|속붕정|장용정|설하정|츄어블정|구강붕해정|정제|정|연질캡슐|경질캡슐|캡슐|"
            r"주사액|주사|프리필드시린지|건조시럽|시럽|과립|산제|산|점안액|점비액|점이액|"
            r"현탁액|흡입액|외용액|액|크림|연고|겔|패치|좌제)$")

def _nospace(sr):
    """시리즈의 공백 제거(띄어쓰기 차이 흡수)."""
    return sr.astype(str).str.replace(r"\s+", "", regex=True)

def match_mask(df, cols, q):
    """contains() 와 같은 조건이지만 행을 복사하지 않고 조건(참/거짓)만 돌려준다.
    AI 탭처럼 자료를 여러 개 동시에 여는 곳에서, 필요한 열만 골라 쓰기 위한 것."""
    qn = re.sub(r"\s+", "", str(q))
    m = pd.Series(False, index=df.index)
    for c in cols:
        if c in df.columns:
            m |= _nospace(df[c]).str.contains(qn, case=False, na=False, regex=False)
    return m

def pick(df, mask, cols):
    """조건에 맞는 행에서 '필요한 열만' 꺼낸다. 전체 열 복사를 피한다."""
    use = [c for c in cols if c in df.columns]
    return df.loc[mask, use] if use else df.loc[mask, []]

def contains(df, cols, q):
    # 띄어쓰기 차이 흡수: 검색어·대상 모두 공백 제거 후 리터럴 검색
    # (regex=False: 품목명에 괄호/대괄호 등 특수문자가 있어도 정규식 오류 없이 검색)
    return df[match_mask(df, cols, q)]

def sales_mask(df, cfg, q):
    """sales_search 와 같은 조건만 돌려준다(행 복사 없음)."""
    qn = re.sub(r"\s+", "", str(q))
    m = _nospace(df[cfg["prod"]]).str.contains(qn, case=False, na=False, regex=False) | \
        _nospace(df[cfg["ing"]]).str.contains(qn, case=False, na=False, regex=False)
    keys = ss.resolve_keys(q)
    if keys:
        m = m | df[cfg["ing"]].astype(str).map(ss.ing_key).isin(keys)
    return m

def sales_search(df, cfg, q):
    """제품명/성분명 + (한글↔영문 성분키) 매칭. 띄어쓰기 차이는 무시."""
    return df[sales_mask(df, cfg, q)]

def msel(container, label, series, key, default=None):
    sr = series.dropna().astype(str).str.strip()
    opts = sorted(sr[sr.str.len() > 0].unique().tolist())
    return container.multiselect(label, opts, default=default or [], key=key)

# 표에 한 번에 보낼 최대 행 수. 수만 행을 그대로 그리면 브라우저로 보내는 데만
# 몇 초씩 걸린다. 화면에는 상위 N건만 그리고, 전체는 CSV로 내려받게 한다.
TABLE_CAP = 500

# 배포된 코드가 어느 것인지 눈으로 확인하려고 둔다. 올릴 때마다 올린다.
BUILD = "2026-11-14-b"

def show_df(df, height=360, cap=TABLE_CAP, key=None, file_name="결과.csv", container=None):
    """큰 표를 상위 cap건만 그리고, 넘치면 전체 CSV 내려받기 버튼을 함께 보여준다."""
    c = container or st
    n = len(df)
    c.dataframe(df.head(cap), use_container_width=True, height=height, hide_index=True)
    if n > cap:
        c.caption(f"표에는 상위 {cap:,}건만 표시했습니다 (전체 {n:,}건). 전체는 아래 CSV로 받으세요.")
        c.download_button(f"⬇️ 전체 {n:,}건 CSV", df.to_csv(index=False).encode("utf-8-sig"),
                          file_name=file_name, key=key or f"dl_{file_name}_{n}")

def isin(df, col, sel):
    return df if not sel else df[df[col].astype(str).isin(sel)]

def cagr_matrix(mat, labels, min_base=1e8):
    """calc_cagr()의 벡터 버전. 행 수가 수천 개면 행별 apply는 수 초씩 걸린다."""
    import numpy as np
    V = np.asarray(mat, dtype="float64")
    V = np.nan_to_num(V, nan=0.0)
    if V.size == 0:
        return np.full(len(V), np.nan)
    years = np.array([int(year_of(c)) for c in labels])
    pos = V > 0
    big = V >= min_base
    has_big, npos = big.any(axis=1), pos.sum(axis=1)
    si = np.where(has_big, big.argmax(axis=1), 0)                 # 첫 기준연도
    ei = V.shape[1] - 1 - pos[:, ::-1].argmax(axis=1)             # 마지막 양수연도
    r = np.arange(V.shape[0])
    sv, ev = V[r, si], V[r, ei]
    n = years[ei] - years[si]
    ok = has_big & (npos >= 2) & (n > 0) & (ev > 0) & (sv > 0)
    out = np.full(V.shape[0], np.nan)
    out[ok] = ((ev[ok] / sv[ok]) ** (1.0 / n[ok]) - 1.0) * 100
    return out

def sales_agg(df, yr, key, maker):
    g = df.groupby(key, dropna=True)
    out = g[yr].sum()
    out["시장규모"] = out[yr[-1]]
    out["CAGR"] = cagr_matrix(out[yr].to_numpy(), yr)
    out = out.join(g[maker].nunique().rename("제조사수")).join(g.size().rename("품목수"))
    return out.reset_index()

@st.cache_data(show_spinner=False)
def sales_agg_cached(src, key, maker):
    """집계 결과 캐시. 위젯을 만질 때마다 수천 그룹을 다시 집계하지 않도록."""
    df, yr, _ = get(src)
    return sales_agg(df, yr, key, maker)

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
    pv.columns = [str(c) for c in pv.columns]   # 연도(정수)+문자열 혼합 헤더 경고 방지
    pv.insert(0, "제품명", [name_map.get(c, c) for c in pv.index])
    return pv.reset_index(drop=True)

# ── 이 화면이 쓰는 자료만 먼저 확보 ────────────────────────────────────────
# 앱을 켤 때 모든 자료를 받으면 홈만 보려고 들어와도 1~2분을 기다린다.
# 화면마다 실제로 쓰는 자료만 그때 받는다(이미 있으면 아무 일도 하지 않는다).
_PAGE_NEEDS = {
    "home":     [],
    "status":   [],
    "sales":    [],                                    # 매출은 '불러오기'를 누를 때
    "search":   ["approval", "patent", "clinical", "price"],
    "sugg":     ["approval", "patent"],
    "patent":   ["approval", "patent"],
    "clinical": ["clinical"],
    "price":    ["approval", "price", "nego"],
    "ai":       ["approval", "patent", "clinical", "price"],
}
_NEED_LABEL = {"approval": "허가", "patent": "특허", "clinical": "임상",
               "price": "약가", "nego": "공단 협상"}
_pg = st.query_params.get("page") or "home"
_need = _PAGE_NEEDS.get(_pg, [])

def _ensure_needed():
    for _n in _need:
        try:
            bootstrap.ensure_one(_n)   # 이 화면에 필요한 자료는 기다려서 확보
        except Exception:
            pass

# 없어도 화면은 열리는 자료(있으면 더 보여줄 뿐) — 이것 때문에 기다리게 하지 않는다
_OPTIONAL = {"nego"}
_WAIT_LIMIT_S = 150          # 이만큼 기다렸는데도 안 되면 일단 화면을 연다

def _label(ns):
    return " · ".join(_NEED_LABEL.get(n, n) for n in ns)

# 준비 화면에서 3초마다 이 부분만 다시 확인한다. 다 받으면 화면 전체를 다시 그린다.
def _wait_tick_body():
    _left = [n for n in st.session_state.get("_wait_for", []) if not bootstrap.has_cache(n)]
    _run, _done, _err = bootstrap.background_status()
    if not _left:
        try:
            st.rerun(scope="app")
        except Exception:
            st.rerun()
        return
    if _err and not _run:
        st.error("자료를 받아오지 못했습니다 — " + str(_err)[:200])
        _c1, _c2 = st.columns(2)
        if _c1.button("다시 시도", key="btn_retry_build"):
            bootstrap.build_later(_left, 0)
            st.rerun()
        if _c2.button("그냥 화면 열기", key="btn_skip_wait"):
            st.session_state["_skip_wait"] = True
            try:
                st.rerun(scope="app")
            except Exception:
                st.rerun()
        return
    if not _run:                       # 어쩌다 멈췄으면 다시 시작 (너무 자주 하지 않는다)
        _tries = st.session_state.get("_wait_tries", 0)
        _last = st.session_state.get("_wait_last", 0.0)
        if _tries < 5 and time.time() - _last > 20:
            st.session_state["_wait_tries"] = _tries + 1
            st.session_state["_wait_last"] = time.time()
            bootstrap.build_later(_left, 0)
        elif _tries >= 5:
            st.warning("자료 준비가 계속 되지 않습니다. 아래 버튼으로 화면을 먼저 열 수 있습니다.")
            if st.button("그냥 화면 열기", key="btn_skip_wait2"):
                st.session_state["_skip_wait"] = True
                try:
                    st.rerun(scope="app")
                except Exception:
                    st.rerun()
            return
    st.caption("받아오는 중… " + _label(_left))

if hasattr(st, "fragment"):
    _wait_tick = st.fragment(run_every="3s")(_wait_tick_body)
else:
    _wait_tick = None

# 서버에 아직 없는 자료가 있으면, 화면을 붙잡은 채로 받지 않는다.
# (무료 플랜은 CPU가 1개라, 받는 동안 브라우저가 화면 조각(js)을 못 받아
#  위젯이 'Failed to fetch dynamically imported module' 로 깨진다)
_missing = [n for n in _need if not bootstrap.has_cache(n)]
_fresh = [n for n in _missing if n not in _OPTIONAL]
if [n for n in _missing if n in _OPTIONAL]:
    bootstrap.build_later([n for n in _missing if n in _OPTIONAL])   # 뒤에서만 받는다

# 너무 오래 기다리게 두지 않는다 — 한도를 넘으면 있는 자료로 화면을 연다
if _fresh:
    _t0 = st.session_state.get("_wait_t0")
    if _t0 is None:
        st.session_state["_wait_t0"] = _t0 = time.time()
    if time.time() - _t0 > _WAIT_LIMIT_S or st.session_state.get("_skip_wait"):
        _fresh = []
else:
    for _k in ("_wait_t0", "_wait_tries", "_wait_last"):
        st.session_state.pop(_k, None)

if _fresh and _wait_tick is not None:
    st.session_state["_wait_for"] = _fresh
    st.info(f"⏳ **{_label(_fresh)} 자료를 처음 준비하고 있습니다.** 1~2분 걸릴 수 있어요.\n\n"
            "준비가 끝나면 이 화면이 **자동으로** 바뀝니다 — 새로고침하지 마세요.")
    bootstrap.build_later(_fresh)      # 화면을 다 보낸 뒤 시작한다
    _wait_tick()
    st.stop()
elif _fresh:                            # 아주 옛 버전 대비(자동 새로고침 불가)
    with st.spinner(f"{_label(_fresh)} 자료를 처음 준비하는 중입니다 — 1~2분 걸릴 수 있어요."):
        _ensure_needed()
else:
    _ensure_needed()

try:
    bootstrap.ensure_rest_in_background()   # 나머지(매출·공단협상 등)는 뒤에서 미리 받아 둠
except Exception:
    pass

# 뒤에서 새 자료를 받는 중이면, 끝났을 때 화면을 스스로 새로 그린다.
# (예전에는 사용자가 F5 를 눌러야 새 자료가 보였다)
def _bg_watch_body():
    _run = bootstrap.background_status()[0]
    if _run:
        st.session_state["_bg_seen"] = True
        st.caption("⏳ 새 자료를 뒤에서 받아오는 중… 끝나면 자동으로 다시 보여드립니다.")
    elif st.session_state.pop("_bg_seen", False):
        try:
            st.rerun(scope="app")      # 다 받았으면 화면 전체를 새 자료로 다시 그린다
        except Exception:
            st.rerun()

if hasattr(st, "fragment"):            # 3초마다 이 작은 부분만 다시 확인(화면은 안 멈춤)
    _bg_watch = st.fragment(run_every="3s")(_bg_watch_body)
else:
    _bg_watch = _bg_watch_body

try:
    if bootstrap.background_status()[0]:
        _bg_watch()
except Exception:
    pass

HAS_REG = ss.available()
HAS_PRICE = ss.price_available()
HAS_CLIN = ss.clinical_available()
HAS_PAT = ss.patent_available()

def has_sales(src):
    """매출 자료 파일이 준비돼 있는지만 확인한다(받아오지 않음)."""
    f = DATA_DIR / {"IQVIA": "iqvia.pkl", "UBIST": "ubist.pkl"}.get(src, "")
    return bool(f) and f.exists()

def fetch_sales_now():
    """'불러오기'를 눌렀을 때 매출 자료(IQVIA·UBIST)를 지금 받아온다.
    성공하면 True. 실패하면 이유를 화면에 보여주고 False."""
    with st.spinner("매출 자료(IQVIA·UBIST)를 받아오는 중… 처음엔 1~2분 걸릴 수 있습니다"):
        ok, err = bootstrap.fetch_sales()
    if ok:
        for _ld in (load_iqvia, load_ubist):   # 새로 받은 파일을 읽도록 캐시만 비운다
            try: _ld.clear()
            except Exception: pass
        return True
    st.error("매출 자료를 불러오지 못했습니다. 잠시 뒤 다시 눌러 보시고, 계속 안 되면 "
             "원본 엑셀(IQVIA·UBIST)의 구글 드라이브 공유 설정을 확인해 주세요.")
    if err:
        with st.expander("자세한 오류 내용 (개발자용)"):
            st.code(err)
    return False

def sales_load_button(key, label="📊 매출 자료 지금 불러오기"):
    """매출 자료가 없을 때 쓰는 공용 버튼. 누르면 받아오고 화면을 새로 그린다."""
    if st.button(label, key=key, type="primary"):
        if fetch_sales_now():
            st.rerun()

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

def _edi_codes(v):
    """허가시트의 보험코드(EDI)에서 9자리 코드들을 뽑는다.

    한 품목에 포장단위별로 코드가 여러 개면 식약처는 쉼표로 이어 준다.
    그런데 시트가 그 값을 '숫자'로 인식하면 1,000 단위 쉼표가 찍히고
    (645,100,922,645,100,000) 유효자리 15자리를 넘는 뒷부분이 0 으로
    뭉개져 되살릴 수 없다. 그 경우 앞 9자리(첫 코드)만 쓴다 —
    9자리씩 쪼개면 645100000 같은 없는 코드가 만들어져 매출이 잘못 붙는다.
    텍스트로 제대로 들어온 값은 쉼표로 끊어 전부 쓴다.
    """
    raw = str(v).strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):   # 숫자로 뭉개진 값
        d = re.sub(r"\D", "", raw)
        if len(d) % 9 == 8:          # 앞자리 0 이 깎인 경우
            d = "0" + d
        return {d[:9]} if len(d) >= 9 else set()
    out = set()
    for p in parts:
        d = re.sub(r"\D", "", p)
        if len(d) == 9:
            out.add(d)
        elif len(d) == 8:
            out.add("0" + d)
    return out

# 금액 축은 1.2k 같은 줄임 표기 대신 1,200 처럼 그대로 보여준다
_WON_AXIS = dict(title="상한금액(원)", tickformat=",d", separatethousands=True)

# ── 약가 산정 ───────────────────────────────────────────────────────────────
# 산정 비율(제도 바뀌면 여기만 고치면 된다). 기준가는 '같은 제제 중 최고가'.
CALC_RATE = {"salt": .90, "salt_adj": 1.00, "dose": 1.00, "dose_adj": 1.10,
             "addon": .70, "improved": .70, "dataonly": .60, "after": .45,
             "gen_base": .45, "gen_innov": .60, "gen_semi": .50,
             "combo_base": .45, "combo_imp": .60, "combo_innov": .68}
# ── 2026.8 개정고시 ─────────────────────────────────────────────────────
# 기준요건(DMF·자체생동) 충족 수에 따른 산정률. 0개는 산술로 28.8% 지만 고시 표기대로 29%.
REQ_RATE = {2: .45, 1: .36, 0: .29}
STEP_RATE = .29          # 계단식은 기준요건과 무관하게 29% 기준
AFTER_CUT = .85          # 다품목 1년 후 · 계단식 모두 85%
AFTER_ADDON = .45        # 가산이 끝나면 요건률이 아니라 45% 로 유지된다
MP_TOTAL = 14            # 기등재 + 동시신청 합계가 이 수 이상이면 다품목 등재관리
STEP_PRIOR = 13          # 기등재가 이 수 이상이면 계단식 약가인하

# ⑤ 함량배수: 자사제품이 없으면 최근접 함량 최고가의 45%(마약·생물 70%)가 A.
DOSE_A = .45
DOSE_A_SPECIAL = .70

# ① 단일제 자료제출의 세 갈래. 70% 가산은 가운데(동시 조정)에만 붙는다.
OSTATE = ["조정 전 — 이번 등재로도 오리지널 조정이 일어나지 않음",
          "동시 조정 — 이번 등재와 함께 오리지널이 조정됨 (70% 가산)",
          "이미 조정됨 — 오리지널이 전에 조정을 마친 상태"]

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


@st.cache_data(show_spinner="같은 성분의 다른 함량 품목을 찾는 중…")
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


@st.cache_data(show_spinner="오리지널 약가 이력 보는 중…")
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


def _calc_phases(ty, base, opt):
    """유형별 단계 목록 [(이름, 기간, 비율)] 과 안내문."""
    R, ph = CALC_RATE, []
    if ty.startswith("①"):
        # 산정 비율은 '넣은 기준가가 이미 조정된 가격인지'로 갈린다.
        #   염·이성체/신규제형 : 조정 전 가격 90%  / 조정된 가격 100%
        #   용법용량 개선      : 조정 전 가격 100% / 조정된 가격 110%
        salt = opt["sub"].startswith("염")
        imp = opt["imp"].startswith("개량")
        # 오리지널의 조정 상태로 세 갈래가 갈린다. 70% 가산은 '등재와 동시에
        # 오리지널이 조정되는' 경우에만 붙는다. 이미 조정을 마친 오리지널을
        # 기준으로 삼으면 가산 없이 100%(용법용량 개선 110%)로 산정한다.
        st3 = int(opt.get("ostate", 0))
        if st3 == 1:
            ph.append(("최초등재 가산", "1년", R["addon"]))
            ph.append(("개량신약 가산 유지" if imp else "자료제출 가산", "3년",
                       R["improved"] if imp else R["dataonly"]))
            ph.append((f"가산 종료 후 ({R['after']*100:.0f}%)", "이후", R["after"]))
            return ph, ("등재와 동시에 오리지널이 조정되는 경우입니다. "
                        "기준가는 **조정 전** 오리지널 가격이어야 하며, "
                        "70%에서 시작해 가산이 끝나면 45%로 유지됩니다.")
        if st3 == 2:
            rate = R["salt_adj"] if salt else R["dose_adj"]
            ph.append(("이미 조정된 가격 기준", "이후", rate))
            return ph, (f"오리지널이 이미 조정을 마친 경우라 가산 없이 {rate*100:.0f}% 로 산정합니다. "
                        "기준가는 **조정된 뒤의** 가격이어야 합니다.")
        init = R["salt"] if salt else R["dose"]
        ph.append(("최초 등재 (조정 전 유지)", "이후", init))
        return ph, (f"오리지널이 조정 전이고 이번 등재로도 조정이 일어나지 않는 경우라 "
                    f"{init*100:.0f}% 를 계속 적용합니다. 가산은 붙지 않습니다.")
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

        if way == "다품목":
            if add:
                # 가산 중에는 인하가 걸리지 않는다. 가산이 끝나면 45% 하락과 85% 인하가 한 번에.
                ph.append(("가산 종료 — 45% × 85%", "이후", AFTER_ADDON * AFTER_CUT))
                note = ("다품목 등재관리 대상입니다. 가산을 받는 동안은 인하가 걸리지 않고, "
                        "가산이 끝나는 순간 45%로 떨어짐과 동시에 85%가 같이 걸립니다.")
            else:
                ph.append(("등재 ~ 1년", "1년", req))
                ph.append(("1년 후 — 다품목 85%", "이후", req * AFTER_CUT))
                note = ("다품목 등재관리 대상입니다. 등재 1년 뒤 최초 산정가의 85%로 조정됩니다 "
                        f"({req*100:.0f}% → {req*AFTER_CUT*100:.2f}%).")
            return ph, (f"기등재 {opt['prior']}개 + 동시신청 {opt['apply']}개 = "
                        f"{opt['prior']+opt['apply']}개. " + note)

        if add:
            ph.append((f"가산 종료 후 ({AFTER_ADDON*100:.0f}%)", "이후", AFTER_ADDON))
            return ph, ("가산이 끝나면 기준요건과 무관하게 45%로 유지됩니다.")
        ph.append(("기본 산정", "이후", req))
        return ph, (f"기준요건 {opt['reqs']}개 충족 → {req*100:.0f}%. "
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
    for name, term, rate in phases:
        dur = 2.0 if term == "이후" else float(str(term).replace("년", "") or 1)
        amt = base * rate
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
    for name, term, rate in phases:
        dur = 2.0 if term == "이후" else float(str(term).replace("년", "") or 1)
        fig.add_annotation(x=t + dur / 2, y=base * rate, text=f"{won(base*rate):,}원",
                           showarrow=False, yshift=14, font=dict(size=12, color="#c9376b"))
        t += dur
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=36, b=10), showlegend=False,
                      title="등재 후 예상 상한금액 변화", yaxis=_WON_AXIS,
                      xaxis=dict(title="등재 후 경과", tickmode="array",
                                 tickvals=ticks, ticktext=tlabels))
    return fig

@st.cache_data(show_spinner=False, max_entries=1024)
def _be_n(design, cv, target, gmr, alpha, lo, hi):
    """목표 검정력을 넘는 가장 작은 인원. 순수 계산이라 캐시가 안전하다."""
    return be_calc.sample_size(design, cv, target, gmr, alpha, lo, hi)


@st.cache_data(show_spinner=False, max_entries=1024)
def _be_pw(design, n, cv, gmr, alpha, lo, hi):
    return be_calc.power(design, n, cv, gmr, alpha, lo, hi)


def _be_page():
    """생동 피험자 수 계산기.

    화면 구성은 biostatistics.ca 의 PowerTOST 계산기와 같게 맞췄다
    (왼쪽 입력 · 오른쪽 결과, 칸 일곱 개, 버튼 하나, 결과는 '전체' 인원).
    자료를 전혀 읽지 않는 순수 계산이라 화면이 가볍다.

    위젯 key 는 모두 be2_ 로 시작한다 — 예전 화면이 남긴 session_state 값과
    종류가 달라(라디오 '80%' → 숫자칸) 섞이면 오류가 나기 때문이다.
    """
    st.markdown("**🧮 생동 피험자 수** · 변동계수(CV%)를 넣으면 생물학적동등성시험에 "
                "몇 명이 필요한지 계산합니다 (TOST · 평균생동)")

    left, right = st.columns([5, 7], gap="large")

    with left:
        _labels, _keys = [], []
        for k in be_calc.DESIGN_ORDER:
            d = be_calc.DESIGNS[k]
            _labels.append(("⚠ 드묾 · " if d["rare"] else "")
                           + f"{d['label']}  ({d['seq']})")
            _keys.append(k)
        _pick = st.selectbox("설계", _labels, index=0, key="be2_design")
        dkey = _keys[_labels.index(_pick)]
        D = be_calc.DESIGNS[dkey]
        st.caption(D["note"])

        _kind = D["cv"]      # 개체내 / 전체
        _split = st.checkbox("AUC·Cmax 를 따로 넣기", value=False, key="be2_split")
        if _split:
            cv_auc = float(st.number_input(
                f"AUC CV% ({_kind})", min_value=0.0, max_value=200.0,
                value=25.0, step=1.0, format="%.4g", key="be2_cv_auc"))
            cv_cmax = float(st.number_input(
                f"Cmax CV% ({_kind})", min_value=0.0, max_value=200.0,
                value=35.0, step=1.0, format="%.4g", key="be2_cv_cmax"))
            _cvs = [("AUC", cv_auc), ("Cmax", cv_cmax)]
        else:
            cv_one = float(st.number_input(
                f"변동계수 CV% ({_kind})", min_value=0.0, max_value=200.0,
                value=30.0, step=1.0, format="%.4g", key="be2_cv"))
            _cvs = [("CV", cv_one)]

        gmr = float(st.number_input(
            "기대 기하평균비 GMR (시험약/대조약)", min_value=0.70, max_value=1.30,
            value=0.95, step=0.01, format="%.3f", key="be2_gmr"))
        lo = float(st.number_input(
            "생동한계 아래", min_value=0.50, max_value=0.99, value=0.80,
            step=0.01, format="%.2f", key="be2_lo"))
        hi = float(st.number_input(
            "생동한계 위", min_value=1.01, max_value=2.00, value=1.25,
            step=0.01, format="%.2f", key="be2_hi"))
        st.caption(f"{lo * 100:.2f}% ~ {hi * 100:.2f}%")
        target = float(st.number_input(
            "목표 검정력 (%)", min_value=50.0, max_value=99.0, value=80.0,
            step=1.0, format="%.4g", key="be2_pw"))
        alpha = float(st.number_input(
            "유의수준 α (단측, %)", min_value=0.5, max_value=20.0, value=5.0,
            step=0.5, format="%.4g", key="be2_alpha"))
        drop = float(st.number_input(
            "예상 탈락률 (%)", min_value=0.0, max_value=60.0, value=0.0,
            step=1.0, format="%.4g", key="be2_drop"))
        st.caption("탈락률을 넣으면 **등록해야 할 인원**도 같이 내줍니다. "
                   "0 이면 완료 기준만 봅니다.")
        _go = st.button("피험자 수 계산", type="primary",
                        use_container_width=True, key="be2_go")

    if _go:
        st.session_state["be2_done"] = True
    if not st.session_state.get("be2_done"):
        with right:
            st.info("왼쪽 값을 확인하고 **피험자 수 계산** 을 누르세요.")
        return

    parts = []
    for _nm, _cv in _cvs:
        if _cv and _cv > 0:
            n = _be_n(dkey, _cv / 100, target / 100, gmr, alpha / 100, lo, hi)
            parts.append({"name": _nm, "cv": _cv, "n": n,
                          "power": (_be_pw(dkey, n, _cv / 100, gmr,
                                           alpha / 100, lo, hi) if n else None)})
    done = [p for p in parts if p["n"]]

    with right:
        st.caption("값을 바꾸면 바로 다시 계산됩니다.")
        if not done:
            if not parts:
                st.info("CV% 를 넣어주세요.")
            else:
                st.warning("이 조건에서는 2,000명 안에서 목표 검정력에 닿지 못했습니다. "
                           "CV% 나 목표 검정력, 생동한계를 확인해 주세요.")
            return
        top = max(done, key=lambda q: q["n"])
        st.metric("필요 피험자 수 (전체 · 완료 기준)", f"{top['n']:,} 명")
        st.metric("실제 검정력", f"{top['power'] * 100:.2f} %")
        if drop > 0:
            _en = be_calc.enroll(top["n"], drop / 100, D["step"])
            st.metric("등록해야 할 인원", f"{_en:,} 명" if _en else "—",
                      delta=f"탈락 {drop:g}% 감안", delta_color="off")
        st.caption(f"순서당·군당이 아니라 **전체** 인원입니다. "
                   f"순서가 {D['step']}가지인 설계라 인원을 "
                   f"**{D['step']}의 배수**로 맞춥니다.")
        if len(done) > 1:
            _a, _b = done[0], done[1]
            st.info(f"**{top['name']}** 쪽이 더 많아 이쪽으로 정했습니다 — "
                    f"{_a['name']}(CV {_a['cv']:g}%) {_a['n']}명 · "
                    f"{_b['name']}(CV {_b['cv']:g}%) {_b['n']}명.")

    big = max(p["cv"] for p in done)

    if _kind == "전체":
        st.warning("병렬설계의 CV 는 **개체내가 아니라 전체(개체간 포함) 변동**입니다. "
                   "교차설계용 개체내 CV 를 그대로 넣으면 인원이 실제보다 적게 나옵니다.")
    if dkey == "paired":
        st.warning("**대응표본은 생동시험으로 인정되지 않습니다.** 순서를 무작위로 "
                   "배정하지도, 기간을 나누지도 않아 약의 효과와 기간 효과를 갈라낼 수 "
                   "없습니다. 같은 사람에게서 두 측정법을 비교할 때 쓰는 설계입니다.")
    if dkey == "2x4x2":
        st.warning("**Balaam 은 인원이 크게 늡니다.** 네 순서 중 RR·TT 군은 시험약과 "
                   "대조약을 둘 다 받지 않아 T/R 비교에 직접 쓰이지 않습니다 — 같은 "
                   "CV에서 2×2 교차의 약 4배가 듭니다.")
    if big > 30 and _kind != "전체":
        st.warning("CV 가 30% 를 넘습니다 — **고변동 의약품**입니다. 반복설계와 "
                   "기준조절(RSABE·ABEL)을 쓰면 인원을 크게 줄일 수 있습니다. "
                   "**이 계산기는 평균생동(ABE)만 다룹니다** — 기준조절 적용 가능 "
                   "여부는 의약품동등성시험기준에서 확인하세요.")

    if st.checkbox("CV 가 달라지면 — 표로 보기", key="be2_tbl_cv"):
        _rows = []
        for c in sorted({round(max(2.0, big + d)) for d in (-10, -5, 0, 5, 10)}):
            n = _be_n(dkey, c / 100, target / 100, gmr, alpha / 100, lo, hi)
            p = _be_pw(dkey, n, c / 100, gmr, alpha / 100, lo, hi) if n else None
            _rows.append({"CV%": f"{c:g}%",
                          "완료 기준": f"{n:,} 명" if n else "—",
                          "실제 검정력": f"{p * 100:.1f}%" if p else "—",
                          "": "← 지금 값" if abs(c - big) < 0.5 else ""})
        st.dataframe(pd.DataFrame(_rows), hide_index=True,
                     use_container_width=True, height=38 * (len(_rows) + 1) + 4)
        st.caption("지금 설계·GMR·검정력 그대로 두고 CV 만 바꿨을 때입니다. "
                   "가정 하나가 비용을 두 배로 만들 수 있습니다.")

    if st.checkbox("설계를 바꾸면 — 표로 보기", key="be2_tbl_design"):
        _d = []
        for k in be_calc.DESIGN_ORDER:
            d = be_calc.DESIGNS[k]
            n = _be_n(k, big / 100, target / 100, gmr, alpha / 100, lo, hi)
            _d.append({"설계": d["label"], "순서": d["seq"],
                       "CV 의미": d["cv"],
                       "완료 기준": f"{n:,} 명" if n else "—",
                       "비고": ("생동에서 거의 쓰지 않음" if d["rare"] else ""),
                       "": "← 지금 설계" if k == dkey else ""})
        st.dataframe(pd.DataFrame(_d), hide_index=True,
                     use_container_width=True, height=38 * (len(_d) + 1) + 4)
        st.caption(f"CV {big:g}% 로 모두 계산했습니다. 병렬의 CV 는 전체 변동이라 "
                   "교차설계 칸과 나란히 비교하면 안 됩니다 — 같은 약이면 전체 CV 가 "
                   "개체내보다 커서 실제로는 더 많이 필요합니다. 반복설계는 인원이 "
                   "적은 대신 기간·채혈이 늘어 1인당 비용이 올라갑니다.")

    with st.expander("계산 방법과 검산"):
        st.markdown(
            "**계산**\n\n"
            "- σ = √(ln(1 + CV²)) · SE² = bk × σ² ÷ N\n"
            f"- {D['label']}: bk = **{D['bk']:g}** · 자유도 = **{D['dfs']}** "
            f"· 인원은 **{D['step']}의 배수**\n"
            f"- TOST · 유의수준 {alpha:g}% 단측 · 생동한계 "
            f"{lo * 100:.2f}~{hi * 100:.2f}% · GMR {gmr:.3f}\n"
            "- 목표 검정력을 넘는 가장 작은 N 을 찾고, 등록 인원은 "
            "⌈N ÷ (1−탈락률)⌉ 을 배수로 올립니다\n\n"
            "**검산**\n\n"
            "- PowerTOST 문서 예제: 2×2 · CV 30% · GMR 0.95 · 검정력 80% → "
            "**40명 · 0.815845** (소수 여섯째 자리까지 일치)\n"
            "- 설계 12개의 bk·자유도·배수는 PowerTOST 원본 소스에서 확인한 값입니다\n"
            "- biostatistics.ca 의 PowerTOST 계산기에 **167가지 조건**을 직접 넣어 "
            "받은 값과 대조 — 인원·달성검정력 모두 전부 일치했습니다 "
            "(CV 5~60% · GMR 0.90~1.00 · 검정력 80~95% · α 5·10% · "
            "생동한계 0.8~1.25 와 0.9~1.11)\n"
            "- 저장소의 `tests/check_be_calc.py` 로 언제든 다시 돌려볼 수 있습니다\n\n"
            "**다루지 않는 것** — 고변동 의약품의 기준조절(RSABE·ABEL), "
            "비선형 약동학, 중간분석. 평균생동(ABE) 하나만 계산합니다.")


def _cost_prices():
    """1상 비용 단가. 코드의 기본값을 쓰고, Secrets 의 [phase1_cost] 에 값이
    있으면 그 값으로 덮어쓴다. Secrets 가 없어도 죽지 않는다.
    """
    try:
        raw = st.secrets.get("phase1_cost", None)
    except Exception:
        raw = None
    out = dict(cost_calc.DEFAULT_PRICES)
    if raw:
        for k in cost_calc.KEYS:
            try:
                out[k] = float(raw[k])
            except Exception:
                pass
    return out


def _cost_page():
    """1상 비용 어림 계산기. 자료를 읽지 않는 순수 계산이라 가볍다."""
    st.markdown("**💰 1상 비용 어림** · 숫자 다섯 개로 1상 시험의 대략적인 총 비용을 "
                "봅니다 (견적을 대신하지 않습니다)")

    base = cost_calc.clean(_cost_prices())
    have = cost_calc.ready(base)
    left, right = st.columns([5, 7], gap="large")

    with left:
        n = int(st.number_input("대상자 수 (명)", min_value=1, max_value=2000,
                                value=60, step=1, key="cost_n"))
        k = int(st.number_input("기 수", min_value=1, max_value=12,
                                value=2, step=1, key="cost_k"))
        pts = int(st.number_input("기당 채혈 포인트 (회)", min_value=1, max_value=100,
                                  value=20, step=1, key="cost_pts"))
        an = int(st.number_input("분석 성분 수 (개)", min_value=1, max_value=10,
                                 value=1, step=1, key="cost_an"))
        hrs = float(st.number_input("마지막 채혈 시점 (투약 후 시간)", min_value=1,
                                    max_value=2000, value=240, step=12,
                                    key="cost_hrs"))
        st.caption(f"총 검체 수 **{n * k * pts:,}개** = {n} × {k} × {pts}")

        # 단가가 없으면 바로 넣을 수 있게 칸을 펼쳐 둔다
        edit = st.checkbox("단가 고치기", value=not have, key="cost_edit")
        prices = dict(base)
        if edit:
            st.caption("단가는 **만 원** 단위로 넣습니다.")
            for key, label, hint, step in cost_calc.PRICE_KEYS:
                prices[key] = 1e4 * float(st.number_input(
                    f"{label} (만 원)", min_value=0.0, max_value=10_000_000.0,
                    value=float(base.get(key, 0)) / 1e4, step=step / 1e4,
                    format="%.6g", help=hint, key=f"cost_pm_{key}"))

    with right:
        if not cost_calc.ready(prices):
            st.info("단가가 모두 0입니다. 왼쪽 **단가 고치기**에 값을 넣어 주세요.")
            return
        r = cost_calc.estimate(n, k, pts, an, hrs, prices)
        tot = r["total"]
        st.metric("총 비용 (VAT 별도)", f"약 {tot / 1e8:.2f}억 원")
        st.metric("VAT 포함", f"약 {r['vat_total'] / 1e8:.2f}억 원")
        st.caption("오차 ±10% 안팎 · 할인 전 기준")
        _rows = []
        for _nm, _key in (("임상 CRO", "cro"), ("분석", "lab"),
                          ("병원 (실시기관 + 사례비)", "site")):
            _rows.append({"블록": _nm, "금액": f"{r[_key] / 1e8:.2f}억",
                          "비중": f"{(r[_key] / tot * 100) if tot else 0:.0f}%"})
        st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True,
                     height=38 * (len(_rows) + 1) + 4)
        st.caption(f"병원 단가는 1명·1기당 **{r['site_unit'] / 1e4:,.0f}만 원**으로 "
                   f"잡았습니다 (마지막 채혈 {hrs:g}시간 기준).")
        if edit:
            st.caption("고친 단가는 이 화면에서만 쓰이고 저장되지 않습니다.")

    st.caption("견적 네 건을 반올림한 어림값입니다. 업체와 성분에 따라 달라집니다. "
               "대조약·임상약·표준품 구입비, 식약처 수수료, 사내 인건비는 들어 있지 않고, "
               "실제 계약은 할인으로 3%에서 15%쯤 낮아질 수 있습니다. 건강한 성인 대상 1상 기준입니다.")

    with st.expander("계산 방식과 한계"):
        _p = cost_calc.clean(prices)

        def _m(key):
            return f"{_p[key] / 1e4:,.6g}만"

        st.markdown(
            "**계산**\n\n"
            f"- 임상 CRO = {_m('cro_base')} + {_m('cro_per_period')} × 기 "
            f"+ {_m('cro_per_subject')} × 대상자\n"
            f"- 분석 = ({_m('lab_base')} + {_m('lab_per_sample')} × 검체 수) "
            "× 성분 수\n"
            "- 병원 = 병원 단가 × 대상자 × 기 (실시기관 비용과 사례비를 합친 값)\n"
            f"- 병원 단가(1명·1기당): 24시간 이하 {_m('site_24h')} · "
            f"72시간 {_m('site_72h')} · 240시간 이상 {_m('site_240h')}. "
            "그 사이는 직선으로 이어 계산합니다\n"
            "- 검체 수 = 대상자 × 기 × 기당 채혈 포인트\n\n"
            "**맞춰 본 결과** — 견적 네 건(대상자 40명과 60명, 2기와 3기, 채혈 17회에서 "
            "22회)에 넣었을 때 "
            "총액 차이는 ±3% 안이었습니다. 단가를 그 네 건에서 뽑았으므로, "
            "새 견적에서는 더 벗어날 수 있습니다.\n\n"
            "**가정** — 성분이 여러 개면 분석을 성분마다 따로 한다고 보고 배수로 "
            "곱합니다(한 번에 분석할 수 있으면 실제는 더 적습니다). 병원 단가의 "
            "72시간·24시간 기준점은 추정이고, 240시간을 넘는 경우는 자료가 없어 "
            "240시간 값으로 둡니다.")


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
            opt["sub"] = st.radio("산정 유형",
                                  ["염·이성체 / 신규제형 (90% · 조정가 기준 100%)",
                                   "용법용량 개선 (100% · 조정가 기준 110%)"], key="calc_sub")
            opt["imp"] = st.radio("개량신약 인정", ["개량신약 (70% · 3년+α)", "자료제출 (60% · 최대 3년)"],
                                  key="calc_imp")
            opt["ostate"] = OSTATE.index(st.radio(
                "**오리지널 조정 상태**", OSTATE, index=adj_guess,
                key=f"calc_ostate_{row['제제코드']}"))
            st.caption("70% 가산은 **등재와 동시에 오리지널이 조정되는** 경우에만 붙습니다. "
                       "이미 조정을 마친 오리지널을 기준으로 삼으면 가산 없이 "
                       "100%(용법용량 개선 110%)입니다.")
        elif ty.startswith("②"):
            q1, q2 = st.columns(2)
            _dmf = q1.checkbox("DMF 등록 원료", value=True, key="calc_dmf")
            _bio = q2.checkbox("자체 생동시험", value=True, key="calc_bio")
            opt["reqs"] = int(_dmf) + int(_bio)
            st.caption(f"기준요건 **{opt['reqs']}개 충족 → 산정률 "
                       f"{REQ_RATE[opt['reqs']]*100:.0f}%** "
                       "(2개 45% · 1개 36% · 0개 29%)")

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
                         f"{REQ_RATE[opt['reqs']]*100:.0f}% 로 등재됩니다.",
                 "다품목": f"**다품목 등재관리** — 기등재 {opt['prior']} + 신청 {opt['apply']} = "
                          f"{opt['prior']+opt['apply']}개 (14개 이상). 1년 뒤 85%로 조정됩니다.",
                 "계단식": f"**계단식 약가인하** — 기등재가 {opt['prior']}개 (13개 이상)라 "
                          "min(기등재 최저가, 29% 산정액) × 85% 로 산정됩니다."}[_way])

            if _way == "계단식":
                c1, c2 = st.columns(2)
                opt["low"] = c1.number_input(
                    "기등재 동일제제 최저가", value=float(row["최저가"]), step=1.0,
                    format="%.0f", key=f"calc_low_{row['제제코드']}")
                _kind = cheap_kind(row.get("투여"), row.get("단위"))
                _opts = list(CHEAP) + ["직접 입력"]
                _idx = _opts.index(_kind) if _kind in _opts else len(_opts) - 1
                _sel = c2.selectbox("저가의약품 기준", _opts, index=_idx, key="calc_cheapkind")
                if _sel == "직접 입력":
                    opt["cheap"] = c2.number_input("기준금액(원)", value=0.0, step=1.0,
                                                   format="%.0f", key="calc_cheapamt")
                else:
                    opt["cheap"] = float(CHEAP[_sel])
                    c2.caption(f"{CHEAP[_sel]:,}원 — 투여 {row.get('투여') or '?'} · "
                               f"단위 {row.get('단위') or '?'} 로 자동 판정")
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
        st.metric("등재 시 예상 상한금액", f"{won(base * phases[0][2]):,} 원",
                  delta=f"기준가 대비 {phases[0][2]*100:.2f}%", delta_color="off")
        if base > 0:
            st.plotly_chart(_calc_chart(base, phases), use_container_width=True)
        st.dataframe(pd.DataFrame([{"단계": n, "기간": y, "비율": f"{r*100:.2f}%",
                                    "상한금액": f"{won(base*r):,} 원"} for n, y, r in phases]),
                     use_container_width=True, hide_index=True)
        st.caption("산정 근거")
        st.code("\n".join(f"{n}: {base:,.0f} × {r*100:.2f}% = {won(base*r):,}원"
                          for n, y, r in phases), language=None)
    st.caption(note + "  산정 비율은 제도 변경 시 코드 상단 CALC_RATE 에서 고칩니다.")

@st.cache_data(show_spinner="약가 품목 준비 중…")
def _price_name_opts():
    """약가표의 제품명 목록. 제품명에 함량·포장이 들어 있어(…_(56.64mg/8mL)) 함량을
    하나씩 골라볼 수 있다. 허가 품목명으로 고르면 같은 이름의 여러 함량이 섞여 나온다."""
    if not HAS_PRICE:
        return []
    s = ss.load_price()["제품명"].dropna().astype(str).str.strip()
    return sorted({x for x in s if x})

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
        # 약가표 제품명과 정확히 일치하는 한 품목만(제품명에 함량·포장이 들어 있다).
        # 예전처럼 글자 포함 검색을 하면 같은 이름의 다른 함량이 섞여 나온다.
        sub = pr[pr["제품명"].astype(str) == str(term)]
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
        _pc = pr["_core"] if "_core" in pr.columns else ss.ing_core_series(pr["주성분명"])
        sub = pr[_pc == core] if core else pr.iloc[0:0]
    return sub["제품코드"].dropna().unique().tolist(), f"{term} 오리지널(신약) 약가 변동"

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

    def _feed(kor_ing, kor_name, eng_key, single):
        """한글 성분코어/브랜드코어 → 영문키 등록 (먼저 넣은 쪽 우선). 전부 벡터 연산."""
        ok = single & (eng_key.astype(str).str.len() > 0)
        if not ok.any():
            return
        core = ss.ing_core_series(kor_ing[ok])
        brand = (kor_name[ok].astype(str).str.replace(r"\s+", "", regex=True)
                 .str.split(r"[0-9(\[]", regex=True).str[0].fillna(""))
        k = eng_key[ok].astype(str)
        cc = pd.DataFrame({"c": core, "k": k})
        cc = cc[cc["c"].str.len() >= 2].drop_duplicates("c")
        for a, b in zip(cc["c"], cc["k"]):
            c2k.setdefault(a, b)
        bb = pd.DataFrame({"b": brand, "k": k})
        bb = bb[bb["b"].str.len() >= 2].drop_duplicates("b")
        for a, b in zip(bb["b"], bb["k"]):
            b2k.setdefault(a, b)

    # ① 허가자료 우선 (품목수가 많아 표기가 대표적)
    try:
        ap = ss.load_approval()
        _ing = ap.get("주성분", pd.Series(dtype=str))
        _eng = ap.get("주성분(영문)", pd.Series(dtype=str))
        _feed(_ing, ap.get("품목명", pd.Series(dtype=str)), ss.ing_key_series(_eng),
              ss.is_single_series(_ing, _eng))   # 조합제는 한글/영문 성분 순서가 엇갈려 제외
    except Exception:
        pass

    # ② 특허자료의 한글 성분명으로 보완 (허가에 영문명이 비어 있는 성분 구제)
    try:
        pt = ss.load_patent()
        if "INGR_NAME" in pt.columns:
            _feed(pt["INGR_NAME"], pt.get("품목명", pd.Series(dtype=str)), pt["_key"],
                  ss.is_single_series(pt["INGR_NAME"],
                                      pt.get("INGR_ENG_NAME", pd.Series("", index=pt.index))))
    except Exception:
        pass

    return c2k, b2k

# 좌측 상단 제목
st.markdown(
    "<div style='color:#1f2a44;font-size:26px;font-weight:800;letter-spacing:-.01em'>🔎 제품 검토</div>"
    "<div style='color:#9aa3b2;font-size:12px;margin-bottom:8px'>매출 IQVIA·UBIST · 허가/특허/임상 식약처 · 약가 심평원+NHIS</div>",
    unsafe_allow_html=True)
_data_status_banner()

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
        if basis == "제품명" and _edic:
            edis_self = {e for x in _ap0[_ap0.get("품목명", pd.Series(dtype=str)).astype(str) == q][_edic].dropna()
                         for e in _edi_codes(x)}
        if cores:
            _ac = (_ap0["_core"] if "_core" in _ap0.columns
                   else ss.ing_core_series(_ap0.get("주성분", pd.Series(dtype=str))))
            _same = _ap0[_ac.isin(cores)]
            ing_keys |= {k for k in (ss.ing_key(x) for x in _same.get("주성분(영문)", pd.Series(dtype=str)).dropna()) if k}
            if _edic:
                edis_same = {e for x in _same[_edic].dropna() for e in _edi_codes(x)}
        edis_self = {e for e in edis_self if len(e) >= 6}
        edis_same = {e for e in edis_same if len(e) >= 6}
    # 허가자료에 영문 성분명이 비어 있는 성분(신약 중 더러 있음)은 위에서 영문키를 못 만든다.
    # 한글→영문 매핑표로 보강해야 영문으로 적힌 매출자료와 연결된다.
    if cores and not ing_keys:
        try:
            _c2k0, _ = _kor2engkey()
            ing_keys |= {_c2k0[c] for c in cores if _c2k0.get(c)}
        except Exception:
            pass

    def _ing_union(df, base):
        """동일성분(영문 _key) 매칭 행을 기존 결과에 추가(있는 것만 더함, 제거 없음)."""
        if ing_keys and "_key" in df.columns:
            add = df[df["_key"].isin(ing_keys)]
            if not add.empty:
                return pd.concat([base, add]).drop_duplicates()
        return base

    def _kor_union(df, base, col):
        """동일성분 코어 매칭 행을 추가(약가 주성분명·임상 성분명용).
        로더에서 미리 만들어 둔 '_core' 열을 쓴다(없으면 그 자리에서 벡터 계산)."""
        if not cores:
            return base
        if "_core" in df.columns:
            cc = df["_core"]
        elif col in df.columns:
            cc = ss.ing_core_series(df[col])
        else:
            return base
        add = df[cc.isin(cores)]
        if not add.empty:
            return pd.concat([base, add]).drop_duplicates()
        return base

    if q and not has_sales(src1):
        st.info(f"{src1} 매출 자료가 아직 준비되지 않았습니다 — 허가/특허/임상/약가는 아래에서 확인할 수 있습니다.")
        sales_load_button("btn_fetch_sales_search", "📊 매출 자료 지금 불러오기")
    if q and has_sales(src1):
        df, yr, cfg = get(src1)
        # 매출 매칭: ① 브랜드 코어 ② 제형어(정/캡슐/주 등) 제거한 더 짧은 브랜드 토큰으로 제품명 검색
        _bt = re.sub(_FORM_RE, "", q_brand)
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

        # 동일성분 매칭 마스크. 매출자료의 성분명은 영문(예: 'semaglutide 0.25mg')이고
        # 검색어는 한글(세마글루티드)이라, 영문 성분키로 맞춰야 연결된다.
        _fm = pd.Series(False, index=df.index)
        if cfg["ing"] in df.columns:
            _ingc = df[cfg["ing"]].astype(str)
            if ing_keys:
                _fm = _fm | ss.ing_key_series(_ingc).isin(ing_keys)
            if cores:
                _sub = "|".join(re.escape(c) for c in cores)      # 한글 표기 자료용(부분 포함)
                _fm = (_fm | ss.ing_core_series(_ingc).isin(cores)
                       | _ingc.str.contains(_sub, regex=True, na=False))
        if _sedi is not None and edis_same:
            _fm = _fm | _sedi.isin(edis_same)

        if basis == "주성분":                          # 성분으로 찾았으면 성분 매출 전체를 본다
            hit, _by_ing = df[_fm], True
        else:
            hit, _by_ing, _by_pref = df[_pm], False, False
            if hit.empty and _terms:
                # 허가명에만 붙은 추가 표기(예: '…에스현탁액' ↔ '…현탁액') 때문에 글자 포함으로는
                # 안 걸리는 경우가 있다. 양쪽에서 제형어를 떼고, 매출 제품명이 허가명의
                # '앞부분'과 정확히 같으면 같은 브랜드로 본다(4자 이상일 때만).
                _pb = _prodn.str.replace(_FORM_RE, "", regex=True)
                _pref = {t[:k] for t in _terms for k in range(4, len(t) + 1)}
                _pm2 = _pb.isin(_pref) if _pref else pd.Series(False, index=df.index)
                if _pm2.any():
                    hit, _by_pref = df[_pm2], True
            if hit.empty and _fm.any():                # 그래도 없으면 → 동일성분 매출로 폴백
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
                st.caption("※ 선택한 성분의 매출을 합산했습니다." if basis == "주성분"
                           else "※ 제품명 직접 매칭이 없어 '동일성분' 매출을 합산해 보여줍니다.")
            elif _by_pref:
                _names = ", ".join(sorted(hit[cfg["prod"]].astype(str).unique())[:8])
                st.caption(f"※ 제품명 표기가 자료마다 달라, 앞부분이 같은 제품으로 매칭했습니다 → {_names}")
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
                show_df(m[cc].sort_values("허가일자", ascending=False), height=300, file_name="허가목록.csv")
            else: st.info("허가 데이터 미연결")
        with tabs[1]:
            if HAS_REG and HAS_PAT:
                pt = ss.load_patent()
                m = _kor_union(pt, _ing_union(pt, contains(pt, ["품목명", "INGR_ENG_NAME", "INGR_NAME"], q)), "INGR_NAME").copy()
                m["만료D(년)"] = ((m["_exp"] - TODAY).dt.days / 365.25).round(1)
                _live = ss.active_patent_mask(m["DOMESTIC_PATENT_STATUS"])
                if ss.has_listed_flag(m):
                    _lst = ss.listed_patent_mask(m)
                    st.caption(f"특허 {len(m):,}건 · 식약처 등재(제품특허) {int(_lst.sum())}건 "
                               f"(존속 등록 {int((_lst & _live).sum())}건) · 기타특허 {int((~_lst).sum())}건")
                    st.caption("만료일 판단은 **제품특허**(원개발사가 식약처 특허목록에 등재한 특허)로 합니다. "
                               "기타특허는 같은 성분에 관한 다른 특허(다른 회사의 염·수화물·조성 특허 등)라 참고용입니다.")
                else:
                    st.caption(f"특허 {len(m):,}건 · 존속 등록 {int(_live.sum())}건")
                cmap = {ss.PATENT_PAGE_COL: "구분", "품목명": "품목명", "PATENT_GB_CODE": "유형",
                        "PATENTEE": "특허권자", "DOMESTIC_PATENT_NO": "특허번호",
                        "DOMESTIC_PATENT_STATUS": "상태", "DOMESTIC_END_DATE": "만료일", "만료D(년)": "만료D(년)"}
                cmap = {k2: v for k2, v in cmap.items() if k2 in m.columns}
                show_df(m.rename(columns=cmap)[list(cmap.values())].sort_values("만료일", ascending=False),
                        height=300, file_name="특허목록.csv")
            else: st.info("특허 데이터 미연결")
        with tabs[2]:
            if HAS_REG and HAS_CLIN:
                cl = ss.load_clinical()
                if "성분명" in cl.columns:
                    cl = cl.copy(); cl["_key"] = cl["성분명"].map(ss.ing_key)
                m = _kor_union(cl, _ing_union(cl, contains(cl, ["제품명", "성분명"], q)), "성분명")
                _step = m["CLINIC_STEP_NM"] if "CLINIC_STEP_NM" in m.columns else pd.Series([], dtype=str)
                st.caption(f"임상 {len(m):,}건 · 생동(제네릭 개발) {int(_step.astype(str).str.contains('생동', na=False).sum())}건")
                cmap = {"제품명": "제품명", "성분명": "성분명", "CLINIC_STEP_NM": "단계", "TRGT_DISS_NM": "대상질환",
                        "STATUS": "상태", "CLST_APRV_DT": "승인일", "원개발사": "개발사"}
                cc = {k2: v for k2, v in cmap.items() if k2 in m.columns}
                show_df(m.rename(columns=cc)[list(cc.values())].sort_values("승인일", ascending=False),
                        height=300, file_name="임상목록.csv")
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
                    mat_dates, form_dates, use_dates, appr, reg = [], [], [], None, None
                    if HAS_REG:
                        pt = ss.load_patent()
                        reg = pt[pt["_key"].isin(sel_keys) & ss.active_patent_mask(pt["DOMESTIC_PATENT_STATUS"])
                                 & pt["_exp"].notna() & ss.listed_patent_mask(pt)]
                        mat_dates = sorted(reg[ss.substance_mask(reg["PATENT_GB_CODE"])]["_exp"].dropna().unique())
                        form_dates = sorted(reg[ss.gb_mask(reg["PATENT_GB_CODE"], "물질형태")
                                                & ~ss.substance_mask(reg["PATENT_GB_CODE"])]["_exp"].dropna().unique())
                        use_dates = sorted(reg[ss.gb_mask(reg["PATENT_GB_CODE"], "용도")]["_exp"].dropna().unique())
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
                    # 특허 만료선은 모아서 한 번에 넣는다. add_vline 을 하나씩 부르면 선이
                    # 늘 때마다 그림 전체를 다시 복사해, 선 수의 제곱으로 느려진다.
                    _shp, _ann = [], []
                    for _ds, _col, _dash, _y, _txt in (
                            (mat_dates, "crimson", "dash", 1.0, "물질특허만료"),
                            (form_dates, "mediumpurple", "dashdot", 0.64, "염·형태특허만료"),
                            (use_dates, "darkorange", "dot", 0.82, "용도특허만료")):
                        for d in _ds:
                            dt = pd.Timestamp(d)
                            _shp.append(dict(type="line", xref="x", yref="paper", x0=dt, x1=dt, y0=0, y1=1,
                                             line=dict(color=_col, dash=_dash)))
                            _ann.append(dict(x=dt, y=_y, xref="x", yref="paper", text=_txt, showarrow=False,
                                             font=dict(color=_col, size=9), textangle=-90, xanchor="left"))
                    if _shp:
                        fig.update_layout(shapes=_shp, annotations=_ann)
                    if appr is not None and not appr.empty:
                        by = (one_gy["금액"].max() if not one_gy.empty else 1) * 0.03
                        fig.add_trace(go.Scatter(x=appr["_hd"], y=[by] * len(appr), mode="markers",
                                      marker=dict(color="green", symbol="triangle-up", size=8, opacity=0.5), name="동일성분 등재(허가)",
                                      text=appr["품목명"], hovertemplate="%{x|%Y-%m-%d} 등재<br>%{text}<extra></extra>"))
                    fig.update_layout(height=440, title=f"{sel} — 급여 상한금액 전체 이력 + 등재·특허 만료",
                                      yaxis=_WON_AXIS, legend=dict(orientation="h", y=-0.25), hovermode="x unified")
                    st.plotly_chart(fig, use_container_width=True)
                    st.caption("파란선=선택 품목 · 회색=동일성분 타 품목 · 초록△=동일성분 등재(허가) · 빨강=물질특허만료 · "
                               "보라=염·형태특허만료 · 주황=용도특허만료 · ✕=급여삭제 (특허는 식약처 등재 특허만)")
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
                    show_df(m[ordered].rename(columns={c: label.get(c, c) for c in ordered}),
                            height=320, file_name="재심사DMF.csv")
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
                _no_match = m.empty   # 거르기 전부터 동일성분이 하나도 없는지
                _stc, _lkc = ss.dmf_status_col(dmf), ss.dmf_link_col(dmf)
                _mtag = (f" · 매칭 성분: {', '.join(sorted(cores)[:6])}" if cores else "")
                if _stc:
                    # 식약처 최신 목록(매일 새벽 로봇 수집) — 상태·연계심사 정보가 있다
                    _meta = ss.dmf_meta() or {}
                    _dt = pd.to_datetime(m.get("DMF_PERMIT_DATE", pd.Series(dtype=str)), errors="coerce")
                    _okm = m[_stc].astype(str).str.strip().eq("정상")
                    _new = _okm & (_dt >= pd.Timestamp.today().normalize() - pd.Timedelta(days=90))
                    _lnk = m[_lkc].astype(str).str.strip().ne("") if _lkc else pd.Series(False, index=m.index)
                    st.caption(f"식약처 DMF 공고 기준"
                               + (f" · {_meta.get('fetched_at')} 받음" if _meta.get("fetched_at") else "")
                               + f" · 동일성분 DMF {len(m):,}건 (정상 {int(_okm.sum()):,} · 전체 {len(dmf):,}건)"
                               + f" · 연계심사 문서 있음 {int(_lnk.sum()):,}건 · 최근 90일 새 등록 {int(_new.sum()):,}건"
                               + _mtag)
                    _c1, _c2 = st.columns(2)
                    _inc_off = _c1.checkbox("취하·취소·폐업 포함", value=False, key="dmf_inc_off")
                    _lnk_only = _c2.checkbox("연계심사만 보기", value=False, key="dmf_link_only")
                    if not m.empty:
                        m = m.assign(상태=m[_stc].astype(str).str.strip().where(~_new, "새 등록"),
                                     연계심사=_lnk.map({True: "연계심사", False: ""}),
                                     _dt=_dt)
                        if not _inc_off:
                            m = m[_okm.reindex(m.index, fill_value=False)]
                        if _lnk_only:
                            m = m[m["연계심사"] == "연계심사"]
                        m = m.sort_values("_dt", ascending=False, na_position="last").drop(columns="_dt")
                else:
                    st.caption(f"동일성분 DMF {len(m):,}건 (전체 {len(dmf):,}건)" + _mtag)
                if not _no_match and m.empty:
                    st.info("조건에 맞는 DMF가 없습니다. '취하·취소·폐업 포함'을 켜거나 '연계심사만 보기'를 꺼 보세요.")
                elif _no_match:
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
                    if _stc:
                        label = dict(label, ENTP_NAME="신청인", MNFCTR_NAME="제조소",
                                     DMF_PERMIT_DATE="최초등록일", DMF_PERMIT_NO="등록번호")
                        prefer = [c for c in ["상태", "연계심사", ing_col, "ENTP_NAME", "MNFCTR_NAME",
                                              "MANUF_COUNTRY_CODE_NM", "DMF_PERMIT_DATE", "최종변경일자",
                                              "DMF_PERMIT_NO", "문서번호", "연계심사문서번호"]
                                  if c and c in m.columns]
                        rest = [c for c in m.columns if c not in prefer and c != _stc]
                        ordered = prefer + rest
                    else:
                        prefer = [c for c in [ing_col, "ENTP_NAME"] if c and c in m.columns]
                        ordered = prefer + [c for c in m.columns if c not in prefer]
                    show_df(m[ordered].rename(columns={c: label.get(c, c) for c in ordered}),
                            height=320, file_name="재심사DMF.csv")

# ══════════════════════════ 제품 제안 (필터 기반) ══════════════════════════
if PAGE == "sugg":
    st.subheader("🎯 제품 제안 — 조건 필터")
    a, b = st.columns(2)
    src2 = a.radio("매출 자료원", ["IQVIA", "UBIST"], horizontal=True, key="s2")
    unit = b.radio("분석 단위", ["성분별", "제품별"], horizontal=True, key="u2")
    if not has_sales(src2):
        st.info(f"{src2} 매출 자료가 아직 준비되지 않았습니다 — 제품 제안은 매출 자료가 있어야 동작합니다.\n\n"
                "아래 버튼을 누르면 지금 받아옵니다(처음엔 1~2분 걸릴 수 있습니다).")
        sales_load_button("btn_fetch_sales_sugg")
        st.stop()
    df, yr, cfg = get(src2)
    m = sales_agg_cached(src2, cfg["ing"] if unit == "성분별" else cfg["prod"], cfg["maker"])
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

    _nm = m[name]
    _k = ss.ing_key_series(_nm)                        # ① 영문 성분명이면 바로 키
    _blank = _k.str.len() == 0
    if _blank.any():
        if unit == "성분별":                            # ② 한글 성분명 → 매핑표
            _k.loc[_blank] = ss.ing_core_series(_nm[_blank]).map(_c2k).fillna("")
        else:                                          # ② 브랜드명 → 매핑표
            _b = (_nm[_blank].astype(str).str.replace(r"\s+", "", regex=True)
                  .str.split(r"[0-9(\[]", regex=True).str[0].fillna(""))
            _k.loc[_blank] = _b.map(_b2k).fillna("")
            _still = (_k.str.len() == 0) & _blank       # ③ 미매칭 → 매출자료의 성분명으로
            if _still.any() and _p2i:
                _i = _nm[_still].map(_p2i).fillna("")
                _ik = ss.ing_key_series(_i)
                _ik = _ik.where(_ik.str.len() > 0, ss.ing_core_series(_i).map(_c2k).fillna(""))
                _k.loc[_still] = _ik
    m["_key"] = _k.fillna("")
    if HAS_REG:
        # 단일제 후보에는 단일 성분 품목의 특허만 반영한다.
        # (조인키가 영문 성분명의 첫 단어라, 그대로 두면 그 성분이 들어간 복합제
        #  특허까지 단일제에 붙어 실제보다 늦게 풀리는 것처럼 보인다)
        def _ingname(v):
            return _p2i.get(v, v) if unit == "제품별" else v
        m["_solo"] = m[name].map(lambda v: ss.is_single_ing(_ingname(v)))

        def _pbk_for(solo):
            """성분별은 오리지널(신약) 품목의 특허만 본다.
            제네릭사가 따로 낸 염·제형 특허가 그 성분 전체를 막는 것처럼 보이지
            않도록. 오리지널 품목이 허가자료에 없는 성분은 전체 기준으로 보되
            근거에 표시한다."""
            base = ss.patent_by_key(single_only=solo)
            if unit != "성분별":
                return base
            orig = ss.patent_by_key(single_only=solo, original_only=True).copy()
            _, _, basis = ss.original_items()

            def _mark(dfx, tag):
                for c in ("근거_물질", "근거_물질형태", "근거_물질기타", "근거_용도"):
                    if c in dfx.columns:
                        dfx[c] = [f"{tag(k)}{t}" if t else ""
                                  for k, t in zip(dfx["_key"], dfx[c].fillna(""))]
                return dfx

            # 신약 표기가 없어 '최초 허가 품목'을 오리지널로 본 성분은 그렇게 표시
            orig = _mark(orig, lambda k: "" if basis.get(k) == "신약" else "ⓘ최초허가 기준 · ")
            rest = _mark(base[~base["_key"].isin(basis)].copy(), lambda k: "⚠오리지널 미확인 · ")
            return pd.concat([orig, rest], ignore_index=True)

        _parts = []
        for _solo in (True, False):
            _sub = m[m["_solo"] == _solo]
            if _sub.empty:
                continue
            _pbk = _pbk_for(_solo)
            _pbk = _pbk[_pbk["_key"].astype(str).str.len() > 0].drop_duplicates("_key")
            _parts.append(_sub.merge(_pbk, on="_key", how="left"))
        m = pd.concat(_parts, ignore_index=True) if _parts else m.drop(columns=["_solo"])

    # ATC(매출자료 기준)
    _keycol = cfg["ing"] if unit == "성분별" else cfg["prod"]
    _atc = cfg.get("atc1")
    if _atc and _atc in df.columns:
        _av = df[_atc].astype(str).str.strip()
        _ok = _av.str.len().gt(0) & ~_av.str.lower().eq("nan")
        _amap = df.loc[_ok, [_keycol]].assign(_a=_av[_ok]).groupby(_keycol)["_a"].first()
        m["ATC"] = m[name].map(_amap).fillna("")

    st.markdown("**필터 조건**")
    f1, f2, f3, f4 = st.columns(4)
    min_size = f1.number_input("① 최소 시장규모(억, 최신연도)", value=50, step=10, min_value=0)
    min_cagr = f2.number_input("② 최소 성장 CAGR(%)", value=0.0, step=5.0)
    cy = int(TODAY.year)
    with f3:
        # 화면 이름 → 집계 칸(특허만료_○○). 염·수화물·이성체 특허는 다른 염·형태로
        # 피할 수 있어 따로 둔다. 물질(기타)는 항체 의약품 등에서 실제 성분 특허일 수
        # 있어 기본으로 함께 본다(예전 '물질'과 같고, 염·수화물·이성체만 빠진다).
        _PT_KIND = {"물질": "물질", "물질(염·수화물·이성체)": "물질형태",
                    "물질(기타)": "물질기타", "용도": "용도"}
        _pt_sel = st.multiselect("③ 특허 만료 기준 유형", list(_PT_KIND),
                                 default=["물질", "물질(기타)"] if HAS_REG else [],
                                 help="물질 = 성분 자체 · 물질(염·수화물·이성체) = 다른 염·형태로 피할 수 "
                                      "있는 특허 · 물질(기타) = 항체 의약품 등에서는 성분 특허일 수 있음")
        ptypes = [_PT_KIND[t] for t in _pt_sel]
        sel_year = st.selectbox("이 연도까지 특허 만료", list(range(cy, cy + 16)), index=5,
                                help="선택한 유형 특허가 이 연도까지 만료(또는 없음)인 후보만")
    with f4:
        _atc_opts = sorted({str(x).strip() for x in m.get("ATC", pd.Series(dtype=str))
                            if str(x).strip() and str(x).strip().lower() != "nan"})
        atc_sel = st.multiselect("④ ATC 계열", _atc_opts, default=[],
                                 help="비우면 전체. 선택하면 해당 계열만")

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

    res = res.sort_values("시장규모", ascending=False)
    res["시장규모(억)"] = (res["시장규모"] / 1e8).round(1)
    res["CAGR(%)"] = res["CAGR"].round(1)
    # (화면 칸 이름, 집계 칸 이름)
    _PT_COLS = [("물질특허", "물질"), ("염·형태특허", "물질형태"),
                ("물질(기타)특허", "물질기타"), ("용도특허", "용도")]
    if HAS_REG:
        for _lab, _k in _PT_COLS:
            res[f"{_lab} 만료"] = pd.to_datetime(res.get(f"특허만료_{_k}")).dt.date.astype("string")
            res[f"{_lab} 근거"] = res.get(f"근거_{_k}", pd.Series("", index=res.index)).fillna("")
    cols = ([name] + (["ATC"] if "ATC" in res.columns else [])
            + ["시장규모(억)", "CAGR(%)"]
            + ([f"{_lab} 만료" for _lab, _ in _PT_COLS] if HAS_REG else []))
    src_cols = [f"{_lab} 근거" for _lab, _ in _PT_COLS] if HAS_REG else []
    st.markdown(f"#### ✅ 조건 충족 후보 {len(res):,}개")
    show_src = st.checkbox("특허 근거 표시 (그 만료일이 어느 품목·유형의 특허에서 왔는지)",
                           value=False, key="sugg_src") if HAS_REG else False
    vcols = cols + (src_cols if show_src else [])
    st.dataframe(res[vcols].head(200), use_container_width=True, height=430, hide_index=True)
    st.download_button("⬇️ CSV", res[cols + src_cols].to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"제품제안_{src2}_{unit}.csv")
    st.caption("가중치 점수 없이, 큰 시장·고성장·특허만료·ATC 조건을 직접 필터링합니다. · "
               "ATC는 매출자료 기준 · "
               "특허 만료일=존속 중인 등록특허 중 가장 늦은 날짜(소멸·무효 제외). "
               "**식약처 등재 특허(제품특허)만** 봅니다. 물질특허는 성분 자체 특허이고, "
               "염·수화물·이성체 특허는 ‘염·형태특허’로 따로 보여줍니다. "
               "**단일제 후보는 단일제 특허만** 반영하고, 복합제 후보만 복합제 특허를 포함합니다. "
               "**성분별은 오리지널 품목의 특허 기준** — 1순위 신약구분=‘신약’, 없으면 그 성분 최초 허가 품목"
               "(근거에 ‘ⓘ최초허가 기준’ 표시). 둘 다 못 찾으면 전체 기준으로 표시하고 ‘⚠오리지널 미확인’으로 알립니다 "
               "(‘특허 근거 표시’로 확인)")

# ══════════════════════════ 매출 분석 (기존 app.py 4개 탭 그대로) ══════════════════════════
if PAGE == "sales":
    # 매출(IQVIA·UBIST)은 데이터가 커서 메모리를 많이 쓴다.
    # 무료 플랜 안정성을 위해 버튼을 눌렀을 때만 불러온다(기본은 미로딩).
    _sales_ready = has_sales("IQVIA") or has_sales("UBIST")
    if st.session_state.get("_load_sales") and _sales_ready:
        import sales_analysis
        try:
            sales_analysis.render()
        except Exception as _e:      # 매출 대시보드 오류가 앱 전체를 멈추지 않게
            st.error("매출 분석을 여는 중 문제가 생겼습니다. 다른 탭은 정상 사용할 수 있습니다.")
            with st.expander("자세한 오류 내용 (개발자용)"):
                st.code(f"{type(_e).__name__}: {_e}")
        if st.button("🧹 매출 데이터 닫기(메모리 절약)", key="btn_unload_sales"):
            st.session_state["_load_sales"] = False
            st.cache_data.clear()
            st.rerun()
    else:
        st.info("매출 분석 데이터(IQVIA·UBIST)는 용량이 커서 필요할 때만 불러옵니다.\n\n"
                "아래 버튼을 누르면 매출 대시보드가 열립니다."
                + ("" if _sales_ready else
                   "\n\n아직 자료가 준비되지 않았다면 버튼을 누를 때 함께 받아옵니다"
                   "(처음엔 1~2분 걸릴 수 있습니다)."))
        if st.button("📊 매출 분석 불러오기", key="btn_load_sales", type="primary"):
            if _sales_ready or fetch_sales_now():
                st.session_state["_load_sales"] = True
                st.rerun()

# ══════════════════════════ 특허 분석 ══════════════════════════
if PAGE == "patent":
    st.subheader("⚖️ 특허 분석 — 조건별")
    if not (HAS_REG and HAS_PAT):
        st.info("특허 데이터 미연결")
    else:
        pt = ss.load_patent().copy()
        if ss.has_listed_flag(pt):
            # 기본은 식약처 등재 특허(제품특허)만. 고른 값이 선택지에 없으면 오류가 나므로
            # 기본값은 실제 선택지 안에서만 고른다.
            _pg = pt[ss.PATENT_PAGE_COL].astype(str).str.strip()
            _pg_opts = sorted(_pg[_pg.str.len() > 0].unique().tolist())
            _pg_def = [o for o in _pg_opts if "제품" in o]
            _pg_sel = st.multiselect("구분", _pg_opts, default=_pg_def, key="p_page",
                                     help="제품특허 = 원개발사가 식약처 의약품 특허목록에 등재한 특허. "
                                          "기타특허 = 같은 성분에 관한 다른 특허(다른 회사의 염·수화물·조성 특허 등).")
            pt = isin(pt, ss.PATENT_PAGE_COL, _pg_sel)
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
            cmap = {ss.PATENT_PAGE_COL: "구분", "품목명": "품목명", "INGR_NAME": "성분", "PATENT_GB_CODE": "유형",
                    "PATENTEE": "특허권자", "DOMESTIC_PATENT_STATUS": "상태", "DOMESTIC_END_DATE": "만료일",
                    "만료D(년)": "만료D(년)"}
            cc = {k2: v for k2, v in cmap.items() if k2 in pt.columns}
            show_df(pt.rename(columns=cc)[list(cc.values())].sort_values("만료일"), height=360, file_name="특허분석.csv")

# ══════════════════════════ 임상 분석 ══════════════════════════
# ── 임상 적응증(대상질환) 키워드 검색용 통합 텍스트 ───────────────────────
# 적응증은 한 칸에만 적히지 않는다. 대상질환명·분류·세부·기타에 더해 시험 제목과
# 시험 목적에도 들어가므로, 이 칸들을 모두 합친 텍스트에서 찾는다.
_DIS_COLS = ["TRGT_DISS_NM", "TRGT_DISS_CD_NM", "TRGT_DISS_DTL_CD_NM", "TRGT_DISS_DTL_ETC",
             "CLNC_TEST_TITLE", "IVSGT_PURPS"]

@st.cache_data(show_spinner=False)
def _clinical_dis_blob():
    """적응증 관련 칸을 하나로 합쳐 공백 제거·소문자화한 검색용 시리즈."""
    cl = ss.load_clinical()
    cols = [c for c in _DIS_COLS if c in cl.columns]
    if not cols:
        return pd.Series("", index=cl.index), []
    b = cl[cols[0]].astype(str)
    for c in cols[1:]:
        b = b + " " + cl[c].astype(str)
    return b.str.replace(r"\s+", "", regex=True).str.lower(), cols

def _dis_mask(index, kw, mode):
    """적응증 키워드 매칭 마스크. mode: '모두 포함(AND)' | '하나라도 포함(OR)'."""
    terms = [re.sub(r"\s+", "", t).lower() for t in re.split(r"[,;\s]+", str(kw)) if t.strip()]
    if not terms:
        return pd.Series(True, index=index)
    blob, _ = _clinical_dis_blob()
    hits = [blob.str.contains(t, regex=False, na=False) for t in terms]
    m = hits[0]
    for h in hits[1:]:
        m = (m & h) if mode.startswith("모두") else (m | h)
    return m.reindex(index, fill_value=False)

# ── 임상 단계 바구니 ────────────────────────────────────────────────────
# 자료에는 1상·1b상·1/2a상·연구자 임상시험·연장 등 19종이 섞여 있다.
# 글자 모양으로 가르면 새 표기가 생겨도 규칙이 받는다.
#   명확히 n상인 것만 n상 · 걸친 것(1/2상)은 따로 · 연구자·연장은 분석에서 제외
_STEP_ONE = re.compile(r"(\d)[ab]?상")
_STEP_MIX = re.compile(r"\d[ab]?/\d[ab]?상")

def step_bucket(v):
    t = str(v).strip()
    if "생동" in t:
        return "생동"
    if "연구자" in t or "연장" in t:
        return "제외"
    m = _STEP_ONE.fullmatch(t)
    if m:
        return m.group(1) + "상"
    if _STEP_MIX.fullmatch(t):
        return re.sub(r"[ab]", "", t)      # 1/2a상 → 1/2상
    return "미분류"

def step_bucket_series(sr):
    """값 종류가 스무 개 남짓이라 고유값만 계산해 map 한다(행마다 돌리면 느리다)."""
    s = sr.astype(str)
    return s.map({u: step_bucket(u) for u in s.unique()})

# 보기 좋은 차례. 여기 없는 바구니는 그래프에서 뺀다(미분류·0상 등은 건수가 미미).
STEP_ORDER = ["생동", "1상", "1/2상", "2상", "2/3상", "3상", "4상"]

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

        # ── 보기 전환: 검색(지금까지 쓰던 화면) / 분석(새 화면) ──────────────
        # 탭이 아니라 라디오를 쓰는 이유: st.tabs 는 안 보이는 탭까지 전부 계산한다.
        # 라디오 + st.stop() 이면 고른 쪽만 돈다(약가 분석 탭과 같은 방식).
        _view = st.radio("보기", ["🔍 검색", "📊 분석", "🧮 생동 피험자 수",
                                  "💰 1상 비용 어림"],
                         horizontal=True, key="clin_view", label_visibility="collapsed")

        if _view.endswith("피험자 수"):
            _be_page()
            ss.release_memory()
            st.stop()

        if _view.endswith("비용 어림"):
            _cost_page()
            ss.release_memory()
            st.stop()

        if _view.endswith("분석"):
            _date_c = "CLST_APRV_DT" if "CLST_APRV_DT" in cl.columns else None
            _step_c = "CLINIC_STEP_NM" if "CLINIC_STEP_NM" in cl.columns else None
            if not (_date_c and _step_c):
                st.info("임상 자료에 승인일 또는 단계 칸이 없어 분석을 할 수 없습니다.")
                ss.release_memory()
                st.stop()

            # 기간
            _PERIODS = {"1주": 7, "1달": 30, "3달": 90, "1년": 365, "3년": 1095, "전체": None}
            a1, a2 = st.columns([2, 3])
            _pname = a1.radio("승인일 기간", list(_PERIODS), index=2,
                              horizontal=True, key="clin_period")
            _akw = a2.text_input("적응증 키워드", key="clin_kw",
                                 placeholder="예: 당뇨 비만  (비우면 전체)")
            b1, b2 = st.columns([2, 3])
            _adev = b1.text_input("개발사", key="clin_dev2", placeholder="비우면 전체")
            _amode = b2.radio("키워드 조건", ["모두 포함(AND)", "하나라도 포함(OR)"],
                              horizontal=True, key="clin_kwmode")
            # 어느 단계를 그래프에 올릴지. 기본은 전부.
            _apick = st.multiselect("볼 단계", STEP_ORDER, default=STEP_ORDER,
                                    key="clin_steps",
                                    help="고른 단계만 봅니다. 지표·그래프·성분 순위에 모두 걸립니다. "
                                         "비우면 전체로 돌아갑니다.")

            # 같은 조건을 '올해'와 '작년 같은 기간'에 각각 적용해 비교한다
            _d = pd.to_datetime(cl[_date_c], errors="coerce")
            _bk = step_bucket_series(cl[_step_c])
            _sel = pd.Series(True, index=cl.index)
            if _akw.strip():
                _sel &= _dis_mask(cl.index, _akw, _amode)
            if _adev.strip() and "원개발사" in cl.columns:
                _sel &= cl["원개발사"].astype(str).str.contains(
                    _adev.strip(), case=False, na=False, regex=False)
            _sel &= _bk.ne("제외")              # 연구자 임상시험·연장은 분석에서 제외
            if _apick and set(_apick) != set(STEP_ORDER):
                _sel &= _bk.isin(_apick)       # 고른 단계만 — 아래 전부에 걸린다

            _days = _PERIODS[_pname]
            _now = pd.Timestamp.now().normalize()
            if _days is None:
                _cur = _sel & _d.notna()
                _prev = pd.Series(False, index=cl.index)
            else:
                _cur = _sel & (_d >= _now - pd.Timedelta(days=_days)) & (_d <= _now)
                _prev = (_sel & (_d >= _now - pd.Timedelta(days=365 + _days))
                         & (_d < _now - pd.Timedelta(days=365)))

            _m = cl[_cur]
            _mb = _bk[_cur]
            _np = int(_prev.sum())

            def _delta(now_n, prev_n):
                """작년 같은 기간 대비. 비교 대상이 없으면 표시하지 않는다."""
                if _days is None or prev_n < 5:
                    return None
                return f"{(now_n - prev_n) / prev_n * 100:+.0f}%"

            _nbio = int((_mb == "생동").sum())
            _n3 = int((_mb == "3상").sum())
            if "성분명" in _m.columns:
                _core = ss.ing_core_series(_m["성분명"])
                # 한글이 없는 이름(개발코드·영문명)은 허가·약가와 못 잇는다
                _named = _m["성분명"].astype(str).str.contains("[가-힣]", regex=True, na=False)
            else:
                _core = pd.Series("", index=_m.index)
                _named = pd.Series(False, index=_m.index)
            _nnamed = int(_named.sum())

            k = st.columns(5)
            k[0].metric("임상 건수", f"{len(_m):,}", _delta(len(_m), _np))
            k[1].metric("생동 (제네릭 개발)", f"{_nbio:,}",
                        _delta(_nbio, int((_bk[_prev] == '생동').sum())) if _np else None)
            k[2].metric("3상 (2~3년 뒤 진입)", f"{_n3:,}",
                        _delta(_n3, int((_bk[_prev] == '3상').sum())) if _np else None)
            k[3].metric("성분 확인 비율",
                        f"{(100 * _nnamed / len(_m)):.0f}%" if len(_m) else "—",
                        help="성분명이 한글로 적힌 비율. 개발코드(RD5208-T 등)는 허가·약가와 이을 수 없습니다.")
            k[4].metric("개발사 수",
                        f"{_m['원개발사'].nunique():,}" if "원개발사" in _m.columns else "—")
            if _days is not None and _np >= 5:
                st.caption(f"증감은 **작년 같은 기간**({_np:,}건) 대비입니다. "
                           "비교 대상이 5건 미만이면 표시하지 않습니다.")

            if _m.empty:
                st.info("조건에 맞는 임상이 없습니다. 기간을 넓히거나 키워드를 지워보세요.")
                ss.release_memory()
                st.stop()

            # ── 그래프 ──
            g1, g2 = st.columns(2)

            # 기간에 맞춰 눈금을 주/월/분기로 바꾼다
            _freq, _flab = ("W", "주") if _days and _days <= 60 else \
                           (("M", "월") if (_days or 9999) <= 400 else ("Q", "분기"))
            _per = _m[_date_c].pipe(pd.to_datetime, errors="coerce").dt.to_period(_freq)
            _keep = [b for b in (_apick or STEP_ORDER) if b in set(_mb)]
            _cnt = (pd.DataFrame({"기간": _per, "단계": _mb.values})
                    .dropna(subset=["기간"]))
            _cnt = _cnt[_cnt["단계"].isin(_keep)]
            if not _cnt.empty and _keep:
                # 빈 구간은 0으로 채운다. 안 그러면 없는 분기를 건너뛰며 선이 이어져
                # 실제보다 완만해 보이고, 단계마다 x축 차례가 달라져 선이 뒤로 꺾인다.
                _idx = pd.period_range(_cnt["기간"].min(), _cnt["기간"].max(), freq=_freq)
                _grid = (_cnt.value_counts(["기간", "단계"])
                         .unstack("단계").reindex(_idx).fillna(0)
                         .reindex(columns=_keep, fill_value=0).astype(int))
                _order = [str(x) for x in _idx]          # 시간 순서를 x축에 못박는다
                _tr = (_grid.rename_axis("기간").reset_index()
                       .assign(기간=lambda d: d["기간"].astype(str))
                       .melt(id_vars="기간", var_name="단계", value_name="건수"))
            else:
                _tr, _order = pd.DataFrame(columns=["기간", "단계", "건수"]), []
            if len(_order) >= 2:
                g1.plotly_chart(px.line(_tr, x="기간", y="건수", color="단계", markers=True,
                                        category_orders={"단계": _keep, "기간": _order},
                                        title=f"단계별 추이 ({_flab} 단위)"),
                                use_container_width=True)
            else:
                _vc = _mb.value_counts().reindex(_keep).dropna().reset_index()
                _vc.columns = ["단계", "건수"]
                g1.plotly_chart(px.bar(_vc, x="단계", y="건수", title="단계별 건수"),
                                use_container_width=True)
                g1.caption("기간이 짧아 구간이 하나뿐입니다 — 추이 대신 건수로 보여드립니다.")

            _yr = pd.to_datetime(_m[_date_c], errors="coerce").dt.year.dropna()
            if _yr.nunique() < 2:
                g2.info("고른 기간이 한 해 안에 들어 있어 연도별 그래프는 생략했습니다. "
                        "기간을 1년 이상으로 넓히면 나타납니다.")
            elif not _yr.empty:
                _yc = _yr.astype(int).value_counts().sort_index().reset_index()
                _yc.columns = ["연도", "건수"]
                _fy = px.bar(_yc, x="연도", y="건수", title="연도별 임상 승인 추이")
                # 연도는 정수다. 그냥 두면 2,022.5 같은 눈금이 생긴다.
                _fy.update_xaxes(type="category", tickformat="d", separatethousands=False)
                g2.plotly_chart(_fy, use_container_width=True)
                if int(_yr.max()) == _now.year:
                    g2.caption(f"{_now.year}년은 아직 진행 중이라 건수가 적게 보입니다.")

            # ── 성분 순위 / 개발코드 목록 ──
            h1, h2 = st.columns(2)
            _ok = _named.values
            _scope = "·".join(_apick) if (_apick and set(_apick) != set(STEP_ORDER)) else "전 단계"
            h1.markdown(f"**🧬 성분별 ({_scope})** · {len(_m):,}건 중 성분 확인 {int(_ok.sum()):,}건")
            if _ok.any():
                # 성분마다 어느 단계에 몇 건인지까지 보여준다(생동 3 · 3상 1 처럼).
                _rk = pd.DataFrame({"성분": _core[_ok].values, "단계": _mb.values[_ok]})
                _rk = _rk[_rk["성분"].astype(str).str.len() > 0]
                _tot = _rk["성분"].value_counts()
                _rank = (_rk.groupby("성분")["단계"]
                         .apply(lambda v: " · ".join(f"{k} {n}" for k, n in v.value_counts().items()))
                         .rename("단계별").to_frame()
                         .assign(건수=_tot).sort_values("건수", ascending=False)
                         .head(15).reset_index()[["성분", "건수", "단계별"]])
                h1.dataframe(_rank, hide_index=True, use_container_width=True, height=300)
                h1.caption("‘아토르바스타틴칼슘삼수화물’처럼 염 표기가 붙은 이름은 떼어 한 성분으로 묶습니다.")
            else:
                h1.info("이 조건에서는 성분이 확인된 생동이 없습니다.")

            _code = _m[~_named.values]
            h2.markdown(f"**🔒 성분 미확인 (개발코드)** · {len(_code):,}건")
            if len(_code):
                _cls = (_code["TRGT_DISS_CD_NM"].astype(str).str.strip()
                        if "TRGT_DISS_CD_NM" in _code.columns
                        else pd.Series("", index=_code.index))
                _cls = _cls.mask(_cls.isin(["", "nan", "None"]), "분류 미확인")
                _cv = _cls.value_counts().head(12).reset_index()
                _cv.columns = ["질환 분류", "건수"]
                h2.dataframe(_cv, hide_index=True, use_container_width=True, height=300)
                h2.caption("성분을 못 찾으니 추이 대신 분류별 건수로 봅니다. "
                           "낱건은 ‘검색’ 보기에서 보세요.")
            else:
                h2.success("이 조건에서는 모든 건의 성분이 확인됩니다.")

            with st.expander(f"📄 분석에 쓰인 {len(_m):,}건 보기"):
                _cols = [c for c in [_date_c, _step_c, "성분명", "제품명",
                                     "TRGT_DISS_NM", "원개발사"] if c in _m.columns]
                _v = _m[_cols].copy()
                # 한글 성분명은 염을 뗀 이름을, 코드명은 코드 자체를 그대로 싣는다.
                # (예전엔 코드일 때 '— 코드명'으로 덮어써 멀쩡한 코드를 버렸다)
                _raw = _m["성분명"].astype(str).str.strip() if "성분명" in _m.columns \
                    else pd.Series("", index=_m.index)
                _v.insert(0, "묶인 성분/코드", _core.where(_named, _raw).values)
                _v.insert(0, "구분", pd.Series(_named.values).map({True: "성분", False: "코드명"}).values)
                _v.insert(0, "바구니", _mb.values)
                _v = _v.rename(columns={_date_c: "승인일", _step_c: "단계",
                                        "TRGT_DISS_NM": "대상질환", "원개발사": "개발사"})
                if "승인일" in _v.columns:
                    _v = _v.sort_values("승인일", ascending=False)
                show_df(_v, height=360, file_name="임상분석_기간.csv", key="clin_an_dl")

            ss.release_memory()
            st.stop()

        f = st.columns(4)
        f_step = msel(f[0], "임상 단계", cl[step_c], "c_step") if step_c else []
        f_stat = msel(f[1], "상태", cl[stat_c], "c_stat") if stat_c else []
        dev_kw = f[2].text_input("개발사 검색", key="c_dev")
        kw = f[3].text_input("성분/제품 검색", key="c_kw")

        f2 = st.columns([3, 1])
        f_dis = msel(f2[0], "대상질환", cl[dis_c], "c_dis") if dis_c else []

        f3 = st.columns([3, 1.4])
        dis_kw = f3[0].text_input(
            "적응증 키워드 포함", key="c_dis_kw",
            placeholder="예: 비만 당뇨   (쉼표나 띄어쓰기로 여러 개)",
            help="대상질환명·분류·세부·기타 + 임상시험 제목·시험 목적을 한꺼번에 찾습니다. 띄어쓰기는 무시합니다.")
        dis_mode = f3[1].radio("키워드 조건", ["모두 포함(AND)", "하나라도 포함(OR)"],
                               key="c_dis_mode", horizontal=False)

        m = cl
        if step_c: m = isin(m, step_c, f_step)
        if stat_c: m = isin(m, stat_c, f_stat)
        if dis_c:  m = isin(m, dis_c, f_dis)
        if dev_c and dev_kw:
            m = m[m[dev_c].astype(str).str.contains(dev_kw, case=False, na=False, regex=False)]
        if kw:
            m = contains(m, [c for c in [prod_c, ing_c] if c], kw)
        if dis_kw.strip():
            m = m[_dis_mask(m.index, dis_kw, dis_mode)]
            _terms = [t for t in re.split(r"[,;\s]+", dis_kw) if t.strip()]
            st.caption(f"적응증 키워드 {'모두' if dis_mode.startswith('모두') else '하나라도'} 포함: "
                       + " · ".join(f"‘{t}’" for t in _terms))

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
            # 그래프는 '분석' 보기로 옮겼다. 검색 보기는 찾는 데 집중한다.
            cmap = {"제품명": "제품명", "성분명": "성분명", "CLINIC_STEP_NM": "단계",
                    "TRGT_DISS_NM": "대상질환", "TRGT_DISS_DTL_CD_NM": "대상질환(세부)",
                    "CLNC_TEST_TITLE": "임상시험 제목",
                    "STATUS": "상태", "CLST_APRV_DT": "승인일", "원개발사": "개발사"}
            cc = {k2: v for k2, v in cmap.items() if k2 in m.columns}
            view = m.rename(columns=cc)[list(cc.values())]
            if "승인일" in view.columns:
                view = view.sort_values("승인일", ascending=False)
            show_df(view, height=380, file_name="임상분석.csv")

# ══════════════════════════ 약가 분석 ══════════════════════════
if PAGE == "price":
    st.subheader("💊 약가 분석")
    if not HAS_PRICE:
        st.info("약가 데이터 미연결")
    else:
        pr = ss.load_price()
        _mode = st.radio("보기", ["약가 조회", "약가 산정"], horizontal=True,
                         key="price_mode", label_visibility="collapsed")
        if _mode == "약가 산정":
            _price_calc_page()
            ss.release_memory()      # 무거운 화면 — 쓰고 버린 자리를 돌려준다
            st.stop()
        # ── 검색 기반 약가 변동 그래프 (성분→오리지널 / 제품→해당 제품) ──
        st.markdown("**🔎 약가 변동 그래프** · 성분=동일성분 오리지널(신약) 기준, "
                    "제품=고른 품목 하나(함량·포장까지 구분)")
        _pc = st.columns([1, 1, 2])
        _pbasis = _pc[0].radio("기준", ["성분", "제품"], key="ptrend_basis", horizontal=True)
        _byspec = _pbasis == "제품"
        _popts = _price_name_opts() if _byspec else _approval_opts("주성분")
        _pkw = _pc[1].text_input(f"{_pbasis} 검색어", key="ptrend_kw", placeholder="일부만 입력")
        _pfo = _filter_opts(_popts, _pkw)
        _psel = _pc[2].selectbox(
            f"{'약가' if _byspec else '허가'} {_pbasis} 선택 ({len(_popts):,}개 중 검색)",
            ["(선택하세요)"] + _pfo, key="ptrend_sel")
        if _psel and _psel != "(선택하세요)" and "급여구분" in pr.columns:
            _codes, _title = _price_trend_codes(pr, _pbasis, _psel)
            # 같은 성분·함량·제형 묶음(제제코드)이 있으면 경쟁 품목 수를 같이 알려준다
            if _byspec and "제제코드" in pr.columns:
                _gc = pr.loc[pr["제품명"].astype(str) == _psel, "제제코드"].astype(str)
                _gc = _gc[_gc.str.len() > 0]
                if not _gc.empty:
                    _peer = pr.loc[(pr["제제코드"].astype(str) == _gc.iloc[0]) &
                                   (pr["급여구분"] == "급여"), "제품코드"].nunique()
                    st.caption(f"동일제제 `{_gc.iloc[0]}` · 같은 성분·함량·제형 급여 품목 {_peer}개")
            _h = pr[(pr["제품코드"].isin(_codes)) & (pr["급여구분"] == "급여")].sort_values("적용일자")
            if _h.empty:
                st.info("해당 급여 약가 이력이 없습니다.")
            else:
                _show = list(_h.groupby("제품명").size().sort_values(ascending=False).index[:30])
                _fig = go.Figure()
                for _nm, _g in _h[_h["제품명"].isin(_show)].groupby("제품명"):
                    _fig.add_trace(go.Scatter(x=_g["적용일자"], y=_g["금액"], mode="lines+markers",
                                              name=str(_nm)[:26], line_shape="hv"))
                _fig.update_layout(height=430, title=_title, yaxis=_WON_AXIS,
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
                        figo.update_layout(height=360, title="오리지널(신약) 상한금액 추이", yaxis=_WON_AXIS, legend=dict(orientation="h", y=-0.3))
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
                st.dataframe(ym.head(TABLE_CAP), use_container_width=True, height=380)
                if len(ym) > TABLE_CAP:
                    st.caption(f"표에는 상위 {TABLE_CAP:,}건만 표시했습니다 (전체 {len(ym):,}건). 아래 CSV로 전체를 받으세요.")
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
                same = dmf[ss.ing_core_series(dmf[ic]).isin(kor)]
                m = pd.concat([m, same]).drop_duplicates()
            # 식약처 최신 목록이면 취하·취소·폐업된 DMF는 빼고 본다
            _stc = ss.dmf_status_col(m) if hasattr(ss, "dmf_status_col") else None
            if _stc:
                m = m[m[_stc].astype(str).str.strip().eq("정상")]
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

    # AI 탭은 허가·특허·약가·임상·DMF·재심사·매출을 한 질문에 모두 본다.
    # 보통 읽기(load_approval 등)는 표 전체를 공유 캐시에 올려 세션 내내 붙잡으므로,
    # 일곱 개가 동시에 올라가면 무료 플랜 메모리(약 1GB)를 넘겨 앱 전체가
    # 강제 종료된다("Oh no" 화면). 그래서 아래 helper 들은
    #   ① 쓰는 열만 파일에서 직접 읽고 ② 캐시에 올리지 않고 ③ 쓰고 나면 바로 놓아준다.
    # 측정: 자료 4개 기준 734MB → 375MB. 화면에 나가는 문구·숫자는 이전과 같다.
    def _ctx_sales(qterm, last_n=1):
        """매출(IQVIA) 요약. last_n=1 이면 전체 연도, 5면 최근 5개년."""
        if not has_sales("IQVIA"):
            return None
        df, yr, cfg = get("IQVIA")
        if not yr:
            return None
        m = sales_mask(df, cfg, qterm)
        if not bool(m.any()):
            return None
        use = list(yr[-last_n:]) if last_n > 1 else list(yr)
        return pick(df, m, use + [cfg["maker"], cfg["ing"]]), yr, cfg

    def _ctx_approval(qterm, cols):
        """허가 요약용 조각. (매칭수, 조각DF) 또는 None."""
        if not HAS_REG:
            return None
        need = ["품목명", "주성분", "주성분(영문)"]
        ap = ss.load_slim("approval", list(dict.fromkeys(need + cols)))
        if ap is None:
            ap = ss.load_approval()          # 열만 읽기가 안 되면 기존 방식
        m = match_mask(ap, need, qterm)
        n = int(m.sum())
        if n == 0:
            return None
        return n, pick(ap, m, cols)

    def _ctx_patent(qterm):
        """특허 요약: (매칭수, 존속등록수, 물질만료, 용도만료, 특허권자) 또는 None.
        예전에는 concat + 중복제거로 표를 두 벌 만들었는데, 조건을 합치면
        복사 없이 같은 결과가 나온다."""
        if not HAS_REG:
            return None
        pt = ss.load_slim("patent", ["품목명", "INGR_ENG_NAME", "INGR_NAME", "PATENT_GB_CODE",
                                     "DOMESTIC_PATENT_STATUS", "DOMESTIC_END_DATE", "PATENTEE",
                                     ss.PATENT_PAGE_COL])
        pt = ss.enrich_patent(pt) if pt is not None else ss.load_patent()
        m = match_mask(pt, ["품목명", "INGR_ENG_NAME", "INGR_NAME"], qterm)
        keys = ss.resolve_keys(qterm)
        if keys and "_key" in pt.columns:
            m = m | pt["_key"].isin(keys)
        n = int(m.sum())
        if n == 0:
            return None
        sub = pick(pt, m, ["PATENT_GB_CODE", "DOMESTIC_PATENT_STATUS", "_exp", "PATENTEE",
                           ss.PATENT_PAGE_COL])
        live = (sub[ss.active_patent_mask(sub["DOMESTIC_PATENT_STATUS"]) & sub["_exp"].notna()
                    & ss.listed_patent_mask(sub)]
                if {"DOMESTIC_PATENT_STATUS", "_exp"}.issubset(sub.columns) else sub.iloc[0:0])
        mat = use = pd.NaT
        if "PATENT_GB_CODE" in live.columns and not live.empty:
            gb = live["PATENT_GB_CODE"].astype(str)
            mat = live.loc[ss.substance_mask(gb), "_exp"].max()
            use = live.loc[ss.gb_mask(gb, "용도"), "_exp"].max()
        pats = (", ".join(sub["PATENTEE"].dropna().astype(str).unique()[:5])
                if "PATENTEE" in sub.columns else "")
        return n, len(live), mat, use, pats

    def _ctx_price(qterm):
        """약가 요약: (급여품목수, 최저, 최고) 또는 None."""
        if not HAS_PRICE:
            return None
        pr = ss.load_slim("price", ["제품명", "주성분명", "제품코드", "급여구분", "적용일자", "금액"])
        if pr is None:
            pr = ss.load_price()
        m = match_mask(pr, ["제품명", "주성분명"], qterm)
        if not bool(m.any()):
            return None
        mp = pick(pr, m, ["제품코드", "급여구분", "적용일자", "금액"])
        if "급여구분" in mp.columns:
            mp = mp[mp["급여구분"] == "급여"]
        if mp.empty:
            return None
        ls = mp.sort_values("적용일자").groupby("제품코드").tail(1)
        if ls.empty:
            return None
        return ls["제품코드"].nunique(), ls["금액"].min(), ls["금액"].max()

    def _ctx_clinical(qterm):
        """임상 요약: (건수, 단계문구) 또는 None."""
        cl = ss.load_slim("clinical", ["제품명", "성분명", "CLINIC_STEP_NM"])
        if cl is None:
            cl = ss.load_clinical()
        m = match_mask(cl, ["제품명", "성분명"], qterm)
        n = int(m.sum())
        if n == 0:
            return None
        steps = ""
        if "CLINIC_STEP_NM" in cl.columns:
            steps = ", ".join(cl.loc[m, "CLINIC_STEP_NM"].dropna().astype(str)
                              .value_counts().head(6).index)
        return n, steps

    def build_ai_context(qterm):
        parts = []
        if not qterm:
            return "(제품/성분 미지정)"
        try:
            got = _ctx_sales(qterm)
            if got:
                hit, yr, cfg = got
                s = hit[yr].sum(); cg = calc_cagr(s.tolist(), yr)
                parts.append(f"[매출·IQVIA] 최신연도({year_of(yr[-1])}) {s.iloc[-1]/1e8:,.1f}억 · CAGR {('%+.1f%%'%cg) if cg is not None else 'N/A'} · 제조사 {hit[cfg['maker']].nunique()}곳 · 성분 {hit[cfg['ing']].nunique()}종")
                del hit, got
                gc.collect()
        except Exception:
            pass
        try:
            got = _ctx_approval(qterm, ["업체명", "신약구분"])
            if got:
                n, am = got
                newd = int((am["신약구분"] == "신약").sum()) if "신약구분" in am.columns else 0
                parts.append(f"[허가] {n:,}건 · 업체 {am['업체명'].nunique()}곳 · 신약(오리지널) {newd}건")
                del am, got
                gc.collect()
        except Exception:
            pass
        try:
            got = _ctx_patent(qterm)
            if got:
                n, nlive, mat, use, _ = got
                parts.append(f"[특허] 매칭 {n}건 · 존속 등록 {nlive}건 · 물질특허 만료 {mat.date() if pd.notna(mat) else '-'} · 용도특허 만료 {use.date() if pd.notna(use) else '-'}")
                gc.collect()
        except Exception:
            pass
        try:
            got = _ctx_price(qterm)
            if got:
                ncode, lo, hi = got
                parts.append(f"[약가] 급여 품목 {ncode}개 · 상한금액 최고 {hi:,.0f}원 · 최저 {lo:,.0f}원")
                gc.collect()
            ny = ss.nego_years(qterm) if hasattr(ss, 'nego_available') and ss.nego_available() else []
            if ny:
                parts.append(f"[공단 약가협상 완료 연도(공식)] {', '.join(ny)}")
        except Exception:
            pass
        try:
            got = _ctx_clinical(qterm)
            if got:
                n, steps = got
                parts.append(f"[임상·동일성분] {n}건 · 단계: {steps or '미상'}")
                gc.collect()
        except Exception:
            pass
        # 재심사(PMS)
        try:
            rm = _pms_match(qterm)
            if rm is not None and not rm.empty:
                show = [c for c in rm.columns if any(k in c.upper() for k in ["REEXAM", "YEAR", "DATE"])][:4]
                parts.append(f"[PMS·재심사] 매칭 {len(rm)}건" + (f" · 예시 {rm.iloc[0][show].to_dict()}" if show else ""))
            del rm
            gc.collect()
        except Exception:
            pass
        # DMF(동일성분)
        try:
            dm = _dmf_match(qterm)
            if dm is not None and not dm.empty and "ENTP_NAME" in dm.columns:
                ents = ", ".join(dm["ENTP_NAME"].dropna().astype(str).unique()[:15])
                parts.append(f"[DMF 등록업체(동일성분)] {len(dm)}건 · {ents}")
            del dm
            gc.collect()
        except Exception:
            pass
        return "\n".join(parts) if parts else "(데이터 매칭 없음)"

    def build_review_context(qterm):
        """제품 검토서 양식용 데이터 수집(항목별)."""
        L = []
        try:
            got = _ctx_approval(qterm, ["업체명", "신약구분", "전문/일반", "주성분", "품목명"])
            if got:
                n, am = got
                gubun = ", ".join(am["전문/일반"].dropna().astype(str).unique()[:3]) if "전문/일반" in am.columns else ""
                ings = ", ".join(am["주성분"].dropna().astype(str).unique()[:6]) if "주성분" in am.columns else ""
                newd = int((am["신약구분"] == "신약").sum()) if "신약구분" in am.columns else 0
                reps = ", ".join(am["품목명"].dropna().astype(str).unique()[:8]) if "품목명" in am.columns else ""
                L.append(f"[허가] 매칭 {n:,}건 · 업체 {am['업체명'].nunique()}곳 · 신약(오리지널) {newd}건")
                L.append(f"  허가분류(전문/일반): {gubun or '미상'}")
                L.append(f"  주성분: {ings or '미상'}")
                L.append(f"  대표 품목명: {reps}")
                del am, got
                gc.collect()
        except Exception:
            pass
        try:
            got = _ctx_price(qterm)
            if got:
                ncode, lo, hi = got
                L.append(f"[약가] 급여 품목 {ncode}개 · 상한금액 {lo:,.0f}~{hi:,.0f}원")
                gc.collect()
        except Exception:
            pass
        try:
            rm = _pms_match(qterm)
            if rm is not None and not rm.empty:
                show = [c for c in rm.columns if any(k in c.upper() for k in ["REEXAM", "YEAR", "DATE", "CODE"])][:5]
                L.append(f"[PMS·재심사] 매칭 {len(rm)}건 · 예시: {rm.iloc[0][show].to_dict() if show else '컬럼확인필요'}")
            del rm
            gc.collect()
        except Exception:
            pass
        try:
            got = _ctx_patent(qterm, extra_cols=("PATENTEE",))
            if got:
                n, nlive, mat, use, pats = got
                L.append(f"[특허] 매칭 {n}건 · 존속 등록 {nlive}건 · 물질특허 만료 {mat.date() if pd.notna(mat) else '-'} · 용도특허 만료 {use.date() if pd.notna(use) else '-'}")
                if pats:
                    L.append(f"  특허권자: {pats}")
                gc.collect()
        except Exception:
            pass
        try:
            got = _ctx_clinical(qterm)
            if got:
                n, steps = got
                L.append(f"[임상·동일성분] {n}건 · 단계: {steps or '미상'}")
                gc.collect()
        except Exception:
            pass
        try:
            got = _ctx_sales(qterm, last_n=5)
            if got:
                hit, yr, cfg = got
                last5 = yr[-5:]; s = hit[last5].sum()
                mm = " · ".join(f"{year_of(y)}년 {s[y] / 1e6:,.0f}" for y in last5)
                L.append(f"[매출·IQVIA 최근5개년(백만원)] {mm}")
                del hit, got
                gc.collect()
        except Exception:
            pass
        try:
            dm = _dmf_match(qterm)
            if dm is not None and not dm.empty and "ENTP_NAME" in dm.columns:
                ents = ", ".join(dm["ENTP_NAME"].dropna().astype(str).unique()[:20])
                L.append(f"[DMF 등록업체(동일성분)] {len(dm)}건 · {ents}")
            del dm
            gc.collect()
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
            # 삼항식을 문장 하나로 두면 그 값(DeltaGenerator)이 남아,
            # Streamlit 이 그걸 화면에 출력한다(파이썬 설명문이 쏟아진다).
            if ok:
                st.markdown(ans)
            if gemini_ai.LAST_MODEL and gemini_ai.LAST_MODEL != gemini_ai.model_name():
                st.caption(f"설정된 모델({gemini_ai.model_name()})이 붐벼서 "
                           f"**{gemini_ai.LAST_MODEL}** 로 답했습니다.")
            else:
                st.error(ans)
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
                    if gemini_ai.LAST_MODEL and gemini_ai.LAST_MODEL != gemini_ai.model_name():
                        st.caption(f"설정된 모델({gemini_ai.model_name()})이 붐벼서 "
                                   f"**{gemini_ai.LAST_MODEL}** 로 답했습니다.")
                else:
                    st.error(ans)

# ══════════════════════════ 상태 ══════════════════════════
if PAGE == "status":
    st.subheader("데이터 연결 상태")
    st.caption(f"코드 버전: {BUILD}")
    if _DATA_ERR is not None:
        st.error(f"마지막 자동 갱신이 실패했습니다 — {type(_DATA_ERR).__name__}: {_DATA_ERR}")

    # 자동 갱신은 하루 한 번(한국시간 새벽 5시 기준). 그 사이에는 저장된 자료를 그대로 쓴다.
    _run, _done, _err = bootstrap.background_status()
    _c1, _c2 = st.columns([1, 3])
    with _c1:
        if st.button("🔄 지금 새로고침", disabled=_run, type="primary",
                     help="구글 시트·드라이브에서 자료를 다시 받아옵니다. 화면은 멈추지 않고 "
                          "뒤에서 진행되며, 끝나면 새로고침했을 때 반영됩니다."):
            bootstrap.refresh_now(include_sales=True)
            st.rerun()
    with _c2:
        if _run:
            st.info("자료를 새로 받는 중입니다… 끝나면 화면을 새로고침(F5)하면 반영됩니다.")
        elif _err:
            st.warning(f"마지막 새로고침이 실패했습니다 — {_err}")
        elif _done:
            st.success("새로고침이 끝났습니다. 화면을 새로고침(F5)하면 새 자료가 보입니다.")
        else:
            st.caption(f"자동 갱신: 매일 한국시간 새벽 {bootstrap.REFRESH_HOUR_KST}시 기준 "
                       "(그 전까지는 저장된 자료를 그대로 씁니다) · 매출은 주 1회")

    import datetime as _dt

    def _rows(path):
        """행 수를 파일 메타데이터로만 센다(대용량 매출 파일은 읽지 않음)."""
        try:
            if path.suffix == ".parquet":
                import pyarrow.parquet as _pq
                return f"{_pq.ParquetFile(path).metadata.num_rows:,}행"
            return f"{path.stat().st_size / 1e6:,.1f} MB"
        except Exception:
            return "—"

    def _age(path):
        try:
            h = (_dt.datetime.now() - _dt.datetime.fromtimestamp(path.stat().st_mtime)).total_seconds() / 3600
            if h < 1:
                return f"{int(h*60)}분 전", h
            if h < 48:
                return f"{h:.0f}시간 전", h
            return f"{h/24:.0f}일 전", h
        except Exception:
            return "—", 1e9

    _SD, _SV = Path(__file__).parent / "scout_data", Path(__file__).parent / "saved_data"
    items = [
        ("매출 · IQVIA", _SV / "iqvia.pkl", 24 * 7, "드라이브 엑셀 → iqvia.pkl"),
        ("매출 · UBIST", _SV / "ubist.pkl", 24 * 7, "드라이브 엑셀 → ubist.pkl"),
        ("허가현황", _SD / "approval.parquet", 24, "구글시트 → approval.parquet"),
        ("특허현황", _SD / "patent.parquet", 24, "구글시트 → patent.parquet"),
        ("임상시험", _SD / "clinical.parquet", 24, "구글시트 → clinical.parquet"),
        ("약가(상한금액 이력)", _SD / "price.parquet", 24, "드라이브 엑셀+HIRA 폴더 → price.parquet"),
        ("공단 약가협상", _SD / "nego.parquet", 24, "건보공단 공개자료 → nego.parquet"),
    ]
    rows, n_missing, n_stale = [], 0, 0
    for name, path, maxh, note in items:
        if not path.exists():
            # 자료는 뒤에서 자동으로 받아온다 → 아직 안 받은 건 '곧 받아옴'이지 오류가 아니다
            _hint = " · 받아오는 중" if _run else " · 곧 자동으로 받아옵니다"
            rows.append((name, "⏳ 아직 안 받음", "—", "—", note + _hint))
            continue
        txt, h = _age(path)
        if h > maxh:
            rows.append((name, "⚠️ 오래됨", _rows(path), txt, note)); n_stale += 1
        else:
            rows.append((name, "✅ 연결", _rows(path), txt, note))

    # 구글시트에서 바로 읽는 항목(파일 캐시 없음)
    for name, fn, note in [("재심사(PMS)", ss.load_rejdge, "구글시트 '재심사' 탭 (실시간 조회)"),
                           ("DMF", ss.load_dmf, "식약처 DMF 공고 목록 (매일 05시 로봇 수집 · 실패 시 구글시트+엑셀)")]:
        try:
            d = fn()
            ok = d is not None and not d.empty
            rows.append((name, "✅ 연결" if ok else "❌ 미연결",
                         f"{len(d):,}행" if ok else "0행", "조회 시점", note))
            if not ok:
                n_missing += 1
        except Exception as _e:
            rows.append((name, "❌ 미연결", "—", "—", f"{note} · {type(_e).__name__}")); n_missing += 1

    if n_missing == 0 and n_stale == 0:
        st.success("모든 자료가 정상 연결돼 있습니다.")
    else:
        msg = []
        if n_missing: msg.append(f"미연결 {n_missing}건")
        if n_stale: msg.append(f"갱신 지연 {n_stale}건")
        st.warning(" · ".join(msg) + " — 아래 표에서 확인하세요.")

    st.dataframe(pd.DataFrame(rows, columns=["항목", "상태", "규모", "마지막 갱신", "경로"]),
                 hide_index=True, use_container_width=True)
    st.caption(f"‘마지막 갱신’은 서버에 저장된 자료 파일 기준입니다. 자동 갱신은 매일 한국시간 "
               f"새벽 {bootstrap.REFRESH_HOUR_KST}시 기준으로 한 번(매출은 주 1회)이며, 자료가 이미 있으면 "
               "화면을 멈추지 않고 뒤에서 받아옵니다. ⚠️ 표시는 그 주기를 넘겼다는 뜻이며, 원본 시트 공유 "
               "설정이나 서비스 계정 키를 확인해 주세요. · 성분 조인 키 = 영문 성분명 첫 단어.")

# ── 화면 하나를 다 그린 뒤, 쓰고 버린 메모리를 OS 에 돌려준다 ──────────────
# 캐시에 올린 자료는 그대로 두고 '이미 버려진' 자리만 반납하므로 자료를 다시
# 읽지 않는다. 점유가 눈에 띄게 늘었을 때만 돌아간다(release_memory 참고).
ss.release_memory()
