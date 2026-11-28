"""약가 분석 (v2). 지금 사이트 '약가 분석 → 약가 조회'와 같은 화면·같은 결과를,
약가 표를 메모리에 올리지 않고 DuckDB 로 파일에서 필요한 줄만 꺼내 만든다."""
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import data
from core.names import ing_core, ing_key, nospace

WON = dict(title="상한금액(원)", tickformat=",d", separatethousands=True)
CAP = 500

st.subheader("💊 약가 분석")
P, A = data.price(), data.approval()
if not P:
    data.show_missing("price")
    st.stop()

mode = st.radio("보기", ["약가 조회", "약가 산정"], horizontal=True, key="price_mode", label_visibility="collapsed")
if mode == "약가 산정":
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None          # 자료 확보는 v2 의 data.ensure 가 한다
    data.ensure("approval")
    from views import price_calc_mod
    price_calc_mod._price_calc_page()
    ss.release_memory()
    st.stop()

mt = data._mtime(data.FILES["price"])
amt = data._mtime(data.FILES["approval"])


@st.cache_resource(show_spinner=False, max_entries=2)
def _ing_opts(_amt):
    if not A:
        return []
    s = data.q(f'SELECT DISTINCT "주성분" FROM {A} WHERE "주성분" IS NOT NULL')["주성분"]
    return sorted({c for c in (ing_core(x) for x in s) if len(c) >= 2})


@st.cache_resource(show_spinner=False, max_entries=2)
def _prod_opts(_mt):
    s = data.q(f'SELECT DISTINCT trim("제품명") AS n FROM {P} WHERE "제품명" IS NOT NULL')["n"]
    return sorted({x for x in s if x})


@st.cache_resource(show_spinner=False, max_entries=2)
def _latest(_mt):
    """제품코드별 마지막 줄 — DuckDB 안에 작은 표로 한 번 만들어 둔다(파일이 바뀌면 새로)."""
    name = f"price_latest_{int(_mt)}"
    data.q(f"""CREATE OR REPLACE TABLE {name} AS
               SELECT * FROM {P}
               QUALIFY row_number() OVER (PARTITION BY "제품코드" ORDER BY "적용일자" DESC) = 1""")
    return name


def _filter_opts(opts, kw, cap=300):
    if not kw:
        return opts[:cap]
    k = nospace(kw).lower()
    return [o for o in opts if k in nospace(o).lower()][:cap]


def _keys(term):
    """검색어 → 성분키 집합 (지금 사이트 resolve_keys 와 같은 규칙)."""
    keys = set()
    if re.search(r"[A-Za-z]", term) and ing_key(term):
        keys.add(ing_key(term))
    if A:
        r = data.q(f"""SELECT DISTINCT "주성분(영문)" AS e FROM {A}
                       WHERE contains(CAST("주성분" AS VARCHAR), ?) OR contains(lower(CAST("주성분(영문)" AS VARCHAR)), lower(?))""",
                   [term, term])
        keys |= {ing_key(x) for x in r["e"].dropna() if ing_key(x)}
    return {k for k in keys if k}


def _trend_codes(basis, term):
    if basis == "제품":
        r = data.q(f'SELECT DISTINCT "제품코드" FROM {P} WHERE "제품명" = ?', [term])
        return r["제품코드"].dropna().tolist(), f"{term} 약가 변동"
    core = ing_core(term)
    keys = _keys(term) | _keys(core)
    origs = []
    if keys and A:
        origs = data.q(f"""SELECT "품목명" FROM {A} WHERE "신약구분" = '신약' AND list_contains(?, _k)""",
                       [sorted(keys)])["품목명"].dropna().astype(str).tolist()
    pref = sorted({p for p in (re.split(r"[0-9]", nospace(o))[0] for o in origs) if len(p) >= 2})
    if pref:
        # 오리지널 품목명 앞부분(브랜드)으로 시작하는 약가 제품 — 띄어쓰기 무시
        r = data.q(f"""SELECT DISTINCT "제품코드" FROM {P}
                       WHERE len(list_filter(?, lambda p: starts_with(
                             regexp_replace(CAST("제품명" AS VARCHAR), '\\s', '', 'g'), p))) > 0""", [pref])
        return r["제품코드"].dropna().tolist(), f"{term} 오리지널(신약) 약가 변동"
    return [], f"{term} 오리지널(신약) 약가 변동"


