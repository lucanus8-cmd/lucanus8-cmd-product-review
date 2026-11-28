"""원료사 동향(DMF) — 제품 제안의 두 번째 보기 (v2). 지금 사이트(scouting.py)와 같은 코드·같은 계산."""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import scout_sources as ss
from core import data
from core.keys import kor2engkey as _kor2engkey

TODAY = pd.Timestamp.today().normalize()
HAS_REG = data.has("approval") and data.has("patent")
HAS_PAT = data.has("patent")


# ══════════════════════════ 원료사 동향(DMF) — 제품 제안 탭의 두 번째 보기 ══════════════════════════
@st.cache_data(ttl=6 * 3600, show_spinner="원료사 동향 계산 중…")
def _dmf_prep():
    import dmf_trend as _dt
    # v2: 허가 표 전체 대신 오리지널사 판정에 쓰는 세 칸만 잠깐 읽는다(계산 뒤 바로 회수)
    _ap = ss.load_slim("approval", ["신약구분", "주성분", "업체명"])
    return _dt.prepare_dmf(ss.load_dmf(), _ap)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _dmf_trend_cached(months, drop_orig):
    import dmf_trend as _dt
    return _dt.trend(_dmf_prep(), TODAY, months, drop_orig)


@st.cache_data(ttl=6 * 3600, show_spinner="임상 자료 정리 중…")
def _dmf_clin_prep():
    import dmf_trend as _dt
    try:
        # v2: 임상 표 전체 대신 계산에 쓰는 칸만 잠깐 읽는다
        cl = ss.load_slim("clinical", ["성분명", "CLINIC_STEP_NM", "TRGT_DISS_NM", "제품명", "CLST_APRV_DT",
                                       "CLNC_TEST_TITLE", "원개발사"])
        return _dt.prepare_clinical(cl)
    except Exception:
        return pd.DataFrame()



@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _dmf_patents():
    """영문 성분키 → 식약처 등재 특허 만료일(물질·염형태·물질기타). 단일제 품목 특허만.
    제품 제안 '성분별'과 같은 기준: 오리지널(신약) 품목이 있으면 그 특허, 없으면 전체."""
    if not HAS_REG:
        return pd.DataFrame()
    try:
        base = ss.patent_by_key(single_only=True)
        orig = ss.patent_by_key(single_only=True, original_only=True)
        _, _, basis = ss.original_items()
    except Exception:
        return pd.DataFrame()
    both = pd.concat([orig[orig["_key"].isin(basis)], base[~base["_key"].isin(basis)]], ignore_index=True)
    both = both[both["_key"].astype(str).str.len() > 0].drop_duplicates("_key")
    keep = ["_key"] + [c for c in ("특허만료_물질", "특허만료_물질형태", "특허만료_물질기타") if c in both.columns]
    return both[keep].set_index("_key")


def _dmf_sales_patent(m, name, unit, yr=()):
    """제품 제안에서 이미 계산한 성분별 매출·특허(m)를 영문 성분키(_key)로 묶는다. 단일제만.
    매출 자료의 성분명은 표기가 DMF(한글)와 달라 이름으로는 안 붙는다. 제품 제안이 특허를
    붙일 때 쓰는 영문 성분키로 잇는다. yr 는 연도별 매출 칸 이름(예: ['2020년', …])."""
    if unit != "성분별" or m is None or m.empty or name not in m.columns or "_key" not in m.columns:
        return pd.DataFrame()
    mm = m[ss.is_single_series(m[name].astype(str)) & (m["_key"].astype(str).str.len() > 0)].copy()
    if mm.empty:
        return pd.DataFrame()
    keep = ["_key", "시장규모", "CAGR"] + [y for y in yr if y in mm.columns]
    return (mm[keep].sort_values("시장규모", ascending=False)
            .drop_duplicates("_key").set_index("_key"))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _dmf_key_map():
    """DMF 성분 코어(한글) → 영문 성분키 표. 허가·특허 자료로 만든 대응표를 쓴다. 한 번 만들어 둔다."""
    try:
        c2k, _ = _kor2engkey()
    except Exception:
        c2k = {}
    x = _dmf_prep()
    cores = sorted(set(x["성분"])) if x is not None and not x.empty else []
    items = [(kk, v) for kk, v in c2k.items() if len(kk) >= 3]
    out = {}
    for c in cores:
        k = c2k.get(c) or c2k.get(ss.ing_core(c))
        if not k and len(c) >= 3:            # '데스벤라팍신' ↔ '데스벤라팍신벤조산' 처럼 염 이름 차이
            k = next((v for kk, v in items if kk.startswith(c) or c.startswith(kk)), "")
        if k:
            out[c] = k
    return out


