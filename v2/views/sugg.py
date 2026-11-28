"""제품 제안 (v2). '조건 필터'는 지금 사이트와 같은 조건·같은 계산.
다른 점: 매출을 성분(제품)별로 묶는 집계를 DuckDB 로 파일에서 바로 해서, 매출 표를 메모리에 올리지 않는다.
④ ATC · ⑤ 질병명(효능·효과)은 접고 펴는 칸."""
import re

import numpy as np
import pandas as pd
import streamlit as st

import scout_sources as ss
from core import data
from core.keys import kor2engkey

TODAY = pd.Timestamp.today().normalize()
ss._ensure = lambda *a, **k: None


def cagr_matrix(mat, labels, min_base=1e8):
    V = np.nan_to_num(np.asarray(mat, dtype="float64"), nan=0.0)
    if V.size == 0:
        return np.full(len(V), np.nan)
    years = np.array([int(re.search(r"(\d{4})", str(c)).group(1)) for c in labels])
    pos, big = V > 0, V >= min_base
    has_big, npos = big.any(axis=1), pos.sum(axis=1)
    si = np.where(has_big, big.argmax(axis=1), 0)
    ei = V.shape[1] - 1 - pos[:, ::-1].argmax(axis=1)
    r = np.arange(V.shape[0])
    sv, ev = V[r, si], V[r, ei]
    n = years[ei] - years[si]
    ok = has_big & (npos >= 2) & (n > 0) & (ev > 0) & (sv > 0)
    out = np.full(V.shape[0], np.nan)
    out[ok] = ((ev[ok] / sv[ok]) ** (1.0 / n[ok]) - 1.0) * 100
    return out


def _atc_levels(cols, src):
    by_lv = {}
    for c in cols:
        mt = re.match(r"^ATC\s*(\d)", str(c))
        if mt:
            by_lv.setdefault(int(mt.group(1)), []).append(c)

    def rank(c):
        u = str(c).upper()
        return 0 if "한글" in u else 1 if "DESC" in u else 3 if "CODE" in u else 2
    lv = [sorted(v, key=rank)[0] for k, v in sorted(by_lv.items()) if k <= 4]
    if not lv and src == "UBIST" and "ATC" in cols:
        lv = ["ATC"]
    return lv


@st.cache_resource(show_spinner="매출 집계 중…", max_entries=8)
def _agg(src, by, _mt):
    """성분(또는 제품)별 연도 합계·시장규모·CAGR·제조사 수·품목 수 + ATC 단계별 첫 값 (작은 표)."""
    S, cfg, yr = data.sales(src)
    lv = _atc_levels(data.cols(src.lower()), src)
    sums = ", ".join(f'sum(TRY_CAST("{y}" AS DOUBLE)) AS "{y}"' for y in yr)
    atc = "".join(f', first(CAST("{c}" AS VARCHAR) ORDER BY file_row_number) FILTER (WHERE trim(coalesce(CAST("{c}" AS VARCHAR), \'\')) NOT IN (\'\', \'nan\', \'None\')) AS "{c}"'
                  for c in lv)
    m = data.q(f'''SELECT "{by}" AS _n, {sums}, count(DISTINCT "{cfg["maker"]}") AS 제조사수, count(*) AS 품목수 {atc}
                   FROM {S} WHERE "{by}" IS NOT NULL GROUP BY 1''')
    m[yr] = m[yr].fillna(0)
    m["시장규모"] = m[yr[-1]]
    m["CAGR"] = cagr_matrix(m[yr].to_numpy(), yr)
    p2i = {}
    if by == cfg["prod"]:
        r = data.q(f'''SELECT "{cfg["prod"]}" AS p, first(CAST("{cfg["ing"]}" AS VARCHAR) ORDER BY file_row_number)
                       FILTER (WHERE trim(coalesce(CAST("{cfg["ing"]}" AS VARCHAR), '')) NOT IN ('', 'nan')) AS i
                       FROM {S} GROUP BY 1''')
        p2i = dict(zip(r["p"], r["i"].fillna("")))
    return m, yr, lv, p2i


