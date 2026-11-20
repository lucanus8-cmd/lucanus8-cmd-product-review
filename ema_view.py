"""제품 제안 › 해외 허가 비교 › 유럽 EMA.

로봇(build_ema_site.py)이 EMA 공식 의약품 자료를 국내 허가(정상 품목)와 성분으로 맞춘 결과를
이 저장소 'data' 가지 ema_site/ 에 작은 파일로 둔다 (약 1.2MB · 이 화면을 열 때만 받음).
이 화면은 그 파일만 읽는다. FDA 화면·다른 화면·다른 자료에는 손대지 않는다.

① 성분으로 찾기  ② 적응증으로 찾기  ③ 국내에 없는 약 목록  ④ 철회·거절 목록
"""
import io
import json
import os
import re
import urllib.request

import pandas as pd
import streamlit as st

BASE = os.environ.get("EMA_SITE_BASE",
                      "https://raw.githubusercontent.com/lucanus8-cmd/lucanus8-cmd-product-review/data/ema_site/")
MODES = ["① 성분으로 찾기", "② 적응증으로 찾기", "③ 국내에 없는 약 목록", "④ 철회·거절 목록"]
ORDER = {"국내 성분 없음": 0, "국내 취하·만료만": 1, "일부 성분만 있음": 2, "성분은 모두 있음 (복합제 없음)": 3,
         "국내 같은 복합제 있음": 4, "국내 성분 있음": 5, "확인 못 함": 6}
NEED = {"prod": ["성분", "제품", "상태", "허가일", "구분", "희귀", "국내 상황", "적응증", "주소"],
        "sent": ["row", "성분", "문장", "_n"]}

# 유럽 적응증은 병명을 바로 쓰거나 'carcinoma' 를 쓴다 (FDA 라벨과 표현이 다름) → 영문 병명별로 덧붙여 찾을 말
# 2026-11 검토(check_ema.py): 질병 10개 207개 성분 전부 확인 — 잘못 잡힌 것 없음
_DM = ["type 2 diabetes", "type 1 diabetes", "diabetes mellitus"]
EU_EXTRA = {
    "diabetes mellitus": _DM, "type 2 diabetes mellitus": _DM, "type 1 diabetes mellitus": _DM,
    "lung cancer": ["lung carcinoma", "nsclc", "sclc", "non small cell lung", "small cell lung"],
    "non-small cell lung cancer": ["lung carcinoma", "nsclc", "non small cell lung"],
    "colorectal cancer": ["colorectal carcinoma", "carcinoma of the colon", "colon or rectum", "colon cancer",
                          "rectal cancer", "mcrc"],
    "prostate cancer": ["prostate carcinoma", "prostate adenocarcinoma", "mcrpc", "mhspc", "castration resistant"],
    "breast cancer": ["breast carcinoma"],
    "obesity": ["management of weight", "obese patients"], "heart failure": ["cardiac failure"],
    "hypertension": ["blood pressure is not adequately controlled"],
}
EU_X = {"acne": ["acne inversa"], "dyslipidemia": ["apolipoprotein"], "hyperlipidemia": ["apolipoprotein"],
        "hypercholesterolemia": ["apolipoprotein"], "hypertension": ["arterialhypertens"]}
# 이런 말이 든 문장은 그 병의 근거로 쓰지 않는다 (합병증·동반 질환 문맥)
_DM_SX = ["kidney disease", "renal disease", "nephropathy", "hypoglyc", "atrial fibrillation", "macular", "retinopathy",
          "neuropathy", "foot ulcer", "skin ulcer", "cardiovascular morbidity", "target organ damage"]
EU_SENT_X = {"diabetes mellitus": _DM_SX, "type 2 diabetes mellitus": _DM_SX, "type 1 diabetes mellitus": _DM_SX,
             "heart failure": ["thromboembolism"], "breast cancer": ["skeletal"]}


def _get(name, timeout=30):
    if BASE.startswith("http"):
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "product-review-app"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    with open(os.path.join(BASE, name), "rb") as f:
        return f.read()


def _norm(s):
    return re.sub(r"[\s\-·,/()+]+", "", str(s).lower())


