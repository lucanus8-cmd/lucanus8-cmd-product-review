"""임상 분석 (v2) — 지금 사이트와 같은 화면·같은 규칙. 임상 표는 scout_sources 공유 로더(한 벌만)."""
import re

import pandas as pd
import plotly.express as px
import streamlit as st

import scout_sources as ss
from core import data
from core.ui import contains, isin, msel, show_df
from views.clinical_calc import _be_page, _cost_page, _meta_page

ss._ensure = lambda *a, **k: None
data.ensure("clinical")
data.ensure("approval")

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
                              "💰 1상 비용 어림", "📐 간이 메타분석"],
                     horizontal=True, key="clin_view", label_visibility="collapsed")

    if _view.endswith("피험자 수"):
        _be_page()
        ss.release_memory()
        st.stop()

    if _view.endswith("비용 어림"):
        _cost_page()
        ss.release_memory()
        st.stop()

    if _view.endswith("메타분석"):      # '분석'으로 끝나므로 아래 '분석' 보기보다 먼저 본다
        _meta_page()
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

