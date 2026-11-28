"""제품 종합분석 (v2). 지금 사이트와 같은 화면·같은 규칙.
다른 점: 허가·특허·임상·약가·매출 표를 통째로 올리지 않고, 고른 제품·성분에 맞는 줄만 DuckDB 로 꺼낸 뒤
그 작은 표로 지금과 같은 계산을 한다."""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import scout_sources as ss            # 특허 판정 같은 '계산 규칙'만 같이 쓴다(표는 여기서 꺼낸 작은 것)
from core import data
from core.keys import kor2engkey
from core.names import FORM_RE, calc_cagr, edi_codes, ing_core, ing_key, nospace, year_of

TODAY = pd.Timestamp.today().normalize()
WON = dict(title="상한금액(원)", tickformat=",d", separatethousands=True)
CAP = 500
ss._ensure = lambda *a, **k: None     # 자료 확보는 v2 의 data.ensure 가 한다


def show_df(df, height=360, file_name="결과.csv"):
    n = len(df)
    st.dataframe(df.head(CAP), width="stretch", height=height, hide_index=True)
    if n > CAP:
        st.caption(f"표에는 상위 {CAP:,}건만 표시했습니다 (전체 {n:,}건). 전체는 아래 CSV로 받으세요.")
        st.download_button(f"⬇️ 전체 {n:,}건 CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=file_name, key=f"dl_{file_name}_{n}")


AP, PT, CL, PR = data.table("approval"), data.table("patent"), data.table("clinical"), data.table("price")
HAS_REG, HAS_PRICE = AP is not None, PR is not None
amt = data._mtime(data.FILES["approval"])


@st.cache_resource(show_spinner="허가 목록 준비 중…", max_entries=4)
def _opts(basis, _amt):
    if basis == "제품명":
        s = data.q(f'SELECT DISTINCT trim(CAST("품목명" AS VARCHAR)) AS v FROM {AP} WHERE "품목명" IS NOT NULL')["v"]
        return sorted({x for x in s if x})
    s = data.q(f'SELECT DISTINCT "주성분" AS v FROM {AP} WHERE "주성분" IS NOT NULL')["v"]
    return sorted({c for c in (ing_core(x) for x in s) if len(c) >= 2})


def _rows(src, where, params, cols="*"):
    return data.q(f"SELECT {cols} FROM {src} WHERE {where}", params)


def _match(src_cols, cols_avail, q):
    """contains(df, cols, q) 와 같은 조건 (SQL)."""
    use = [c for c in src_cols if c in cols_avail]
    if not use:
        return "false", []
    qn = nospace(q).lower()
    return "(" + " OR ".join(f"contains({data.ns(c)}, ?)" for c in use) + ")", [qn] * len(use)


def _union_where(cols_avail, name_cols, q, keys, cores, core_col="_core", kexpr="''"):
    """이름 포함 OR 동일성분(_key) OR 동일성분 코어 — 지금 사이트 contains + _ing_union + _kor_union."""
    w, p = _match(name_cols, cols_avail, q)
    if keys and kexpr != "''":
        w += f" OR list_contains(?, {kexpr})"
        p.append(sorted(keys))
    if cores and core_col in cols_avail:
        w += f" OR list_contains(?, \"{core_col}\")"
        p.append(sorted(cores))
    return w, p


# ══════════════════════════ 화면 ══════════════════════════
st.subheader("🔍 제품 종합분석")
if not HAS_REG:
    data.show_missing("approval")
    st.caption("‘상태’ 화면에서 자료마다 받기 상태와 원인을 볼 수 있습니다.")
    st.stop()
c0, c1, c2 = st.columns([1, 3, 1])
basis = c0.radio("검색 기준", ["제품명", "주성분"], key="s_basis")
opts = _opts(basis, amt)
kw = c1.text_input(f"{basis} 검색어 입력(일부만)", key="s_kw", placeholder="예: 리피토 / 아토르바스타틴")
k_ = nospace(kw).lower()
fopts = [o for o in opts if k_ in nospace(o).lower()][:300] if kw else opts[:300]
cap_note = f" · 상위 {len(fopts)}개 표시" if len(fopts) >= 300 else ""
sel = c1.selectbox(f"허가 {basis} 선택 ({len(opts):,}개 중 검색{cap_note})", ["(선택하세요)"] + fopts, key="s_sel")
src1 = c2.radio("매출 자료원", ["IQVIA", "UBIST"], key="s1")
q = "" if sel == "(선택하세요)" else sel
if not q:
    st.stop()

# 선택값 해석 (지금 사이트와 같음)
apc = data.cols("approval")
q_brand, ing_keys, cores = q, set(), set()
if basis == "제품명":
    q_brand = re.split(r"[0-9(\[]", re.sub(r"\s+", "", q))[0] or q
    r = _rows(AP, '"품목명" = ?', [q], '"주성분", "주성분(영문)"')
    ing_keys = {k for k in (ing_key(x) for x in r["주성분(영문)"].dropna()) if k}
    cores = {c for c in (ing_core(x) for x in r["주성분"].dropna()) if len(c) >= 2}
else:
    cores = {ing_core(q)} if len(ing_core(q)) >= 2 else set()
    if re.search(r"[A-Za-z]", q) and ing_key(q):
        ing_keys.add(ing_key(q))
    r = _rows(AP, "contains(CAST(\"주성분\" AS VARCHAR), ?) OR contains(lower(CAST(\"주성분(영문)\" AS VARCHAR)), lower(?))",
              [q, q], '"주성분(영문)"')
    ing_keys |= {k for k in (ing_key(x) for x in r["주성분(영문)"].dropna()) if k}
edic = next((c for c in ["보험코드(EDI)", "보험코드", "보험EDI코드", "EDI코드", "주보험코드"] if c in apc), None)
edis_self, edis_same = set(), set()
if basis == "제품명" and edic:
    r = _rows(AP, '"품목명" = ?', [q], f'"{edic}"')
    edis_self = {e for x in r[edic].dropna() for e in edi_codes(x)}
if cores:
    cc = "_core" if "_core" in apc else None
    if cc:
        same = _rows(AP, 'list_contains(?, "_core")', [sorted(cores)],
                     '"주성분(영문)"' + (f', "{edic}"' if edic else ""))
        ing_keys |= {k for k in (ing_key(x) for x in same["주성분(영문)"].dropna()) if k}
        if edic:
            edis_same = {e for x in same[edic].dropna() for e in edi_codes(x)}
edis_self = {e for e in edis_self if len(e) >= 6}
edis_same = {e for e in edis_same if len(e) >= 6}
if cores and not ing_keys:
    c2k, _ = kor2engkey()
    ing_keys |= {c2k[c] for c in cores if c2k.get(c)}

# ── 매출 ──
S, cfg, yr = data.sales(src1)
if S is None or not yr:
    st.info(f"{src1} 매출 자료가 아직 준비되지 않았습니다 — 허가/특허/임상/약가는 아래에서 확인할 수 있습니다."
            + (f"\n\n원인: {data.ERR[src1.lower()]}" if data.ERR.get(src1.lower()) else ""))
else:
    scols = data.cols(src1.lower())
    secol = next((c for c in scols if "EDI" in str(c).upper() or ("보험" in str(c) and "코드" in str(c))
                  or str(c).strip() in ("보험코드", "주보험코드", "약가코드", "코드")), None)
    bt = re.sub(FORM_RE, "", q_brand)
    terms = sorted({t for t in {q_brand, bt} if len(t) >= 2})
    pref = sorted({t[:k] for t in terms for k in range(4, len(t) + 1)})
    P_, I_ = data.ns(cfg["prod"]), f'CAST("{cfg["ing"]}" AS VARCHAR)'
    ikey = f"upper(regexp_extract({I_}, '([A-Za-z]{{3,}})', 1))"
    edi_x = f"regexp_replace(CAST(\"{secol}\" AS VARCHAR), '\\D', '', 'g')" if secol else "''"
    form_sql = FORM_RE.replace("'", "''")
    # 제품명·동일성분 후보만 먼저 꺼낸다(작은 표). 판정은 아래에서 지금 사이트와 같은 규칙으로.
    conds = {}
    conds["pm"] = (" OR ".join(f"contains({P_}, ?)" for _ in terms) or "false", [t.lower() for t in terms])
    fm_sql, fm_p = [], []
    if ing_keys:
        fm_sql.append(f"list_contains(?, {ikey})"); fm_p.append(sorted(ing_keys))
    if cores:
        fm_sql.append(f"regexp_matches({I_}, ?)"); fm_p.append("|".join(re.escape(c) for c in cores))
    if edis_same and secol:
        fm_sql.append(f"list_contains(?, {edi_x})"); fm_p.append(sorted(edis_same))
    conds["fm"] = (" OR ".join(fm_sql) or "false", fm_p)
    conds["pe"] = (f"list_contains(?, {edi_x})", [sorted(edis_self)]) if (edis_self and secol) else ("false", [])
    conds["pp"] = (f"list_contains(?, regexp_replace({P_}, '{form_sql}', ''))", [[p_.lower() for p_ in pref]]) \
        if pref else ("false", [])
    sel_sql = ", ".join(f"({c[0]}) AS _{k}" for k, c in conds.items())
    where_sql = " OR ".join(f"({c[0]})" for c in conds.values())
    prm = [x for c in conds.values() for x in c[1]] * 2
    sub = data.q(f"SELECT *, {sel_sql} FROM {S} WHERE {where_sql}", prm)
    if not sub.empty:
        sub["_pm"] = sub["_pm"] | sub["_pe"]
    by_ing = by_pref = False
    if sub.empty:
        hit = sub
    elif basis == "주성분":
        hit, by_ing = sub[sub["_fm"]], True
    else:
        hit = sub[sub["_pm"]]
        if hit.empty and sub["_pp"].any():
            hit, by_pref = sub[sub["_pp"]], True
        if hit.empty and sub["_fm"].any():
            hit = sub[sub["_fm"]]
            by_ing = not hit.empty
    if not hit.empty:
        s = hit[yr].sum()
        cg = calc_cagr(s.tolist(), yr)
        k = st.columns(4)
        k[0].metric(("동일성분 " if by_ing else "") + f"최신연도({year_of(yr[-1])}) 매출", f"{s.iloc[-1] / 1e8:,.1f} 억")
        k[1].metric("매출 CAGR", f"{cg:+.1f}%" if cg is not None else "—")
        k[2].metric("매출상 제조사", f"{hit[cfg['maker']].nunique()} 곳")
        k[3].metric("품목 수" if by_ing else "성분 수",
                    f"{len(hit)} 품목" if by_ing else f"{hit[cfg['ing']].nunique()} 종")
        if by_ing:
            st.caption("※ 선택한 성분의 매출을 합산했습니다." if basis == "주성분"
                       else "※ 제품명 직접 매칭이 없어 '동일성분' 매출을 합산해 보여줍니다.")
        elif by_pref:
            st.caption("※ 제품명 표기가 자료마다 달라, 앞부분이 같은 제품으로 매칭했습니다 → "
                       + ", ".join(sorted(hit[cfg["prod"]].astype(str).unique())[:8]))
    else:
        st.info(f"{src1} 매출 매칭 없음 — 이 제품/성분이 {src1} 매출 파일에 없을 수 있어요 "
                f"(‘매출 분석’ 탭에서 직접 검색해 확인). 허가/특허/임상/약가는 아래 확인)")
        stub = (bt or q_brand)[:2]
        if len(stub) >= 2:
            cand = data.q(f'SELECT DISTINCT CAST("{cfg["prod"]}" AS VARCHAR) AS p FROM {S} '
                          f'WHERE starts_with({data.ns(cfg["prod"])}, ?) ORDER BY 1 LIMIT 12', [stub.lower()])["p"]
            if len(cand):
                st.caption(f"참고 · {src1}에서 ‘{stub}…’로 시작하는 제품명: " + ", ".join(cand))

tabs = st.tabs(["📋 허가", "⚖️ 특허", "🧪 임상", "💊 약가·이벤트", "🔁 재심사(PMS)", "🧬 DMF(동일성분)"])

with tabs[0]:
    w, p = _union_where(apc, ["품목명", "주성분", "주성분(영문)"], q, ing_keys, cores, kexpr=data.key_expr("approval"))
    m = _rows(AP, w, p).drop_duplicates()
    st.caption(f"허가 {len(m):,}건 · 업체 {m['업체명'].nunique()}곳 · 신약 {int((m.get('신약구분', pd.Series(dtype=str)) == '신약').sum())}건")
    cc = [c for c in ["품목명", "업체명", "허가일자", "전문/일반", "주성분", "신약구분", "상태", "보험코드(EDI)"] if c in m.columns]
    show_df(m[cc].sort_values("허가일자", ascending=False) if "허가일자" in cc else m[cc], height=300, file_name="허가목록.csv")

with tabs[1]:
    if PT is None:
        data.show_missing("patent")
    else:
        w, p = _union_where(data.cols("patent"), ["품목명", "INGR_ENG_NAME", "INGR_NAME"], q, ing_keys, cores,
                            kexpr=data.key_expr("patent"))
        m = ss.enrich_patent(_rows(PT, w, p).drop_duplicates())   # 시트의 완전히 같은 중복 줄은 지금 사이트처럼 지운다
        m["만료D(년)"] = ((m["_exp"] - TODAY).dt.days / 365.25).round(1)
        live = ss.active_patent_mask(m["DOMESTIC_PATENT_STATUS"])
        if ss.has_listed_flag(m):
            lst = ss.listed_patent_mask(m)
            st.caption(f"특허 {len(m):,}건 · 식약처 등재(제품특허) {int(lst.sum())}건 "
                       f"(존속 등록 {int((lst & live).sum())}건) · 기타특허 {int((~lst).sum())}건")
            st.caption("만료일 판단은 **제품특허**(원개발사가 식약처 특허목록에 등재한 특허)로 합니다. "
                       "기타특허는 같은 성분에 관한 다른 특허(다른 회사의 염·수화물·조성 특허 등)라 참고용입니다.")
        else:
            st.caption(f"특허 {len(m):,}건 · 존속 등록 {int(live.sum())}건")
        cmap = {ss.PATENT_PAGE_COL: "구분", "품목명": "품목명", "PATENT_GB_CODE": "유형", "PATENTEE": "특허권자",
                "DOMESTIC_PATENT_NO": "특허번호", "DOMESTIC_PATENT_STATUS": "상태", "DOMESTIC_END_DATE": "만료일",
                "만료D(년)": "만료D(년)", "DOMESTIC_INVN_NM": "발명의 명칭"}
        cmap = {a: b for a, b in cmap.items() if a in m.columns}
        try:
            ordr, tier = ss.patent_order(m)
            pm_ = m.loc[ordr]
            if ss.has_listed_flag(m):
                n1 = int((tier == 1).sum())
                st.caption("순서: **제품특허**(허가권자가 식약처 특허목록에 등재) → "
                           + (f"허가권자와 같은 회사의 기타특허 {n1}건 → " if n1 else "")
                           + "다른 회사 특허 · 각 묶음 안에서는 존속 중, 만료일 늦은 순")
        except Exception:
            pm_ = m.sort_values("DOMESTIC_END_DATE", ascending=False)
        show_df(pm_.rename(columns=cmap)[list(cmap.values())], height=300, file_name="특허목록.csv")

with tabs[2]:
    if CL is None:
        data.show_missing("clinical")
    else:
        clc = data.cols("clinical")
        w, p = _match(["제품명", "성분명"], clc, q)
        if cores and "_core" in clc:
            w += ' OR list_contains(?, "_core")'
            p.append(sorted(cores))
        if ing_keys and "성분명" in clc:
            w += " OR list_contains(?, upper(regexp_extract(CAST(\"성분명\" AS VARCHAR), '([A-Za-z]{3,})', 1)))"
            p.append(sorted(ing_keys))
        m = _rows(CL, w, p).drop_duplicates()
        stp = m["CLINIC_STEP_NM"] if "CLINIC_STEP_NM" in m.columns else pd.Series([], dtype=str)
        st.caption(f"임상 {len(m):,}건 · 생동(제네릭 개발) {int(stp.astype(str).str.contains('생동', na=False).sum())}건")
        cmap = {"제품명": "제품명", "성분명": "성분명", "CLINIC_STEP_NM": "단계", "TRGT_DISS_NM": "대상질환",
                "STATUS": "상태", "CLST_APRV_DT": "승인일", "원개발사": "개발사"}
        cc = {a: b for a, b in cmap.items() if a in m.columns}
        mm = m.rename(columns=cc)[list(cc.values())]
        show_df(mm.sort_values("승인일", ascending=False) if "승인일" in mm.columns else mm,
                height=300, file_name="임상목록.csv")

with tabs[3]:
    if not HAS_PRICE:
        data.show_missing("price")
    else:
        prc = data.cols("price")
        w, p = _match(["제품명", "주성분명"], prc, q_brand)
        if cores and "_core" in prc:
            w += ' OR list_contains(?, "_core")'
            p.append(sorted(cores))
        mp = _rows(PR, w, p)
        if not mp.empty:
            mp["금액"] = pd.to_numeric(mp["금액"], errors="coerce")
            mp["적용일자"] = pd.to_datetime(mp["적용일자"], errors="coerce")
            if "_key" not in mp.columns:
                mp["_key"] = mp["주성분명"].map(ing_key)
        if mp.empty:
            st.info("약가 매칭 없음")
        else:
            keys = {k for k in mp["_key"].unique() if k} or set(ing_keys)
            st.caption(f"약가 매칭 {len(mp):,}건 · 제품 {mp['제품코드'].nunique()}개 · 성분키 {len(keys)}개")
            counts = mp[mp["급여구분"] == "급여"]["제품명"].value_counts()
            if counts.empty:
                counts = mp["제품명"].value_counts()
            popts = list(counts.index)
            rkeys = sorted(ing_keys or keys)
            origs = _rows(AP, f"\"신약구분\" = '신약' AND list_contains(?, {data.key_expr('approval')})", [rkeys],
                          '"품목명"')["품목명"].astype(str).tolist() if rkeys else []
            prefixes = {p for p in (re.split(r"[0-9]", re.sub(r"\s", "", o))[0] for o in origs) if len(p) >= 2}
            found = next((i for i, p in enumerate(popts)
                          if any(re.sub(r"\s", "", str(p)).startswith(pre) for pre in prefixes)), None)
            if found is None:
                fd = mp[mp["급여구분"] == "급여"].groupby("제품명")["적용일자"].min()
                if not fd.empty and fd.idxmin() in popts:
                    found = popts.index(fd.idxmin())
            st.caption("※ 성분 검색 시 기본값은 오리지널(신약) 기준입니다. 다른 품목은 아래에서 선택하세요.")
            psel = st.selectbox("약가 추이를 볼 품목", popts, index=found or 0, key="price_prod")
            one = mp[mp["제품명"] == psel].sort_values("적용일자")
            one_gy = one[one["급여구분"] == "급여"]
            sel_keys = {k for k in one["_key"].unique() if k} or keys
            if not one_gy.empty:
                f0, f1 = one_gy["금액"].iloc[0], one_gy["금액"].iloc[-1]
                km = st.columns(4)
                km[0].metric("최초 급여가", f"{f0:,.0f} 원")
                km[1].metric("최신 급여가", f"{f1:,.0f} 원")
                km[2].metric("누적 인하율", f"{(f1 - f0) / f0 * 100:+.1f}%" if f0 else "—")
                km[3].metric("이력 기간", f"{one_gy['적용일자'].min().year}~{one_gy['적용일자'].max().year}")
            mat_dates, form_dates, use_dates, appr = [], [], [], None
            if PT is not None and sel_keys:
                ptk = ss.enrich_patent(_rows(PT, f'list_contains(?, {data.key_expr("patent")})', [sorted(sel_keys)]))
                reg = ptk[ss.active_patent_mask(ptk["DOMESTIC_PATENT_STATUS"]) & ptk["_exp"].notna()
                          & ss.listed_patent_mask(ptk)]
                gb = reg["PATENT_GB_CODE"]
                mat_dates = sorted(reg[ss.substance_mask(gb)]["_exp"].dropna().unique())
                form_dates = sorted(reg[ss.gb_mask(gb, "물질형태") & ~ss.substance_mask(gb)]["_exp"].dropna().unique())
                use_dates = sorted(reg[ss.gb_mask(gb, "용도")]["_exp"].dropna().unique())
            if sel_keys:
                appr = _rows(AP, f"list_contains(?, {data.key_expr('approval')}) AND \"상태\" = '정상'", [sorted(sel_keys)])
                appr["_hd"] = pd.to_datetime(appr["허가일자"].astype(str), format="%Y%m%d", errors="coerce")
                appr = appr[appr["_hd"].notna()]
            fig = go.Figure()
            for nm, g in mp[(mp["급여구분"] == "급여") & (mp["제품명"] != psel)].groupby("제품명"):
                if len(g) >= 2:
                    fig.add_trace(go.Scatter(x=g["적용일자"], y=g["금액"], mode="lines",
                                             line=dict(width=1, color="lightgray"), line_shape="hv", opacity=0.35,
                                             showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=one_gy["적용일자"], y=one_gy["금액"], mode="lines+markers", name=psel[:26],
                                     line=dict(color="royalblue", width=2.5), line_shape="hv"))
            dele = one[one["급여구분"] == "삭제"]
            if not dele.empty:
                fig.add_trace(go.Scatter(x=dele["적용일자"], y=[0] * len(dele), mode="markers",
                                         marker=dict(color="black", symbol="x", size=11), name="급여삭제"))
            shp, ann = [], []
            for ds, col, dash, y, txt in ((mat_dates, "crimson", "dash", 1.0, "물질특허만료"),
                                          (form_dates, "mediumpurple", "dashdot", 0.64, "염·형태특허만료"),
                                          (use_dates, "darkorange", "dot", 0.82, "용도특허만료")):
                for d in ds:
                    dt = pd.Timestamp(d)
                    shp.append(dict(type="line", xref="x", yref="paper", x0=dt, x1=dt, y0=0, y1=1,
                                    line=dict(color=col, dash=dash)))
                    ann.append(dict(x=dt, y=y, xref="x", yref="paper", text=txt, showarrow=False,
                                    font=dict(color=col, size=9), textangle=-90, xanchor="left"))
            if shp:
                fig.update_layout(shapes=shp, annotations=ann)
            if appr is not None and not appr.empty:
                by = (one_gy["금액"].max() if not one_gy.empty else 1) * 0.03
                fig.add_trace(go.Scatter(x=appr["_hd"], y=[by] * len(appr), mode="markers",
                                         marker=dict(color="green", symbol="triangle-up", size=8, opacity=0.5),
                                         name="동일성분 등재(허가)", text=appr["품목명"],
                                         hovertemplate="%{x|%Y-%m-%d} 등재<br>%{text}<extra></extra>"))
            fig.update_layout(height=440, title=f"{psel} — 급여 상한금액 전체 이력 + 등재·특허 만료", yaxis=WON,
                              legend=dict(orientation="h", y=-0.25), hovermode="x unified")
            st.plotly_chart(fig, width="stretch")
            st.caption("파란선=선택 품목 · 회색=동일성분 타 품목 · 초록△=동일성분 등재(허가) · 빨강=물질특허만료 · "
                       "보라=염·형태특허만료 · 주황=용도특허만료 · ✕=급여삭제 (특허는 식약처 등재 특허만)")
            if not one_gy.empty:
                try:
                    nyears = ss.nego_years(psel) if ss.nego_available() else []
                except Exception:
                    nyears = []
                if nyears:
                    st.caption(f"🏛️ 공단 약가협상 완료 연도(공식·NHIS): {', '.join(nyears)}")
                gg = one_gy.sort_values("적용일자").reset_index(drop=True)
                drows = []
                for i in range(1, len(gg)):
                    p0, p1 = gg.loc[i - 1, "금액"], gg.loc[i, "금액"]
                    if p1 < p0:
                        d = gg.loc[i, "적용일자"]
                        gen = 0
                        if appr is not None and not appr.empty:
                            gen = int(((appr["_hd"] >= d - pd.Timedelta(days=455)) & (appr["_hd"] <= d + pd.Timedelta(days=90))).sum())
                        near_mat = [pd.Timestamp(x).date() for x in mat_dates if abs((pd.Timestamp(x) - d).days) <= 365]
                        near_use = [pd.Timestamp(x).date() for x in use_dates if abs((pd.Timestamp(x) - d).days) <= 365]
                        drows.append({"적용일자": d.date(), "이전가": int(p0), "인하가": int(p1),
                                      "인하율%": round((p1 - p0) / p0 * 100, 1), "동일성분 허가등록(±15개월)": gen,
                                      "물질특허 만료(±1년)": str(near_mat[0]) if near_mat else "-",
                                      "용도특허 만료(±1년)": str(near_use[0]) if near_use else "-",
                                      "공단 협상완료(해당연도·공식)": "○" if str(d.year) in nyears else ""})
                if drows:
                    st.markdown("**📉 가격 인하 시점 전후 사실** (공식 데이터, 사유 해석 없음)")
                    st.dataframe(pd.DataFrame(drows), hide_index=True, width="stretch")

with tabs[4]:
    try:
        rj = ss.load_rejdge()
    except Exception:
        rj = None
    if rj is None or rj.empty:
        st.info("재심사(PMS) 데이터 미연결 (구글시트 '재심사' 탭이 서비스계정에 공유됐는지 확인)")
    else:
        name_cols = [c for c in ["ITEM_NAME", "ENTP_NAME"] if c in rj.columns] or [c for c in rj.columns if "NAME" in c.upper()]
        qn = nospace(q_brand).lower()
        mk = pd.Series(False, index=rj.index)
        for c in name_cols:
            mk |= rj[c].astype(str).str.replace(r"\s+", "", regex=True).str.lower().str.contains(qn, regex=False, na=False)
        m = rj[mk]
        st.caption(f"재심사 매칭 {len(m):,}건 (전체 {len(rj):,}건)")
        if m.empty:
            st.info("재심사 매칭 없음")
        else:
            label = {"ITEM_NAME": "품목명", "ENTP_NAME": "업체명", "REEXAM_CODE_NAME": "재심사구분",
                     "REEXAM_CD_NM": "재심사구분", "REEXAM_START_DATE": "재심사시작", "REEXAM_END_DATE": "재심사종료",
                     "RESULT_DATE": "결과일", "CLASS_NO_NAME": "분류", "CLASS_NO": "분류", "ITEM_SEQ": "품목기준코드",
                     "ITEM_NO": "품목번호", "BIZRNO": "사업자번호"}
            prefer = [c for c in ["ITEM_NAME", "ENTP_NAME"] if c in m.columns]
            ordered = prefer + [c for c in m.columns if c not in prefer]
            show_df(m[ordered].rename(columns={c: label.get(c, c) for c in ordered}), height=320, file_name="재심사.csv")

with tabs[5]:
    try:
        dmf = ss.load_dmf()
    except Exception:
        dmf = None
    if dmf is None or dmf.empty:
        st.info("DMF 데이터 미연결")
    else:
        ing_col = ss._dmf_ing_col(dmf)
        name_cols = [c for c in [ing_col, "ENTP_NAME"] if c]
        qn = nospace(q).lower()
        mk = pd.Series(False, index=dmf.index)
        for c in name_cols:
            mk |= dmf[c].astype(str).str.replace(r"\s+", "", regex=True).str.lower().str.contains(qn, regex=False, na=False)
        m = dmf[mk]
        if ing_col and cores:
            def _hit(s):
                sc, sn = ing_core(s), re.sub(r"\s+", "", str(s))
                return any(c and (c == sc or c in sn or (len(c) >= 3 and c in sc)) for c in cores)
            m = pd.concat([m, dmf[dmf[ing_col].astype(str).apply(_hit)]]).drop_duplicates()
        no_match = m.empty
        stc, lkc = ss.dmf_status_col(dmf), ss.dmf_link_col(dmf)
        mtag = f" · 매칭 성분: {', '.join(sorted(cores)[:6])}" if cores else ""
        if stc:
            meta = ss.dmf_meta() or {}
            dt = pd.to_datetime(m.get("DMF_PERMIT_DATE", pd.Series(dtype=str)), errors="coerce")
            okm = m[stc].astype(str).str.strip().eq("정상")
            new = okm & (dt >= TODAY - pd.Timedelta(days=90))
            lnk = m[lkc].astype(str).str.strip().ne("") if lkc else pd.Series(False, index=m.index)
            st.caption("식약처 DMF 공고 기준" + (f" · {meta.get('fetched_at')} 받음" if meta.get("fetched_at") else "")
                       + f" · 동일성분 DMF {len(m):,}건 (정상 {int(okm.sum()):,} · 전체 {len(dmf):,}건)"
                       + f" · 연계심사 문서 있음 {int(lnk.sum()):,}건 · 최근 90일 새 등록 {int(new.sum()):,}건" + mtag)
            d1, d2 = st.columns(2)
            inc_off = d1.checkbox("취하·취소·폐업 포함", value=False, key="dmf_inc_off")
            lnk_only = d2.checkbox("연계심사만 보기", value=False, key="dmf_link_only")
            if not m.empty:
                m = m.assign(상태=m[stc].astype(str).str.strip().where(~new, "새 등록"),
                             연계심사=lnk.map({True: "연계심사", False: ""}), _dt=dt)
                if not inc_off:
                    m = m[okm.reindex(m.index, fill_value=False)]
                if lnk_only:
                    m = m[m["연계심사"] == "연계심사"]
                m = m.sort_values("_dt", ascending=False, na_position="last").drop(columns="_dt")
        else:
            st.caption(f"동일성분 DMF {len(m):,}건 (전체 {len(dmf):,}건)" + mtag)
        if not no_match and m.empty:
            st.info("조건에 맞는 DMF가 없습니다. '취하·취소·폐업 포함'을 켜거나 '연계심사만 보기'를 꺼 보세요.")
        elif no_match:
            st.info("동일성분 DMF 매칭 없음")
        else:
            label = {"INGR_KOR_NAME": "성분(한글)", "INGR_NAME": "성분", "ENTP_NAME": "업체명(수입/제조)",
                     "MNFCTR_NAME": "제조사", "MANUF_COUNTRY_CODE_NM": "제조국", "DMF_PERMIT_DATE": "등록일",
                     "DMF_PERMIT_NO": "DMF번호"}
            if stc:
                label = dict(label, ENTP_NAME="신청인", MNFCTR_NAME="제조소", DMF_PERMIT_DATE="최초등록일",
                             DMF_PERMIT_NO="등록번호")
                prefer = [c for c in ["상태", "연계심사", ing_col, "ENTP_NAME", "MNFCTR_NAME", "MANUF_COUNTRY_CODE_NM",
                                      "DMF_PERMIT_DATE", "최종변경일자", "DMF_PERMIT_NO", "문서번호", "연계심사문서번호"]
                          if c and c in m.columns]
                ordered = prefer + [c for c in m.columns if c not in prefer and c != stc]
            else:
                prefer = [c for c in [ing_col, "ENTP_NAME"] if c and c in m.columns]
                ordered = prefer + [c for c in m.columns if c not in prefer]
            show_df(m[ordered].rename(columns={c: label.get(c, c) for c in ordered}), height=320, file_name="DMF.csv")