@st.cache_resource(ttl=3 * 3600, max_entries=1, show_spinner="EMA 비교 자료 불러오는 중…")
def load():
    """로봇이 만든 표 2개 + 기준일. 못 읽으면 예외(캐시에 남지 않음)."""
    d = {}
    for n in ("prod", "sent"):
        df = pd.read_parquet(io.BytesIO(_get(f"{n}.parquet"))).fillna("")
        miss = [c for c in NEED[n] if c not in df.columns]
        if miss:
            raise ValueError(f"{n} 표에 칸이 없음: {miss}")
        d[n] = df
    try:
        d["meta"] = json.loads(_get("meta.json", timeout=10).decode("utf-8"))
    except Exception:
        d["meta"] = {}
    p = d["prod"]
    p["_s"] = (p["성분"] + " " + p["INN"].astype(str) + " " + p["제품"]).map(_norm)
    p["_o"] = p["국내 상황"].map(ORDER).fillna(9).astype(int)
    p["_y"] = pd.to_numeric(p["허가일"].str.slice(0, 4), errors="coerce").fillna(0).astype(int)
    return d


def _badge_order(s):
    return s.map(ORDER).fillna(9)


def _pivot(name):
    """다른 보기의 줄을 누르면 ① 성분 화면으로 넘어간다 (위젯을 만들기 전에 값을 바꿔 둔다)."""
    st.session_state["ema_pivot"] = name
    st.session_state["ema_nsel"] = st.session_state.get("ema_nsel", 0) + 1
    st.rerun()


def _selected_row(key):
    sel = ((st.session_state.get(key) or {}).get("selection") or {}).get("rows") or []
    return sel[0] if sel else None


def _table(show, key, height_max=520, wide=("근거 (EMA 허가 적응증)",)):
    i = _selected_row(key)
    if i is not None and i < len(show):
        _pivot(show.iloc[i]["성분"])
    st.dataframe(show, key=key, on_select="rerun", selection_mode="single-row", hide_index=True,
                 use_container_width=True, height=min(35 * len(show) + 38, height_max),
                 column_config={c: st.column_config.TextColumn(width="large") for c in wide if c in show.columns})


def _group(p):
    """제품 여러 개 → 성분 한 줄."""
    g = p.sort_values("허가일").groupby("성분", sort=False).agg(
        국내=("국내 상황", "first"), 구분=("구분", lambda x: "/".join(sorted(set(x)))),
        첫허가=("허가일", lambda x: min([v for v in x if v] or [""])), 희귀=("희귀", lambda x: "예" if (x == "예").any() else ""),
        제품=("제품", lambda x: ", ".join(dict.fromkeys(x))), 분야=("치료 분야", "first"), 적응증=("적응증", "first"))
    return g.reset_index()


# ───────────────────────── ① 성분으로 찾기 ─────────────────────────
def _view_ing(d):
    p = d["prod"]
    q = st.text_input("성분·제품명 (영문)", key="ema_q", placeholder="예: ofatumumab · Kesimpta · semaglutide")
    if not q.strip():
        st.caption("EMA 중앙 허가 약의 성분명(INN)이나 유럽 제품명으로 찾습니다. 한글 성분명은 아직 안 됩니다.")
        return
    k = _norm(q)
    m = p[p["_s"].str.contains(k, regex=False)]
    if m.empty:
        st.info("EMA 자료에서 찾지 못했습니다. 영문 성분명(INN)으로 넣어 주세요. "
                "오래된 약은 유럽 각국 허가라 EMA 자료에 없을 수 있습니다.")
        return
    names = list(dict.fromkeys(m.sort_values(["_o", "성분"])["성분"]))
    exact = [n for n in names if _norm(n) == k]
    pick = st.selectbox(f"찾은 성분 {len(names)}개", names, index=names.index(exact[0]) if exact else 0, key="ema_pick")
    x = p[p["성분"] == pick].sort_values(["상태", "허가일"])
    a = x[x["상태"] == "허가"]
    c = st.columns(4)
    c[0].metric("국내 상황", x["국내 상황"].iloc[0])
    first = min([v for v in a["허가일"] if v] or ["—"])
    c[1].metric("유럽 첫 허가", first)
    c[2].metric("유럽 제품 (현재 허가)", f"{len(a)}개")
    c[3].metric("희귀의약품", "예" if (x["희귀"] == "예").any() else "아니오")
    st.markdown("##### 유럽 제품")
    st.dataframe(pd.DataFrame({"제품": x["제품"], "상태": x["상태"], "허가일": x["허가일"], "구분": x["구분"],
                               "희귀": x["희귀"], "조건부 허가": x["조건부"], "허가 회사": x["회사"],
                               "EMA 문서": x["주소"]}),
                 hide_index=True, use_container_width=True, height=min(35 * len(x) + 38, 320),
                 column_config={"EMA 문서": st.column_config.LinkColumn(display_text="열기")})
    st.markdown("##### EMA 허가 적응증 (원문)")
    for i, (_, r) in enumerate(x[x["적응증"] != ""].head(8).iterrows()):
        with st.expander(f"{r['제품']} · {r['상태']}", expanded=(i == 0)):
            st.write(r["적응증"])
    st.caption("국내 상황은 식약처 허가 정상 품목(매일 아침 갱신) 기준이며 염만 다른 것은 같은 성분으로 봅니다. "
               "국내 임상시험은 미국에도 있는 성분이면 '미국 FDA' 쪽 ① 성분 화면에서 볼 수 있습니다.")