# ── 약가 변동 그래프 (+ 제네릭 등재 시점) ─────────────────────────────────
from core import generic_light as GL   # noqa: E402
from core.ui import show_df            # noqa: E402

st.markdown("**🔎 약가 변동 그래프** · 성분=허가 최초 품목(오리지널) 기준, 제품=고른 품목 하나(함량·포장까지 구분)")
pc = st.columns([1, 1, 2])
basis = pc[0].radio("기준", ["성분", "제품"], key="ptrend_basis", horizontal=True)
byspec = basis == "제품"
gmt = GL._mt()
ge_opts = [] if byspec else GL.options()
opts = _prod_opts(mt) if byspec else (ge_opts or _ing_opts(amt))
kw = pc[1].text_input(f"{basis} 검색어", key="ptrend_kw", placeholder="일부만 입력")
sel = pc[2].selectbox(f"{'약가' if byspec else '허가'} {basis} 선택 ({len(opts):,}개 중 검색"
                      + ("" if byspec else " · 복합제 조합 포함") + ")",
                      ["(선택하세요)"] + _filter_opts(opts, kw), key="ptrend_sel2")
if sel and sel != "(선택하세요)":
    gr, show_gen, lab, osel = None, False, [], 0
    if not byspec and ge_opts:
        try:
            g0 = GL.run(sel, None, gmt)
        except Exception as e:
            g0 = None
            st.caption(f"제네릭 등재 계산을 건너뜀 ({type(e).__name__})")
        if g0 and g0["families"]:
            fams = g0["families"]
            lab = [f"{re.split(r'[(_]', str(nm))[0][:30]} · {co} · 허가 {d:%Y.%m}" + (" · 신약" if nw else "")
                   for _b, co, nm, d, nw in fams]
            fkeys = [(f[0], f[1]) for f in fams]
            ai = fkeys.index(g0["auto"]) if g0["auto"] in fkeys else 0
            oc = st.columns([3, 1])
            osel = oc[0].selectbox(f"오리지널 (자동: {g0['reason']} · 허가일자 최초, 신약 표시 우선 · 바꿀 수 있음)",
                                   range(len(lab)), index=ai, format_func=lambda i: lab[i], key=f"ptrend_orig_{sel}")
            show_gen = oc[1].checkbox("제네릭 등재 시점 표시", value=True, key="ptrend_gen")
            pick = fkeys[osel]
            gr = g0 if pick == g0["auto"] else GL.run(sel, pick, gmt)
            if gr.get("warn"):
                w = ", ".join(f"**{re.split(r'[(_]', str(x['name']))[0][:24]}**({x['company']} · {int(x['price']):,}원)"
                              for x in gr["warn"][:5])
                st.warning(f"⚠️ **오리지널이 맞는지 확인해 주세요.** 같은 함량·제형에서 자료 시작일 상한가가 "
                           f"지금 오리지널보다 높은 품목이 있습니다: {w} — 지금 오리지널 "
                           f"{int(gr['warn'][0]['orig_price']):,}원. 오래된 오리지널은 허가를 다시 받아 "
                           f"허가일자가 늦게 적혀 있을 수 있습니다. 위 **오리지널** 칸에서 바꿀 수 있습니다.")
            if gr.get("note"):
                st.info(gr["note"])
    if gr is not None and gr.get("orig_codes"):
        codes = list(gr["orig_codes"])
        title = f"{sel} 오리지널({lab[osel].split(' · ')[0]}) 약가 변동"
    else:
        codes, title = _trend_codes(basis, sel)
        show_gen = False
    if byspec:
        g = data.q(f"""SELECT "제제코드" FROM {P} WHERE "제품명" = ? AND coalesce("제제코드", '') <> '' LIMIT 1""", [sel])
        if not g.empty:
            gc = g["제제코드"].iat[0]
            n = data.q(f"""SELECT count(DISTINCT "제품코드") AS n FROM {P}
                           WHERE "제제코드" = ? AND "급여구분" = '급여'""", [gc])["n"].iat[0]
            st.caption(f"동일제제 `{gc}` · 같은 성분·함량·제형 급여 품목 {n}개")
    h = data.q(f"""SELECT "제품코드", "제품명", "적용일자", "금액" FROM {P}
                   WHERE list_contains(?, trim(CAST("제품코드" AS VARCHAR))) AND "급여구분" = '급여'
                   ORDER BY "적용일자" """, [[str(c).strip() for c in codes]]) if codes else pd.DataFrame()
    if h.empty:
        st.info("해당 급여 약가 이력이 없습니다.")
    else:
        show = list(h.groupby("제품명").size().sort_values(ascending=False).index[:30])
        fig = go.Figure()
        for nm, gg in h[h["제품명"].isin(show)].groupby("제품명"):
            fig.add_trace(go.Scatter(x=gg["적용일자"], y=gg["금액"], mode="lines+markers", name=str(nm)[:26],
                                     line_shape="hv"))
        gen_tabs = []
        if show_gen and gr is not None:
            shp, ann = [], []
            for df_, sym, col, yy, lbl in ((gr["same"], "diamond", "#e8590c", 0.03, "◆같은 약가코드 제네릭"),
                                           (gr["salt"], "diamond-open", "#6b7280", 0.09, "◇염변경 등")):
                if df_ is None or df_.empty:
                    continue
                gen_tabs.append(df_)
                own = df_["비고"] if "비고" in df_.columns else pd.Series("", index=df_.index)
                txt = [f"{str(n)[:30]}<br>{co}" + (" (오리지널 회사)" if ow else "") + f" · {d:%Y-%m-%d} 급여 등재"
                       + (" (자료 시작일 — 실제 등재일 모름)" if u else "")
                       + (f" · 첫 상한가 {int(fp):,}원" if pd.notna(fp) else "")
                       for n, co, d, u, fp, ow in zip(df_["제품명"], df_["업체"], df_["등재일"], df_["날짜모름"],
                                                      df_["첫상한가"], own)]
                fig.add_trace(go.Scatter(x=df_["등재일"], y=[yy] * len(df_), yaxis="y2", mode="markers",
                                         marker=dict(symbol=sym, size=9, color=col), name=lbl[1:],
                                         text=txt, hovertemplate="%{text}<extra></extra>"))
                kk = df_[~df_["날짜모름"]]
                if not kk.empty:
                    d1 = kk["등재일"].min()
                    shp.append(dict(type="line", xref="x", yref="paper", x0=d1, x1=d1, y0=0, y1=1,
                                    line=dict(color=col, dash="dash" if sym == "diamond" else "dot", width=1.5)))
                    ann.append(dict(x=d1, y=1.0 if sym == "diamond" else 0.92, xref="x", yref="paper",
                                    text=f"{lbl[0]} 첫 등재 {d1:%Y.%m}", showarrow=False,
                                    font=dict(color=col, size=11), xanchor="left"))
            fig.update_layout(yaxis2=dict(overlaying="y", range=[0, 1], visible=False, fixedrange=True),
                              shapes=shp, annotations=ann)
        fig.update_layout(height=430, title=title, yaxis=WON, legend=dict(orientation="h", y=-0.3),
                          hovermode="closest" if gen_tabs else "x unified")
        st.plotly_chart(fig, width="stretch")
        st.caption(f"대상 품목 {h['제품코드'].nunique()}개 · 그래프는 이력 많은 상위 30개 표시")
        if show_gen and gr is not None:
            def _sumtxt(df_, mark):
                if df_ is None or df_.empty:
                    return f"{mark} 없음"
                pre = int((df_["등재일"] <= pd.Timestamp("2010-02-01")).sum())
                kk = df_[~df_["날짜모름"]]
                t = f"{mark} {len(df_)}개"
                if pre:
                    t += f" · 2010.02 이전부터 있음({pre})"
                if not kk.empty:
                    r0 = kk.sort_values("등재일").iloc[0]
                    ow = " · 오리지널 회사" if str(r0.get("비고", "")) == "오리지널 회사" else ""
                    t += (f" · {'그 뒤 ' if pre else ''}첫 등재 {r0['등재일']:%Y.%m} "
                          f"({re.split(r'[(_]', str(r0['제품명']))[0][:18]} · {r0['업체']}{ow})")
                return t
            sm = f"📌 {_sumtxt(gr['same'], '◆ 같은 약가코드')}  |  {_sumtxt(gr['salt'], '◇ 염변경 등')}"
            if gr.get("n_combo"):
                sm += f"  |  이 성분이 든 복합제 {gr['n_combo']}개 제외 (복합제는 성분 목록에서 조합으로 따로 보기)"
            st.markdown(sm)
            if gen_tabs:
                with st.expander(f"📋 제네릭 등재 목록 (◆ {len(gr['same'])} · ◇ {len(gr['salt'])})"):
                    tb = pd.concat(gen_tabs, ignore_index=True).sort_values("등재일")
                    tb["급여 등재일"] = [("자료 시작 전" if u else f"{d:%Y.%m.%d}") for d, u in zip(tb["등재일"], tb["날짜모름"])]
                    show_df(tb[[c for c in ["구분", "급여 등재일", "제품명", "업체", "비고", "첫상한가", "지금상한가"]
                                if c in tb.columns]], height=320, file_name="제네릭등재.csv")
                st.caption("◆ = 오리지널과 심평원 약가코드(성분·함량·제형)가 같은 제품 · ◇ = 같은 성분이지만 코드가 다른 제품(염변경 등) · "
                           "등재일 = 그 제품에 처음 급여 상한가가 붙은 날 · 오리지널 회사가 낸 다른 제품(XR·OD 등)도 약가를 받았으면 "
                           "'오리지널 회사'로 표시해 포함 · 공동판매 제품은 구분하지 않음")