def _dmf_keys(cores):
    mp = _dmf_key_map()
    return [mp.get(str(c or ""), "") for c in cores]


def _dmf_atc_filter(t, m, name):
    """원료사 동향 표에 ATC(매출 자료 기준 — '조건 필터'와 같은 값)를 붙이고, 단계별로 좁히는 칸을 보여 준다.
    DMF 성분(한글) → 영문 성분키 → 매출 자료의 같은 성분(단일제, 매출 큰 쪽)의 ATC.
    매출 자료에 없는 성분은 ATC가 비어 있어, ATC를 고르면 빠진다.
    반환: (좁힌 표, 표에 보여 줄 가장 자세한 ATC 칸 이름 또는 None). 문제가 생기면 표를 그대로 돌려준다."""
    try:
        if m is None or m.empty or "_key" not in m.columns or name not in m.columns or t.empty:
            return t, None
        lv = [c for c in m.columns if re.match(r"^ATC\s*\d", str(c))]
        if not lv and "ATC" in m.columns:
            lv = ["ATC"]
        if not lv:
            return t, None
        mm = m[ss.is_single_series(m[name].astype(str)) & (m["_key"].astype(str).str.len() > 0)]
        if "시장규모" in mm.columns:
            mm = mm.sort_values("시장규모", ascending=False)
        mm = mm.drop_duplicates("_key").set_index("_key")
        keys = pd.Series(_dmf_keys(t["성분"]), index=t.index).astype(str)
        for c in lv:
            t[c] = keys.map(mm[c].astype(str).str.strip()).fillna("").replace({"nan": "", "None": ""})
        # 표에는 값이 있는 가장 자세한 단계를 보여 준다
        t["_ATC표시"] = ""
        for c in lv:
            t["_ATC표시"] = t[c].where(t[c].astype(str).str.len() > 0, t["_ATC표시"])
        n_none = int((t[lv[0]].astype(str).str.len() == 0).sum())
        st.markdown("**ATC 계열로 좁히기** (매출 자료 기준 · 비우면 전체)")
        cols = st.columns(len(lv))
        pool, picked = t, False
        for i, c in enumerate(lv):
            opts = sorted({str(x).strip() for x in pool[c] if str(x).strip()})
            mt = re.match(r"^ATC\s*(\d)", str(c))
            lab = ("ATC " + mt.group(1) + "단계") if (len(lv) > 1 and mt) else "ATC"
            sel = cols[i].multiselect(lab, opts, default=[], key=f"dmfv_atc_{i}_{abs(hash(tuple(opts))) % 10**8}",
                                      help="비우면 전체" if i == 0 else "위 단계에서 고른 것 안의 항목만 나옵니다")
            sel = [x for x in sel if x in opts]
            if sel:
                picked = True
                pool = pool[pool[c].astype(str).str.strip().isin(sel)]
        if picked:
            st.caption(f"ATC로 좁힌 성분 {len(pool)}개"
                       + (f" · 매출 자료에 없어 ATC가 비어 있는 성분 {n_none}개는 빠짐" if n_none else ""))
        elif n_none:
            st.caption(f"매출 자료에 없어 ATC가 비어 있는 성분 {n_none}개 — ATC를 고르면 빠집니다")
        return pool, "_ATC표시"
    except Exception as e:                      # ATC 문제로 원료사 동향 화면이 멈추지 않게
        if type(e).__name__ in ("RerunException", "StopException"):
            raise
        return t, None