# ───────────────────────── ② 적응증으로 찾기 ─────────────────────────
def _view_ind(d):
    import fda_view as fv            # 한글 병명 사전·FDA 병명 규칙을 같이 쓴다 (FDA 화면 코드는 바꾸지 않음)
    p, sn = d["prod"], d["sent"]
    a, b, c = st.columns([2, 1, 1])
    q = a.text_input("적응증 (한글·영문)", key="ema_dq", placeholder="예: 다발성경화증 · multiple sclerosis")
    only_gap = b.checkbox("국내 없음만 보기", key="ema_d_gap")
    no_gen = c.checkbox("제네릭·바이오시밀러 빼기", key="ema_d_gen")
    if not q.strip():
        st.caption("EMA 허가 적응증 문장에서만 찾습니다 — '5.1 참고' 안내, 사용하지 말아야 할 경우 같은 주의 문장, "
                   "위험인자 나열, 진단용 약(PET 영상제 등)은 뺍니다. 한글 병명은 사전(약물 재창출과 같은 표)으로 영문으로 바꿔 찾습니다.")
        return
    en = fv._disease_en(q)
    if not en:
        st.info("사전에 없는 한글 병명입니다. 영문으로 넣어 주세요 (예: gout, multiple sclerosis).")
        return
    el = en.strip().lower()
    terms, excl = fv.DISEASE_TERMS.get(el, ([fv.LABEL_WORD.get(el, el)], []))
    terms = [x for x in dict.fromkeys(fv._en_norm(w) for w in list(terms) + EU_EXTRA.get(el, [])) if len(x) >= 3]
    excl = [x for x in (fv._en_norm(e) for e in list(excl) + EU_X.get(el, [])) if x]
    sx = [fv._en_norm(w) for w in EU_SENT_X.get(el, [])]
    if not terms:
        st.info("검색어가 너무 짧습니다.")
        return
    s = sn["_n"]
    for e in excl:
        s = s.str.replace(e, " ", regex=False)
    m = pd.Series(False, index=sn.index)
    for w in terms:
        m |= (s.str.contains(r"\b" + re.escape(w) + r"\b", regex=True) if w in fv._ABBR
              else s.str.contains(w, regex=False))
    for w in sx:
        m &= ~sn["_n"].str.contains(w, regex=False)
    hit = sn[m]
    st.caption(f"→ EMA 허가 적응증 문장에서 {', '.join(repr(x) for x in terms[:6])}{' …' if len(terms) > 6 else ''} 을(를) 찾음"
               + (f" (한글 '{q.strip()}' → 영문 '{en}')" if en != q.strip() else ""))
    if hit.empty:
        st.info("이 적응증으로 EMA 허가된 약을 찾지 못했습니다. 다른 표현(영문)으로도 찾아 보세요.")
        return
    rows = p.loc[p.index.intersection(hit["row"])]
    ev = hit.drop_duplicates("row").set_index("row")["문장"]
    rows = rows.assign(근거=rows.index.map(ev))
    if no_gen:
        rows = rows[~rows["구분"].isin(["제네릭", "바이오시밀러"])]
    if rows.empty:
        st.info("조건에 맞는 약이 없습니다.")
        return
    g = _group(rows)
    g = g.merge(rows.groupby("성분")["근거"].first().rename("근거").reset_index(), on="성분", how="left")
    g["_o"] = _badge_order(g["국내"])
    g = g.sort_values(["_o", "성분"]).reset_index(drop=True)
    cnt = g["국내"].value_counts()
    mc = st.columns(4)
    mc[0].metric("이 적응증 EMA 허가 성분", f"{len(g)}개")
    mc[1].metric("국내 성분 없음", f"{cnt.get('국내 성분 없음', 0)}개")
    mc[2].metric("국내 취하·만료만", f"{cnt.get('국내 취하·만료만', 0)}개")
    mc[3].metric("국내에 있음", f"{int(cnt.get('국내 성분 있음', 0) + cnt.get('국내 같은 복합제 있음', 0))}개")
    if only_gap:
        g = g[g["국내"].isin(["국내 성분 없음", "국내 취하·만료만"])].reset_index(drop=True)
    show = pd.DataFrame({"성분": g["성분"], "국내 상황": g["국내"], "구분": g["구분"], "유럽 첫 허가": g["첫허가"],
                         "희귀": g["희귀"], "유럽 제품": g["제품"].str.slice(0, 60),
                         "근거 (EMA 허가 적응증)": g["근거"].str.slice(0, 260)})
    _table(show, f"ema_tab2_{st.session_state.get('ema_nsel', 0)}")
    st.caption("줄 왼쪽 칸을 누르면 ① 성분 화면으로 넘어갑니다. 국내에 성분이 있어도 이 적응증으로 허가됐는지는 "
               "국내 효능·효과를 봐야 합니다. 문장으로 찾으므로 드물게 맞지 않는 약이 섞일 수 있어 '근거'를 함께 보세요.")
    st.download_button("CSV 받기", show.to_csv(index=False).encode("utf-8-sig"), "ema_적응증.csv", key="ema_csv2")