st.divider()

# ── 조건별 최신 약가 · 연도별 ───────────────────────────────────────────
st.markdown("**📋 조건별 최신 약가 · 연도별**")
L = _latest(mt)


def _distinct(col):
    r = data.q(f'SELECT DISTINCT trim(CAST("{col}" AS VARCHAR)) AS v FROM {L} WHERE "{col}" IS NOT NULL')["v"]
    return sorted({x for x in r if x})


f = st.columns(4)
pay = f[0].multiselect("급여구분", _distinct("급여구분"), default=["급여"], key="pr_pay")
route = f[1].multiselect("투여", _distinct("투여"), key="pr_route")
cls = f[2].multiselect("분류(코드)", _distinct("분류"), key="pr_cls")
ent = f[3].text_input("업체 검색", key="pr_ent")
kw2 = st.text_input("제품/성분 검색", key="pr_kw")

where, prm = ["true"], []
for col, v in (("급여구분", pay), ("투여", route), ("분류", cls)):
    if v:
        where.append(f'list_contains(?, trim(CAST("{col}" AS VARCHAR)))')
        prm.append(list(v))
if ent:
    where.append('contains(lower(CAST("업체명" AS VARCHAR)), lower(?))')
    prm.append(ent)
if kw2:
    k = nospace(kw2).lower()
    where.append("(contains(lower(regexp_replace(CAST(\"제품명\" AS VARCHAR), '\\s', '', 'g')), ?) "
                 "OR contains(lower(regexp_replace(CAST(\"주성분명\" AS VARCHAR), '\\s', '', 'g')), ?))")
    prm += [k, k]