@st.cache_resource(show_spinner=False, max_entries=2)
def _dmf_cols(_n):
    """성분키 → (정상 DMF, 원료사 최근 12개월, 그 전 12개월). 지금 사이트 원료사 동향과 같은 계산."""
    import dmf_trend as dt
    try:
        x = dt.prepare_dmf(ss.load_dmf(), ss.load_slim("approval", ["신약구분", "주성분", "업체명"]))
        t, _ = dt.trend(x, TODAY, 12, True)
    except Exception:
        return None
    if t is None or t.empty:
        return None
    c2k, _ = kor2engkey()
    items = [(kk, v) for kk, v in c2k.items() if len(kk) >= 3]

    def key(c):
        k = c2k.get(c) or c2k.get(ss.ing_core(c))
        if not k and len(c) >= 3:
            k = next((v for kk, v in items if kk.startswith(c) or c.startswith(kk)), "")
        return k or ""
    t = t.assign(_key=[key(str(c or "")) for c in t["성분"]])
    t = t[t["_key"].str.len() > 0]
    return t.groupby("_key")[["정상DMF", "원료사_최근", "원료사_그전"]].sum()


# ══════════════════════════ 화면 ══════════════════════════
st.subheader("🎯 제품 제안")
a, b, c = st.columns([1, 1, 2])
src2 = a.radio("매출 자료원", ["IQVIA", "UBIST"], horizontal=True, key="s2")
unit = b.radio("분석 단위", ["성분별", "제품별"], horizontal=True, key="u2")
_VIEW_Q = {"dmf": "🧪 원료사 동향(DMF)", "rp": "🧬 약물 재창출", "ovs": "해외 허가 비교"}
if "sugg_view" not in st.session_state and st.query_params.get("view") in _VIEW_Q:   # 주소로 바로 열기 (?view=dmf)
    st.session_state["sugg_view"] = _VIEW_Q[st.query_params.get("view")]
view = c.radio("보기", ["🎯 조건 필터", "🧪 원료사 동향(DMF)", "🧬 약물 재창출", "해외 허가 비교"], horizontal=True, key="sugg_view")
if view == "해외 허가 비교":
    ag = st.radio("허가 기관", ["미국 FDA", "유럽 EMA"], horizontal=True, key="ovs_agency")
    if ag == "유럽 EMA":
        import ema_view
        ema_view.render()
    else:
        import fda_view
        fda_view.render()
    st.stop()
if view.startswith("🧬"):
    # 약물 재창출은 매출 자료 없이도 돈다(매출은 있을 때만 덧붙인다)
    data.ensure("approval")
    from views import repurpose_mod
    repurpose_mod._repurpose_view(src2)
    st.stop()
if view.startswith("🧪") and unit != "성분별":
    st.caption("원료사 동향은 성분 단위로 봅니다 — 분석 단위와 상관없이 성분별 매출을 씁니다.")
    unit = "성분별"

S, cfg, yr = data.sales(src2)
if S is None or not yr:
    st.info(f"{src2} 매출 자료가 아직 준비되지 않았습니다 — 제품 제안은 매출 자료가 있어야 동작합니다.")
    st.stop()
by = cfg["ing"] if unit == "성분별" else cfg["prod"]
m0, yr, atc_lv, p2i = _agg(src2, by, data._mtime(data.FILES[src2.lower()]))
m = m0.copy()
name = unit.replace("별", "")
m = m.rename(columns={"_n": name})