# ───────────────────────── ③ 국내에 없는 약 목록 ─────────────────────────
def _view_list(d):
    p = d["prod"]
    p = p[(p["상태"] == "허가") & p["국내 상황"].isin(["국내 성분 없음", "국내 취하·만료만"])]
    a, b, c = st.columns([1.4, 1, 1])
    kind = a.radio("구분", ["신약", "백신", "세포·유전자", "전체 (제네릭·바이오시밀러 포함)"], horizontal=True, key="ema_l_kind")
    if kind.startswith("전체"):
        pass
    else:
        p = p[p["구분"] == kind]
    areas = pd.Series([t.strip() for v in p["치료 분야"] for t in str(v).split(";") if t.strip()]).value_counts()
    area = b.selectbox("치료 분야 (EMA 분류)", ["전체"] + list(areas.index[:80]), key="ema_l_area")
    if area != "전체":
        p = p[p["치료 분야"].map(lambda v: area in [t.strip() for t in str(v).split(";")])]
    yr = c.selectbox("유럽 첫 허가", ["전체", "2024년 이후", "2020년 이후", "2015년 이후", "2010년 이후"], key="ema_l_yr")
    if yr != "전체":
        p = p[p["_y"] >= int(yr[:4])]
    f = st.columns([1, 1, 2])
    if f[0].checkbox("희귀의약품만", key="ema_l_orph"):
        p = p[p["희귀"] == "예"]
    if f[1].checkbox("국내 취하·만료 포함", value=True, key="ema_l_past") is False:
        p = p[p["국내 상황"] == "국내 성분 없음"]
    kw = f[2].text_input("성분·제품 검색", key="ema_l_kw", label_visibility="collapsed", placeholder="성분·제품 검색")
    if kw.strip():
        p = p[p["_s"].str.contains(_norm(kw), regex=False)]
    if p.empty:
        st.info("조건에 맞는 약이 없습니다.")
        return
    g = _group(p).sort_values(["첫허가", "성분"], ascending=[False, True]).reset_index(drop=True)
    show = pd.DataFrame({"성분": g["성분"], "국내 상황": g["국내"], "유럽 제품": g["제품"].str.slice(0, 60),
                         "유럽 첫 허가": g["첫허가"], "구분": g["구분"], "희귀": g["희귀"],
                         "치료 분야": g["분야"].str.slice(0, 80), "EMA 허가 적응증": g["적응증"].str.slice(0, 260)})
    st.caption(f"{len(show):,}개 성분 · 유럽 첫 허가가 최근인 순")
    _table(show, f"ema_tab3_{st.session_state.get('ema_nsel', 0)}", 560, wide=("EMA 허가 적응증",))
    st.caption("EMA 중앙 허가 약 중 국내 허가 정상 품목에 같은 성분이 없는 것입니다. 줄 왼쪽 칸을 누르면 ① 성분 화면으로 넘어갑니다.")
    st.download_button("CSV 받기", show.to_csv(index=False).encode("utf-8-sig"), "ema_국내에없는약.csv", key="ema_csv3")