W = " AND ".join(where)

agg = data.q(f"""SELECT count(*) AS n,
                        max("금액") FILTER (WHERE "급여구분" = '급여') AS hi,
                        min("금액") FILTER (WHERE "급여구분" = '급여') AS lo,
                        count(*) FILTER (WHERE "급여구분" = '급여') AS ng
                 FROM {L} WHERE {W}""", prm).iloc[0]
n = int(agg["n"])
if n == 0:
    st.warning("조건에 맞는 약가 없음")
    st.stop()
k3 = st.columns(3)
k3[0].metric("품목 수", f"{n:,}")
k3[1].metric("상한금액 (최고)", f"{agg['hi']:,.0f} 원" if pd.notna(agg["hi"]) else "—")
k3[2].metric("하한금액 (최저)", f"{agg['lo']:,.0f} 원" if pd.notna(agg["lo"]) else "—")

# ⭐ 오리지널(신약)
if A and int(agg["ng"]) > 0 and n <= 2000:
    latest = data.q(f'SELECT "제품코드","제품명","업체명","주성분명","적용일자","금액","급여구분" FROM {L} WHERE {W}', prm)
    keys = {ing_key(x) for x in latest["주성분명"].dropna() if ing_key(x)}
    origs = data.q(f"""SELECT "품목명" FROM {A} WHERE "신약구분" = '신약' AND list_contains(?, _k)""",
                   [sorted(keys)])["품목명"].dropna().astype(str).tolist() if keys else []
    pref = {p for p in (re.split(r"[0-9]", nospace(o))[0] for o in origs) if len(p) >= 2}
    is_o = latest["제품명"].map(lambda x: any(nospace(x).startswith(p) for p in pref)) if pref \
        else pd.Series(False, index=latest.index)
    ol = latest[is_o & (latest["급여구분"] == "급여")]
    st.markdown(f"**⭐ 오리지널(신약) {len(ol)}개** (전체 급여 {int(agg['ng'])}개 중)")
    if not ol.empty:
        oc = ol["제품코드"].tolist()
        if len(oc) <= 40:
            oh = data.q(f"""SELECT "제품명","적용일자","금액" FROM {P}
                            WHERE list_contains(?, "제품코드") AND "급여구분" = '급여' ORDER BY "적용일자" """, [oc])
            fo = go.Figure()
            for nm, gg in oh.groupby("제품명"):
                fo.add_trace(go.Scatter(x=gg["적용일자"], y=gg["금액"], mode="lines+markers", name=str(nm)[:24],
                                        line_shape="hv"))
            fo.update_layout(height=360, title="오리지널(신약) 상한금액 추이", yaxis=WON, legend=dict(orientation="h", y=-0.3))
            st.plotly_chart(fo, width="stretch")
        else:
            st.caption(f"오리지널 {len(oc)}개 — 그래프는 40개 이하로 좁혀야 표시됩니다.")
        st.dataframe(ol[["제품명", "업체명", "주성분명", "적용일자", "금액"]].sort_values("금액", ascending=False),
                     width="stretch", height=220, hide_index=True)