# 한글 성분/제품명 → 영문 성분키 (지금 사이트와 같은 순서)
c2k, b2k = kor2engkey()
nm = m[name]
k = ss.ing_key_series(nm)
blank = k.str.len() == 0
if blank.any():
    if unit == "성분별":
        k.loc[blank] = ss.ing_core_series(nm[blank]).map(c2k).fillna("")
    else:
        bb = (nm[blank].astype(str).str.replace(r"\s+", "", regex=True)
              .str.split(r"[0-9(\[]", regex=True).str[0].fillna(""))
        k.loc[blank] = bb.map(b2k).fillna("")
        still = (k.str.len() == 0) & blank
        if still.any() and p2i:
            i_ = nm[still].map(p2i).fillna("")
            ik = ss.ing_key_series(i_)
            ik = ik.where(ik.str.len() > 0, ss.ing_core_series(i_).map(c2k).fillna(""))
            k.loc[still] = ik
m["_key"] = k.fillna("")

if view.startswith("🧪"):
    # 원료사 동향은 특허를 따로(성분키별 만료일 작은 표) 붙이므로 아래 특허 병합을 건너뛴다(가볍게)
    for col in atc_lv:
        m[col] = m[col].fillna("").astype(str) if col in m.columns else ""
    if atc_lv:
        m["ATC"] = m[atc_lv[0]]
    data.ensure("clinical")
    from views import dmf_mod
    dmf_mod._dmf_view(m, name, unit, yr)
    st.stop()

HAS_REG = data.has("approval") and data.has("patent")
if HAS_REG:
    def _ingname(v):
        return p2i.get(v, v) if unit == "제품별" else v
    m["_solo"] = m[name].map(lambda v: ss.is_single_ing(_ingname(v)))

    def _pbk_for(solo):
        base = ss.patent_by_key(single_only=solo)
        if unit != "성분별":
            return base
        orig = ss.patent_by_key(single_only=solo, original_only=True).copy()
        _, _, basis = ss.original_items()

        def _mark(dfx, tag):
            for col in ("근거_물질", "근거_물질형태", "근거_물질기타", "근거_용도"):
                if col in dfx.columns:
                    dfx[col] = [f"{tag(kk)}{t}" if t else "" for kk, t in zip(dfx["_key"], dfx[col].fillna(""))]
            return dfx
        orig = _mark(orig, lambda kk: "" if basis.get(kk) == "신약" else "ⓘ최초허가 기준 · ")
        rest = _mark(base[~base["_key"].isin(basis)].copy(), lambda kk: "⚠오리지널 미확인 · ")
        return pd.concat([orig, rest], ignore_index=True)

    parts = []
    for solo in (True, False):
        sub = m[m["_solo"] == solo]
        if sub.empty:
            continue
        pbk = _pbk_for(solo)
        pbk = pbk[pbk["_key"].astype(str).str.len() > 0].drop_duplicates("_key")
        parts.append(sub.merge(pbk, on="_key", how="left"))
    m = pd.concat(parts, ignore_index=True) if parts else m.drop(columns=["_solo"])
for col in atc_lv:
    m[col] = m[col].fillna("").astype(str) if col in m.columns else ""
if atc_lv:
    m["ATC"] = m[atc_lv[0]]

st.markdown("**필터 조건**")
f1, f2, f3 = st.columns(3)
min_size = f1.number_input("① 최소 시장규모(억, 최신연도)", value=50, step=10, min_value=0)
min_cagr = f2.number_input("② 최소 성장 CAGR(%)", value=0.0, step=5.0)
cy = int(TODAY.year)
with f3:
    PT_KIND = {"물질": "물질", "물질(염·수화물·이성체)": "물질형태", "물질(기타)": "물질기타", "용도": "용도"}
    pt_sel = st.multiselect("③ 특허 만료 기준 유형", list(PT_KIND), default=["물질", "물질(기타)"] if HAS_REG else [],
                            help="물질 = 성분 자체 · 물질(염·수화물·이성체) = 다른 염·형태로 피할 수 있는 특허 · "
                                 "물질(기타) = 항체 의약품 등에서는 성분 특허일 수 있음")
    ptypes = [PT_KIND[t] for t in pt_sel]
    sel_year = st.selectbox("이 연도까지 특허 만료", list(range(cy, cy + 16)), index=5,
                            help="선택한 유형 특허가 이 연도까지 만료(또는 없음)인 후보만")