# ───────────────────────── ④ 철회·거절 목록 ─────────────────────────
def _view_withdrawn(d):
    p = d["prod"]
    p = p[~p["상태"].isin(["허가", "허가 권고 (결정 전)"])]
    cnt = p["상태"].value_counts()
    mc = st.columns(3)
    mc[0].metric("허가 후 철회", f"{cnt.get('허가 후 철회', 0)}건")
    mc[1].metric("허가 신청 철회", f"{cnt.get('허가 신청 철회', 0)}건")
    mc[2].metric("허가 거절", f"{cnt.get('허가 거절', 0)}건")
    a, b = st.columns([1, 1])
    stt = a.selectbox("상태", ["전체"] + list(cnt.index), key="ema_w_st")
    if stt != "전체":
        p = p[p["상태"] == stt]
    if b.checkbox("국내에 같은 성분 있는 것만", key="ema_w_kr"):
        p = p[p["국내 상황"].isin(["국내 성분 있음", "국내 같은 복합제 있음"])]
    if p.empty:
        st.info("조건에 맞는 약이 없습니다.")
        return
    p = p.sort_values(["상태 날짜", "성분"], ascending=[False, True]).reset_index(drop=True)
    show = pd.DataFrame({"성분": p["성분"], "유럽 제품": p["제품"], "상태": p["상태"], "날짜": p["상태 날짜"],
                         "국내 상황": p["국내 상황"], "신청했던 적응증": p["적응증"].str.slice(0, 220), "EMA 문서": p["주소"]})
    st.caption(f"{len(show):,}건 · 최근 날짜 순")
    i = _selected_row(f"ema_tab4_{st.session_state.get('ema_nsel', 0)}")
    if i is not None and i < len(show):
        _pivot(show.iloc[i]["성분"])
    st.dataframe(show, key=f"ema_tab4_{st.session_state.get('ema_nsel', 0)}", on_select="rerun",
                 selection_mode="single-row", hide_index=True, use_container_width=True,
                 height=min(35 * len(show) + 38, 560),
                 column_config={"신청했던 적응증": st.column_config.TextColumn(width="large"),
                                "EMA 문서": st.column_config.LinkColumn(display_text="열기")})
    st.caption("유럽에서 거절·철회된 약입니다. 철회 이유(안전성·상업적 이유 등)는 EMA 문서에서 확인하세요.")
    st.download_button("CSV 받기", show.to_csv(index=False).encode("utf-8-sig"), "ema_철회거절.csv", key="ema_csv4")


def render():
    st.markdown("#### EMA 비교")
    try:
        d = load()
    except Exception as e:
        st.info("EMA 비교 자료를 아직 받지 못했습니다. 로봇이 매일 아침 만들어 둡니다 — 잠시 뒤 다시 열어 주세요.")
        st.caption(f"({type(e).__name__})")
        return
    mt = d.get("meta") or {}
    st.caption(f"EMA 중앙 허가 약만 포함 — 오래된 약 일부는 유럽 각국 허가라 없을 수 있습니다 · "
               f"EMA 자료 {mt.get('ema_updated', '—')} · 국내 허가와 맞춘 시각 {mt.get('built', '—')}")
    pv = st.session_state.pop("ema_pivot", None)
    if pv:
        st.session_state["ema_mode"] = MODES[0]
        st.session_state["ema_q"] = pv
        st.session_state.pop("ema_pick", None)
    mode = st.radio("찾는 방법", MODES, horizontal=True, key="ema_mode", label_visibility="collapsed")
    try:
        if mode == MODES[0]:
            _view_ing(d)
        elif mode == MODES[1]:
            _view_ind(d)
        elif mode == MODES[2]:
            _view_list(d)
        else:
            _view_withdrawn(d)
    except Exception as e:          # 이 보기 안의 문제로 사이트 전체가 멈추지 않게
        if type(e).__name__ in ("RerunException", "StopException"):
            raise
        st.warning("이 보기를 그리다 문제가 생겼습니다. 다른 검색어로 다시 해 보세요.")
        st.caption(f"({type(e).__name__}: {str(e)[:120]})")