elif A and n > 2000:
    st.caption("⭐ 오리지널 분류·그래프는 필터를 좁히면(투여·분류·업체·제품/성분 검색) 표시됩니다.")

# 📅 연도별 상한금액
st.markdown("**📅 연도별 상한금액** (연도말 기준, 0=급여삭제, 빈칸=미등재)")
if n > 400:
    st.info(f"필터 결과 {n:,}개 — 400개 이하로 좁히면 연도별 표가 표시됩니다. (업체/제품·성분 검색 활용)")
else:
    lp = data.q(f"""SELECT "제품코드", year("적용일자") AS y, last("금액" ORDER BY "적용일자") AS 금액,
                           last("급여구분" ORDER BY "적용일자") AS 급여구분
                    FROM {P} WHERE list_contains((SELECT list("제품코드") FROM {L} WHERE {W}), "제품코드")
                    GROUP BY 1, 2""", prm)
    names = data.q(f'SELECT "제품코드","제품명" FROM {L} WHERE {W}', prm).set_index("제품코드")["제품명"].to_dict()
    if lp.empty:
        st.info("이력 없음")
    else:
        pv = lp.pivot(index="제품코드", columns="y", values="금액")
        tv = lp.pivot(index="제품코드", columns="y", values="급여구분")
        yrs = list(range(int(lp["y"].min()), 2027))
        pv = pv.reindex(columns=yrs).ffill(axis=1)
        tv = tv.reindex(columns=yrs).ffill(axis=1)
        pv = pv.where(tv != "삭제", 0)
        pv.columns = [str(c) for c in pv.columns]
        pv.insert(0, "제품명", [names.get(c, c) for c in pv.index])
        ym = pv.reset_index(drop=True)
        st.dataframe(ym.head(CAP), width="stretch", height=380)
        st.download_button("⬇️ 연도별 약가 CSV", ym.to_csv(index=False).encode("utf-8-sig"), file_name="연도별약가.csv")

with st.expander("📉 가격 인하폭 Top 20 / 급여중지(삭제) 목록"):
    drop = data.q(f"""WITH c AS (SELECT "제품코드" FROM {L} WHERE {W}),
                      h AS (SELECT "제품코드", first("금액" ORDER BY "적용일자") AS 최초가,
                                   last("금액" ORDER BY "적용일자") AS 최신가
                            FROM {P} WHERE "급여구분" = '급여' AND "제품코드" IN (SELECT "제품코드" FROM c)
                            GROUP BY 1)
                      SELECT l."제품명", l."업체명", h.최초가, h.최신가,
                             round((h.최신가 - h.최초가) / h.최초가 * 100, 1) AS "인하율%"
                      FROM h JOIN {L} l USING ("제품코드")
                      WHERE h.최초가 IS NOT NULL AND h.최초가 <> 0
                      ORDER BY "인하율%" LIMIT 20""", prm)
    st.dataframe(drop, width="stretch", height=240, hide_index=True)
    deleted = data.q(f"""SELECT "제품명","업체명","주성분명","적용일자" FROM {L}
                         WHERE {W} AND "급여구분" = '삭제' ORDER BY "적용일자" DESC LIMIT 30""", prm)
    st.dataframe(deleted, width="stretch", height=200, hide_index=True)