# ④ ATC — 접어 둔다
atc_on = sum(1 for kk, v in st.session_state.items() if str(kk).startswith("sugg_atc_") and v)
atc_box = st.expander("④ ATC 계열로 좁히기 (선택)" + (f" · {atc_on}단계 고름" if atc_on else ""), expanded=bool(atc_on))
atc_pick = {}
if atc_lv:
    atc_box.caption("1단계부터 차례로 좁힙니다. 비우면 전체.")
    ac = atc_box.columns(len(atc_lv))
    pool = m
    for i, col in enumerate(atc_lv):
        opts = sorted({str(x).strip() for x in pool[col] if str(x).strip()})
        lvn = re.match(r"^ATC\s*(\d)", str(col))
        lab = ("ATC " + lvn.group(1) + "단계") if (len(atc_lv) > 1 and lvn) else "ATC"
        sel = ac[i].multiselect(lab, opts, default=[], key=f"sugg_atc_{i}_{abs(hash(tuple(opts))) % 10**8}",
                                help="비우면 전체" if i == 0 else "위 단계에서 고른 것 안의 항목만 나옵니다")
        sel = [x for x in sel if x in opts]
        if sel:
            atc_pick[col] = sel
            pool = pool[pool[col].astype(str).str.strip().isin(sel)]

# ⑤ 질병명(효능·효과) — 접어 둔다
ee_w = str(st.session_state.get("sugg_ee_words", "") or "").strip()
ee_box = st.expander("⑤ 질병명(효능·효과)으로 좁히기 (선택)" + (f" · {ee_w}" if ee_w else ""), expanded=bool(ee_w))
ee_on = False
try:
    import efficacy as ee
    ee_ok = ee.available()
except Exception:
    ee_ok = False
if not ee_ok:
    ee_box.info("효능·효과 자료를 아직 받지 못했습니다. 로봇이 만들어 두면 여기서 질병명으로 찾을 수 있습니다.")
else:
    e1, e2 = ee_box.columns(2)
    ee_words = e1.text_input("질병명 (쉼표로 여러 개 · 하나라도 들어가면)", key="sugg_ee_words",
                             placeholder="예: 당뇨 / 고지혈증, 이상지질혈증")
    ee_ex = e2.text_input("빼는 단어 (이 말이 든 문구로만 걸린 성분은 뺌)", key="sugg_ee_ex", placeholder="예: 폐동맥, 간질환")
    mt_ = ee.meta()
    ee_box.caption("식약처 허가 효능·효과 원문에서 찾습니다 (띄어쓰기 무시) · 단일제 품목 기준"
                   + (f" · 자료 기준일 {mt_.get('기준일')}" if mt_.get("기준일") else "")
                   + " · 문구 첫머리에 나오는 성분이 위로 옵니다")
    ee_on = bool(str(ee_words).strip())

only_dmf_up = (st.checkbox("⑥ 원료사 DMF 등록이 늘어난 성분만 (최근 12개월이 그 전 12개월보다 많음)",
                           value=False, key="sugg_dmf_up") if unit == "성분별" else False)

res = m[(m["시장규모"] >= min_size * 1e8) & (m["CAGR"].fillna(-1e9) >= min_cagr)].copy()
if HAS_REG and ptypes and not res.empty:
    def clear_by(row):
        for t in ptypes:
            exp = row.get(f"특허만료_{t}")
            if pd.notna(exp) and pd.Timestamp(exp).year > sel_year:
                return False
        return True
    res = res[res.apply(clear_by, axis=1).astype(bool)]
for col, sel in atc_pick.items():
    if col in res.columns:
        res = res[res[col].astype(str).str.strip().isin(sel)]
