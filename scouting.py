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

def contains(df, cols, q):
    m = pd.Series(False, index=df.index)
    for c in cols:
        if c in df.columns:
            m |= df[c].astype(str).str.contains(q, case=False, na=False)
    return df[m]

def sales_search(df, cfg, q):
    """제품명/성분명 + (한글↔영문 성분키) 매칭."""
    m = df[cfg["prod"]].astype(str).str.contains(q, case=False, na=False) | \
        df[cfg["ing"]].astype(str).str.contains(q, case=False, na=False)
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

st.title("🔎 제품 검토")
st.caption("매출: IQVIA·UBIST · 허가/특허/임상: 식약처 · 약가: 심평원 + NHIS 협상완료(공식)")

t_search, t_sugg, t_sales, t_patent, t_clinical, t_price, t_ai, t_status = st.tabs(
    ["🔍 제품 종합분석", "🎯 제품 제안", "💰 매출 분석", "⚖️ 특허 분석", "🧪 임상 분석", "💊 약가 분석", "🤖 AI 분석", "ℹ️ 상태"])

# ══════════════════════════ 제품 종합분석 ══════════════════════════
with t_search:
    c1, c2 = st.columns([3, 1])
    q = c1.text_input("제품명 또는 성분 검색", placeholder="예: 미라베그론 / mirabegron / 베타미가")
    src1 = c2.radio("매출 자료원", ["IQVIA", "UBIST"], horizontal=True, key="s1")
    if q:
        df, yr, cfg = get(src1)
        hit = sales_search(df, cfg, q)
        if not hit.empty:
            s = hit[yr].sum(); cg = calc_cagr(s.tolist(), yr)
            k = st.columns(4)
            k[0].metric(f"최신연도({year_of(yr[-1])}) 매출", f"{s.iloc[-1]/1e8:,.1f} 억")
            k[1].metric("매출 CAGR", f"{cg:+.1f}%" if cg is not None else "—")
            k[2].metric("매출상 제조사", f"{hit[cfg['maker']].nunique()} 곳")
            k[3].metric("성분 수", f"{hit[cfg['ing']].nunique()} 종")
        else:
            st.info(f"{src1} 매출 매칭 없음 (허가/특허/임상/약가는 아래 확인)")
        tabs = st.tabs(["📋 허가", "⚖️ 특허", "🧪 임상", "💊 약가·이벤트", "🔁 재심사(PMS)", "🧬 DMF(동일성분)"])
        with tabs[0]:
            if HAS_REG:
                ap = ss.load_approval(); m = contains(ap, ["품목명", "주성분", "주성분(영문)"], q)
                st.caption(f"허가 {len(m):,}건 · 업체 {m['업체명'].nunique()}곳 · 신약 {int((m['신약구분']=='신약').sum())}건")
                cc = [c for c in ["품목명", "업체명", "허가일자", "전문/일반", "주성분", "신약구분", "상태", "보험코드(EDI)"] if c in m.columns]
                st.dataframe(m[cc].sort_values("허가일자", ascending=False), use_container_width=True, height=300, hide_index=True)
            else: st.info("허가 데이터 미연결")
        with tabs[1]:
            if HAS_REG:
                pt = ss.load_patent(); m = contains(pt, ["품목명", "INGR_ENG_NAME", "INGR_NAME"], q).copy()
                m["만료D(년)"] = ((m["_exp"] - TODAY).dt.days / 365.25).round(1)
                st.caption(f"특허 {len(m):,}건 · 등록 {int(m['DOMESTIC_PATENT_STATUS'].str.contains('등록', na=False).sum())}건")
                cmap = {"품목명": "품목명", "PATENT_GB_CODE": "유형", "PATENTEE": "특허권자", "DOMESTIC_PATENT_NO": "특허번호",
                        "DOMESTIC_PATENT_STATUS": "상태", "DOMESTIC_END_DATE": "만료일", "만료D(년)": "만료D(년)"}
                st.dataframe(m.rename(columns=cmap)[list(cmap.values())].sort_values("만료일", ascending=False),
                             use_container_width=True, height=300, hide_index=True)
            else: st.info("특허 데이터 미연결")
        with tabs[2]:
            if HAS_REG:
                cl = ss.load_clinical(); m = contains(cl, ["제품명", "성분명"], q)
                st.caption(f"임상 {len(m):,}건 · 생동(제네릭 개발) {int(m['CLINIC_STEP_NM'].str.contains('생동', na=False).sum())}건")
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
                pr = ss.load_price(); mp = contains(pr, ["제품명", "주성분명"], q).copy()
                if mp.empty:
                    st.info("약가 매칭 없음")
                else:
                    keys = {k for k in mp["_key"].unique() if k}
                    st.caption(f"약가 매칭 {len(mp):,}건 · 제품 {mp['제품코드'].nunique()}개 · 성분키 {len(keys)}개")
                    counts = mp[mp["급여구분"] == "급여"]["제품명"].value_counts()
                    if counts.empty:
                        counts = mp["제품명"].value_counts()
                    opts = list(counts.index)
                    # 오리지널(신약) 우선 기본 선택, 없으면 최초 등재 품목
                    default_idx = 0
                    rkeys = ss.resolve_keys(q)
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
                m = contains(rj, name_cols, q) if name_cols else rj.iloc[0:0]
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
                kor_ings = set()
                if HAS_REG:
                    ap = ss.load_approval()
                    hitap = contains(ap, ["품목명", "주성분", "주성분(영문)"], q)
                    for v in hitap["주성분"].dropna().astype(str):
                        for t in re.split(r"[,/()]|및|\+|\s", v):
                            t = t.strip()
                            if len(t) >= 2:
                                kor_ings.add(t)
                name_cols = [c for c in [ing_col, "ENTP_NAME"] if c]
                m = contains(dmf, name_cols, q) if name_cols else dmf.iloc[0:0]
                if ing_col and kor_ings:
                    same = dmf[dmf[ing_col].astype(str).apply(
                        lambda s: any(k and (k in s or s in k) for k in kor_ings))]
                    m = pd.concat([m, same]).drop_duplicates()
                st.caption(f"동일성분 DMF {len(m):,}건 (전체 {len(dmf):,}건)"
                           + (f" · 매칭 성분: {', '.join(sorted(kor_ings)[:6])}" if kor_ings else ""))
                if m.empty:
                    st.info("동일성분 DMF 매칭 없음")
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
with t_sugg:
    st.subheader("🎯 제품 제안 — 조건 필터")
    a, b = st.columns(2)
    src2 = a.radio("매출 자료원", ["IQVIA", "UBIST"], horizontal=True, key="s2")
    unit = b.radio("분석 단위", ["성분별", "제품별"], horizontal=True, key="u2")
    df, yr, cfg = get(src2)
    m = sales_agg(df, yr, cfg["ing"] if unit == "성분별" else cfg["prod"], cfg["maker"])
    name = unit.replace("별", "")
    m = m.rename(columns={m.columns[0]: name})
    m["_key"] = m[name].map(ss.ing_key)
    if HAS_REG:
        m = m.merge(ss.patent_by_key(), on="_key", how="left")

    st.markdown("**필터 조건**")
    f1, f2, f3 = st.columns(3)
    min_size = f1.number_input("① 최소 시장규모(억, 최신연도)", value=50, step=10, min_value=0)
    min_cagr = f2.number_input("② 최소 성장 CAGR(%)", value=0.0, step=5.0)
    with f3:
        ptypes = st.multiselect("③ 특허 만료 기준 유형", ["물질", "용도"], default=["물질"] if HAS_REG else [])
        cy = int(TODAY.year)
        sel_year = st.selectbox("이 연도까지 특허 만료", list(range(cy, cy + 16)), index=5,
                                help="선택한 유형 특허가 이 연도까지 만료(또는 없음)인 후보만")

    res = m[(m["시장규모"] >= min_size * 1e8) & (m["CAGR"].fillna(-1e9) >= min_cagr)].copy()
    if HAS_REG and ptypes:
        def clear_by(row):
            for t in ptypes:
                exp = row.get(f"특허만료_{t}")
                if pd.notna(exp) and pd.Timestamp(exp).year > sel_year:
                    return False
            return True
        res = res[res.apply(clear_by, axis=1)]

    res = res.sort_values("시장규모", ascending=False)
    res["시장규모(억)"] = (res["시장규모"] / 1e8).round(1)
    res["CAGR(%)"] = res["CAGR"].round(1)
    if HAS_REG:
        res["물질특허 만료"] = pd.to_datetime(res.get("특허만료_물질")).dt.date.astype("string")
        res["용도특허 만료"] = pd.to_datetime(res.get("특허만료_용도")).dt.date.astype("string")
    cols = [name, "시장규모(억)", "CAGR(%)"] + (["물질특허 만료", "용도특허 만료"] if HAS_REG else [])
    st.markdown(f"#### ✅ 조건 충족 후보 {len(res):,}개")
    st.dataframe(res[cols].head(200), use_container_width=True, height=430, hide_index=True)
    st.download_button("⬇️ CSV", res[cols].to_csv(index=False).encode("utf-8-sig"), file_name=f"제품제안_{src2}_{unit}.csv")
    st.caption("가중치 점수 없이, 큰 시장·고성장·특허만료 조건을 직접 필터링합니다.")