def _dmf_view(m, name, unit, yr=()):
    import dmf_trend as _dt
    st.markdown("#### 🧪 원료사 동향 — DMF가 몰리는 성분")
    x = _dmf_prep()
    if x is None or x.empty:
        st.info("식약처 최신 DMF 목록을 아직 읽지 못했습니다. 잠시 뒤 다시 열어 주세요. "
                "(목록은 매일 새벽 5시에 새로 받습니다)")
        return
    c1, c2, c3, c5 = st.columns([1.7, 1.1, 0.9, 1.6])
    basis = c1.radio("기준", ["원료사 새 등록 증가", "추가 신청 증가"], horizontal=True, key="dmfv_basis")
    months = int(c2.number_input("기간(개월)", value=12, min_value=1, max_value=60, step=1, key="dmfv_months_n",
                                 help="최근 N개월과 그 바로 앞 N개월을 비교합니다."))
    min_n = int(c3.number_input("최소 등록 수", value=3, min_value=1, step=1, key="dmfv_min"))
    c5.markdown("<div style='height:1.9rem'></div>", unsafe_allow_html=True)
    drop_orig = c5.checkbox("오리지널사 본인 등록 빼기", value=True, key="dmfv_drop",
                            help="그 성분 신약을 가진 회사(또는 다국적사 한국법인)가 낸 DMF는 "
                                 "제네릭 원료 신호가 아니라서 뺍니다.")

    t_all, n_orig = _dmf_trend_cached(months, drop_orig)
    if t_all is None or t_all.empty:
        st.info("계산할 DMF가 없습니다.")
        return
    if basis.startswith("원료사"):
        t = t_all[(t_all["원료사_최근"] >= min_n) & (t_all["원료사_증감"] > 0)]
        t = t.sort_values(["원료사_증감", "새제조소", "원료사_최근"], ascending=False)
    else:
        t = t_all[(t_all["추가_최근"] >= min_n) & (t_all["추가_증감"] > 0)]
        t = t.sort_values(["추가_증감", "추가_최근"], ascending=False)
    t, _atc_show = _dmf_atc_filter(t.copy(), m, name)
    t = t.head(80).copy()

    # 임상: 성분 이름이 적힌 최근 생동·1상, 코드명 의심
    cx = _dmf_clin_prep()
    has_clin = cx is not None and not cx.empty
    named, sus, kw = [], [], []
    for r in t.itertuples():
        if not has_clin:
            named.append(None); sus.append(None); kw.append(""); continue
        terms, _n = _dt.ingredient_terms(cx, r.성분)
        since = (r.첫등록_최근 if pd.notna(r.첫등록_최근) else TODAY - pd.DateOffset(months=months)) \
            - pd.DateOffset(months=6)
        named.append(_dt.named_recent(cx, r.성분, TODAY))
        sus.append(len(_dt.suspects(cx, terms, since)) if terms else 0)
        kw.append(", ".join(terms))
    t["성분생동1상"] = named
    t["코드명의심"] = sus
    t["질환 핵심어"] = kw

    # 매출·특허(성분별일 때만)
    sp = _dmf_sales_patent(m, name, unit, yr)
    pt = _dmf_patents()
    t["_key"] = _dmf_keys(t["성분"])
    if not sp.empty:
        t = t.join(sp, on="_key")
    if not pt.empty:
        t = t.join(pt, on="_key")
        t["물질만료"] = pd.to_datetime(t.get("특허만료_물질"), errors="coerce")
        if "특허만료_물질기타" in t.columns:
            t["물질만료"] = t["물질만료"].fillna(pd.to_datetime(t["특허만료_물질기타"], errors="coerce"))

    _meta = ss.dmf_meta() or {}
    _rec_tot = int(t_all["원료사_최근"].sum())
    _prev_tot = int(t_all["원료사_그전"].sum())
    st.caption("식약처 DMF 공고 기준"
               + (f" · {_meta.get('fetched_at')} 받음" if _meta.get("fetched_at") else "")
               + f" · 최근 {months}개월 원료사 새 등록 {_rec_tot:,}건 (그 전 {months}개월 {_prev_tot:,}건)"
               + f" · 조건 맞는 성분 {len(t):,}개"
               + (f" · 오리지널사 본인 등록 {n_orig}건 뺌" if drop_orig and n_orig else ""))
    if t.empty:
        st.info("조건에 맞는 성분이 없습니다. 최소 등록 수를 낮추거나 기간·기준을 바꿔 보세요.")
        return

    view = pd.DataFrame({
        "성분": t["성분"],
        f"원료사 {months}개월 (그 전)": [f"{a} ({b})" for a, b in zip(t["원료사_최근"], t["원료사_그전"])],
        "새 제조소": t["새제조소"],
        "추가 신청 (그 전)": [f"{a} ({b})" for a, b in zip(t["추가_최근"], t["추가_그전"])],
        "성분 확인 생동·1상(24개월)": t["성분생동1상"],
        "코드명 의심": t["코드명의심"],
    })
    if _atc_show and _atc_show in t.columns:
        view.insert(1, "ATC", t[_atc_show].astype(str).values)
    _nokey = t["_key"].astype(str).str.len() == 0

    def _dstr(c):
        d = (pd.to_datetime(t[c], errors="coerce").dt.strftime("%Y-%m-%d")
             if c in t.columns else pd.Series(pd.NA, index=t.index, dtype="object"))
        d = d.astype("object").where(d.notna(), "등재특허 없음")
        return d.where(~_nokey, "성분키 못 찾음")
    if not pt.empty:
        view["물질특허 만료"] = _dstr("특허만료_물질")
        view["염·형태특허 만료"] = _dstr("특허만료_물질형태")
    if not sp.empty:
        _ys = [y for y in yr if y in t.columns]
        _ylab = str(_ys[-1]).replace("년", "") if _ys else "최근"
        view[f"매출(억, {_ylab})"] = (pd.to_numeric(t.get("시장규모"), errors="coerce") / 1e8).round(1)
        if len(_ys) >= 2:
            _a = pd.to_numeric(t[_ys[-1]], errors="coerce")
            _b = pd.to_numeric(t[_ys[-2]], errors="coerce")
            view["전년 대비(%)"] = ((_a / _b.where(_b > 0) - 1) * 100).round(1)
        view["CAGR(%)"] = pd.to_numeric(t.get("CAGR"), errors="coerce").round(1)
    view["질환 핵심어"] = t["질환 핵심어"]
    st.dataframe(view, use_container_width=True, hide_index=True, height=min(460, 38 + 35 * len(view)))
    st.download_button("⬇️ CSV", view.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"원료사동향_{months}개월.csv", key="dmfv_csv")
    st.caption("원료사 수는 제조소 기준(신청인은 수입 상사가 많음) · 추가 신청 = 기존 원료에 신청인만 붙은 것"
               "(등록번호 끝 (1),(A)) · 코드명 의심 = 같은 질환의 코드명 생동·1상 수(첫 원료사 등록 6개월 전부터)"
               + ("" if not sp.empty else " · 매출·특허 칸은 분석 단위 ‘성분별’에서 채워집니다")
               + (" · 임상 자료를 아직 못 읽어 임상 칸이 비어 있습니다" if not has_clin else ""))

    # ── 성분 자세히 보기 ──
    st.markdown("#### 🔎 성분 자세히 보기")
    opts = t["성분"].tolist()
    pick = st.selectbox("성분", opts, index=0, key=f"dmfv_pick_{abs(hash(tuple(opts))) % 10**8}")
    row = t[t["성분"] == pick].iloc[0]
    k = st.columns(6)
    k[0].metric(f"원료사 ({months}개월)", f"{int(row['원료사_최근'])}", f"{int(row['원료사_증감']):+d}")
    k[1].metric("새 제조소", f"{int(row['새제조소'])}")
    k[2].metric("성분 확인 생동·1상", "—" if row["성분생동1상"] is None else f"{int(row['성분생동1상'])}")
    k[3].metric("코드명 의심", "—" if row["코드명의심"] is None else f"{int(row['코드명의심'])}")
    _exp = row.get("물질만료") if "물질만료" in row.index else None
    k[4].metric("물질특허 만료", pd.Timestamp(_exp).date().isoformat() if _exp is not None and pd.notna(_exp) else "—")
    _ys = [y for y in yr if y in row.index]
    _sv = pd.to_numeric(pd.Series([row[y] for y in _ys], index=_ys), errors="coerce") if _ys else pd.Series(dtype=float)
    if len(_sv) and pd.notna(_sv.iloc[-1]):
        _d1 = (f"{(_sv.iloc[-1] / _sv.iloc[-2] - 1) * 100:+.1f}% 전년 대비"
               if len(_sv) >= 2 and pd.notna(_sv.iloc[-2]) and _sv.iloc[-2] > 0 else None)
        k[5].metric(f"매출(억, {str(_ys[-1]).replace('년', '')})", f"{_sv.iloc[-1] / 1e8:,.1f}", _d1)
    else:
        k[5].metric("매출(억)", "—")

    g1, g2 = st.columns([1.25, 1])
    _span = max(24, 2 * months)          # 그래프·DMF 목록은 비교 구간 전체(최소 24개월)를 보여 준다
    since_q = (TODAY - pd.DateOffset(months=_span)).to_period("Q").start_time
    q = _dt.quarterly(x, pick, since_q)
    qs = pd.period_range(since_q, TODAY, freq="Q").astype(str).tolist()
    q = q.set_index("분기").reindex(qs, fill_value=0).reset_index().rename(columns={"index": "분기"})
    fig = go.Figure()
    fig.add_bar(x=q["분기"], y=q["원료사"], name="원료사 새 등록", marker_color="#2f6f8f")
    fig.add_bar(x=q["분기"], y=q["추가"], name="추가 신청", marker_color="#8fbcd1")
    if has_clin:
        _cs = cx[(cx["성분"] == pick) & (cx["생동"] | cx["1상"]) & (cx["승인일"] >= since_q)]
        if not _cs.empty:
            _cq = _cs["승인일"].dt.to_period("Q").astype(str).value_counts()
            fig.add_scatter(x=_cq.index, y=[0.3] * len(_cq), mode="markers+text", name="성분 확인 생동·1상",
                            marker=dict(color="#7a5aa6", size=12, symbol="diamond"),
                            text=[f"{v}건" for v in _cq.values], textposition="top center")
    fig.update_layout(barmode="stack", height=320, margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=1.12), yaxis_title="건수",
                      xaxis=dict(type="category", categoryorder="array", categoryarray=qs))
    g1.markdown("**흐름** — 분기별 원료사 DMF와 생동·1상")
    g1.plotly_chart(fig, use_container_width=True, key="dmfv_chart")
    if len(_sv) and _sv.notna().any():
        _sf = go.Figure(go.Bar(x=[str(y).replace("년", "") for y in _sv.index], y=(_sv / 1e8).round(1),
                               marker_color="#3f7f4f", text=(_sv / 1e8).round(1), textposition="outside"))
        _sf.update_layout(height=230, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="억",
                          xaxis=dict(type="category"))
        g1.markdown("**연도별 매출** (단일제, 선택한 매출 자료원)")
        g1.plotly_chart(_sf, use_container_width=True, key="dmfv_sales")
    else:
        g1.caption("이 성분의 단일제 매출이 매출 자료에 없습니다.")

    _d = x[(x["성분"] == pick) & (x["등록일"] >= TODAY - pd.DateOffset(months=_span))].sort_values("등록일", ascending=False)
    g2.markdown(f"**원료사 DMF** (최근 {_span}개월)")
    if _d.empty:
        g2.info(f"최근 {_span}개월 DMF가 없습니다.")
    else:
        g2.dataframe(pd.DataFrame({
            "최초등록일": _d["등록일"].dt.date.astype("string"),
            "신청인": _d["신청인"], "제조소": _d["제조소"], "국가": _d["제조국"],
            "구분": ["오리지널사" if o else ("추가 신청" if a else "원료사") for o, a in zip(_d["오리지널사"], _d["추가"])],
            "상태": _d["상태"],
            "연계심사": _d["연계심사문서번호"].str.strip().ne("").map({True: "연계심사", False: ""}),
        }), use_container_width=True, hide_index=True, height=300)

    st.markdown("##### 🧬 코드명 생동·1상 의심 목록")
    if not has_clin:
        st.info("임상 자료를 아직 읽지 못했습니다.")
        return
    terms, n_ev = _dt.ingredient_terms(cx, pick)
    s1, s2 = st.columns([2.2, 1])
    kw_txt = s1.text_input("질환 핵심어 (쉼표로 여러 개)", value=", ".join(terms), key=f"dmfv_kw_{pick}",
                           help="이 성분 이름이 적힌 임상의 대상질환에서 자동으로 뽑습니다. 직접 고쳐도 됩니다.")
    since = (row["첫등록_최근"] if pd.notna(row["첫등록_최근"]) else TODAY - pd.DateOffset(months=months)) \
        - pd.DateOffset(months=6)
    s2.markdown(f"<div style='padding-top:1.9rem'>기간: {pd.Timestamp(since).date()} ~ 오늘</div>",
                unsafe_allow_html=True)
    if n_ev == 0:
        st.caption("이 성분 이름이 적힌 임상이 없어 핵심어를 자동으로 못 뽑았습니다. 직접 넣어 주세요.")
    elif n_ev <= 2:
        st.caption(f"근거 적음: 이 성분 이름이 적힌 임상 {n_ev}건에서 뽑았습니다. 대표 적응증과 다를 수 있어요.")
    else:
        st.caption(f"이 성분 이름이 적힌 임상 {n_ev}건의 대상질환에서 뽑았습니다.")
    canon, raw = _dt.parse_terms(kw_txt)
    sdf = _dt.suspects(cx, canon, since, raw=raw)
    if sdf.empty:
        st.info("이 기간에 질환이 겹치는 코드명 생동·1상이 없습니다.")
        return
    if sdf["개발사"].astype(str).str.strip().eq("").all():
        sdf = sdf.drop(columns=["개발사"])
    st.dataframe(sdf, use_container_width=True, hide_index=True, height=min(420, 38 + 35 * len(sdf)))
    st.caption("같은 질환의 다른 성분일 수도 있어 확정이 아니라 의심으로만 보여 줍니다. "
               "높음 = 핵심어가 둘 이상 겹치고 다른 질환이 없음 · 중간 = 하나 겹침 · 낮음 = 다른 질환도 함께 적힘.")