res = res.sort_values("시장규모", ascending=False)
ee_cols = []
if ee_on and "_key" in res.columns:
    r = ee.search(res["_key"].astype(str), ee_words, ee_ex)
    res = res[r["걸림"]].copy()
    r = r.loc[res.index]
    res["효능·효과 문구"] = r["위치"].map({"첫머리": "[첫머리] ", "본문": "[본문] "}).fillna("") + r["문구"]
    res["_ee_b"] = (r["위치"] != "첫머리").astype(int)
    res = res.sort_values(["_ee_b", "시장규모"], ascending=[True, False], kind="mergesort")
    ee_cols = ["효능·효과 문구"]
res["시장규모(억)"] = (res["시장규모"] / 1e8).round(1)
res["CAGR(%)"] = res["CAGR"].round(1)
dmf_cols = []
if unit == "성분별":
    mp = _dmf_cols(1)
    if mp is not None:
        one = ss.is_single_series(res[name].astype(str))
        for lab, col in (("DMF 정상", "정상DMF"), ("원료사 12개월", "원료사_최근"), ("원료사 그 전 12개월", "원료사_그전")):
            res[lab] = res["_key"].astype(str).map(mp[col]).fillna(0).where(one).astype("Int64")
        dmf_cols = ["DMF 정상", "원료사 12개월", "원료사 그 전 12개월"]
        if only_dmf_up:
            res = res[(res["원료사 12개월"] > res["원료사 그 전 12개월"]).fillna(False)]
PT_COLS = [("물질특허", "물질"), ("염·형태특허", "물질형태"), ("물질(기타)특허", "물질기타"), ("용도특허", "용도")]
if HAS_REG:
    for lab, kk in PT_COLS:
        res[f"{lab} 만료"] = pd.to_datetime(res.get(f"특허만료_{kk}")).dt.date.astype("string")
        res[f"{lab} 근거"] = res.get(f"근거_{kk}", pd.Series("", index=res.index)).fillna("")
atc_last = atc_lv[-1] if len(atc_lv) > 1 else None
if atc_last and atc_last in res.columns:
    res["ATC 상세"] = res[atc_last]
cols = ([name] + (["ATC"] if "ATC" in res.columns else []) + (["ATC 상세"] if "ATC 상세" in res.columns else [])
        + ["시장규모(억)", "CAGR(%)"] + dmf_cols + ([f"{lab} 만료" for lab, _ in PT_COLS] if HAS_REG else []))
src_cols = [f"{lab} 근거" for lab, _ in PT_COLS] if HAS_REG else []
st.markdown(f"#### ✅ 조건 충족 후보 {len(res):,}개")
show_src = st.checkbox("특허 근거 표시 (그 만료일이 어느 품목·유형의 특허에서 왔는지)", value=False, key="sugg_src") if HAS_REG else False
if ee_cols:
    if st.checkbox("효능·효과 문구 표시 (어느 문구 때문에 걸렸는지)", value=True, key="sugg_ee_show"):
        cols = cols[:1] + ee_cols + cols[1:]
vcols = cols + (src_cols if show_src else [])
st.dataframe(res[vcols].head(200), width="stretch", height=430, hide_index=True)
st.download_button("⬇️ CSV", res[cols + src_cols].to_csv(index=False).encode("utf-8-sig"), file_name=f"제품제안_{src2}_{unit}.csv")
st.caption("가중치 점수 없이, 큰 시장·고성장·특허만료·ATC 조건을 직접 필터링합니다. · ATC는 매출자료 기준 · "
           "특허 만료일=존속 중인 등록특허 중 가장 늦은 날짜(소멸·무효 제외). **식약처 등재 특허(제품특허)만** 봅니다. "
           "**단일제 후보는 단일제 특허만** 반영하고, 복합제 후보만 복합제 특허를 포함합니다. "
           "**성분별은 오리지널 품목의 특허 기준** — 1순위 신약구분=‘신약’, 없으면 그 성분 최초 허가 품목"
           "(근거에 ‘ⓘ최초허가 기준’ 표시). 둘 다 못 찾으면 전체 기준으로 표시하고 ‘⚠오리지널 미확인’으로 알립니다.")