# ══════════════════════════ 매출 분석 (기존 app.py 4개 탭 그대로) ══════════════════════════
with t_sales:
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
with t_patent:
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
        if patee: pt = pt[pt["PATENTEE"].astype(str).str.contains(patee, case=False, na=False)]
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
                d0 = min(max(lo, TODAY.year - 1), hi)   # 기본 시작값을 [lo,hi]로 클램프
                d1 = max(min(hi, TODAY.year + 5), lo)   # 기본 끝값을 [lo,hi]로 클램프
                if d0 > d1:
                    d0, d1 = lo, hi
                rng = st.slider("만료 연도 범위", lo, hi, (d0, d1), key="p_rng")
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
with t_clinical:
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
            m = m[m[dev_c].astype(str).str.contains(dev_kw, case=False, na=False)]
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
with t_price:
    st.subheader("💊 약가 분석 — 조건별 · 연도별")
    if not HAS_PRICE:
        st.info("약가 데이터 미연결")
    else:
        pr = ss.load_price()
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
            latest = latest[latest["업체명"].astype(str).str.contains(ent, case=False, na=False)]
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
                drop = drop.join(latest.set_index("제품코드")[["제품명", "업체명"]]).reset_index()
                st.dataframe(drop.nsmallest(20, "인하율%")[["제품명", "업체명", "최초가", "최신가", "인하율%"]],
                             use_container_width=True, height=240, hide_index=True)
                deleted = latest[latest["급여구분"] == "삭제"][["제품명", "업체명", "주성분명", "적용일자"]]
                st.dataframe(deleted.sort_values("적용일자", ascending=False).head(30), use_container_width=True, height=200, hide_index=True)

# ══════════════════════════ AI 분석 (Gemini) ══════════════════════════
with t_ai:
    import gemini_ai
    st.subheader("🤖 AI 분석 (Gemini)")

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
            if hasattr(ss, "rejdge_available") and ss.rejdge_available():
                rj = ss.load_rejdge()
                ncols = [c for c in ["ITEM_NAME", "ENTP_NAME"] if c in rj.columns] or [c for c in rj.columns if "NAME" in c.upper()]
                rm = contains(rj, ncols, qterm) if ncols else rj.iloc[0:0]
                if not rm.empty:
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
            if hasattr(ss, "dmf_available") and ss.dmf_available():
                dmf = ss.load_dmf(); ic = ss._dmf_ing_col(dmf)
                dm = contains(dmf, [c for c in [ic, "ENTP_NAME"] if c], qterm) if (ic or "ENTP_NAME" in dmf.columns) else dmf.iloc[0:0]
                if not dm.empty and "ENTP_NAME" in dm.columns:
                    ents = ", ".join(dm["ENTP_NAME"].dropna().astype(str).unique()[:20])
                    L.append(f"[DMF 등록업체(동일성분)] {ents}")
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
with t_status:
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
