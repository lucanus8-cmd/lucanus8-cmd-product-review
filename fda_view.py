"""제품 제안 › FDA 비교 보기.

로봇(GitHub fda-daily, 매일 아침 6시쯤)이 FDA 공개 자료를 국내 허가(정상 품목)·식약처 임상 승인과
맞춰 본 결과를 이 저장소 'data' 가지 fda_site/ 에 작은 파일로 둔다. 이 화면은 그 파일만 읽는다
(약 6MB · 화면을 열 때만 받음). 다른 화면·다른 자료에는 손대지 않는다.

① 성분으로 찾기  ② 적응증으로 찾기  ③ 국내에 없는 약 목록
"""
import html
import io
import json
import os
import re
import urllib.request

import pandas as pd
import streamlit as st

BASE = os.environ.get("FDA_SITE_BASE",
                      "https://raw.githubusercontent.com/lucanus8-cmd/lucanus8-cmd-product-review/data/fda_site/")
MODES = ["① 성분으로 찾기", "② 적응증으로 찾기", "③ 국내에 없는 약 목록"]
ORDER = {"국내 성분 없음": 0, "일부 제형 없음": 1, "국내에도 있음": 2, "비교 제외 (수액·영양)": 3,
         "비교 못 함 (제형 미확인)": 4}
NEED = {"ing": ["주성분", "국내 상황", "한글명", "미국 상표", "미국 제형", "신청번호"],
        "prod": ["주성분", "신청번호", "구분", "상표", "회사", "함량", "제형", "첫 허가"],
        "ind": ["신청번호", "상표", "적응증"],
        "cmp": ["구분", "주성분", "미국 첫 허가", "미국 제형"]}

# 한글 병명 사전(약물 재창출과 같은 표)의 영문 이름 중 FDA 라벨에서 쓰는 말이 다른 것
LABEL_WORD = {
    "atopic eczema": "atopic dermatitis", "migraine disorder": "migraine", "parkinson disease": "parkinson",
    "alzheimer disease": "alzheimer", "crohn disease": "crohn", "behcet disease": "behcet",
    "sjogren syndrome": "sjogren", "hiv infection": "hiv", "liver cirrhosis": "cirrhosis",
    "peptic ulcer disease": "ulcer", "raynaud disease": "raynaud", "common cold": "common cold",
    "systemic lupus erythematosus": "lupus", "non-alcoholic steatohepatitis": "steatohepatitis",
    "age-related macular degeneration": "macular degeneration", "dry eye syndrome": "dry eye",
    "attention deficit hyperactivity disorder": "attention deficit", "autism spectrum disorder": "autis",
    "gastroesophageal reflux disease": "gastroesophageal reflux", "irritable bowel syndrome": "irritable bowel",
    "iron deficiency anemia": "iron deficiency", "gastric cancer": "gastric", "nicotine dependence": "smoking cessation",
    "type 2 diabetes mellitus": "type 2 diabetes", "type 1 diabetes mellitus": "type 1 diabetes",
    "hepatocellular carcinoma": "hepatocellular", "non-alcoholic fatty liver disease": "fatty liver",
    "major depressive disorder": "depressi", "anxiety disorder": "anxiety", "covid-19": "covid",
    "chronic obstructive pulmonary disease": "chronic obstructive pulmonary", "multiple myeloma": "myeloma",
    "benign prostatic hyperplasia": "prostatic hyperplasia", "angina pectoris": "angina",
}


# 같은 병을 FDA 라벨에서 다르게 쓰는 표현들 (사전의 영문 이름 → 라벨에서 찾을 말들)
_LIPID = ["dyslipidemia", "hyperlipidemia", "hypercholesterolemia", "hypertriglyceridemia", "ldl-c",
          "chylomicronemia", "lipoprotein"]
LABEL_TERMS = {
    "dyslipidemia": _LIPID, "hyperlipidemia": _LIPID, "hypercholesterolemia": _LIPID,
    "type 2 diabetes mellitus": ["type 2 diabetes", "glycemic control"],
    "type 1 diabetes mellitus": ["type 1 diabetes", "glycemic control"],
    "obesity": ["obesity", "weight management", "weight reduction", "chronic weight"],
    "gout": ["gout", "hyperuricemia"], "hyperuricemia": ["hyperuricemia", "gout"],
    "epilepsy": ["epilep", "seizure"], "major depressive disorder": ["depressi"],
    "chronic obstructive pulmonary disease": ["chronic obstructive pulmonary", "copd"],
    "attention deficit hyperactivity disorder": ["attention deficit", "adhd"],
    "chronic kidney disease": ["chronic kidney disease", "ckd"],
    "atopic eczema": ["atopic dermatitis"], "insomnia": ["insomnia", "sleep onset", "sleep maintenance"],
    "overactive bladder": ["overactive bladder", "urge urinary incontinence"],
    "erectile dysfunction": ["erectile dysfunction"], "osteoporosis": ["osteoporosis"],
}
# 질환군(③ 목록과 같은 분류)에서 찾을 말 (영문 이름·한글 병명 → 질환군 글자)
GROUP_KEY = {
    "dyslipidemia": "이상지질혈증", "hyperlipidemia": "이상지질혈증", "hypercholesterolemia": "이상지질혈증",
    "type 2 diabetes mellitus": "당뇨병", "type 1 diabetes mellitus": "당뇨병", "hypertension": "고혈압",
    "asthma": "천식", "chronic obstructive pulmonary disease": "copd", "epilepsy": "뇌전증", "gout": "통풍",
    "osteoporosis": "골다공증", "parkinson disease": "파킨슨", "tuberculosis": "결핵", "acne": "여드름",
    "constipation": "변비", "vomiting": "구토", "nausea": "구토",
}   # 편두통·치매·부정맥처럼 여러 병이 한 묶음인 질환군은 넣지 않는다 (진통제 전체 등이 섞임)


# ── ② 적응증 문장에서 찾을 말 (영문 병명 → (찾을 말, 빼고 볼 말)) ──
# 2026-11 검토(check_ind_headings.py): 66개 질병 1,632쌍, 무작위 표본 약 94% 정확.
_HTN_X = ["ocular hypertension", "pulmonary arterial hypertension", "pulmonary hypertension",
          "intracranial hypertension", "portal hypertension", "intraocular"]
_LIPIDS = ["hyperlipidemia", "hypercholesterolemia", "dyslipidemia", "hypertriglyceridemia", "ldl",
           "hyperlipoproteinemia", "triglyceride", "chylomicronemia", "lipoprotein", "sitosterolemia"]
_LIPID_X = ["medium chain triglyceride"]       # 트리헵타노인 같은 영양 공급용
_DM = ["glycemic control", "glycaemic control", "treatment of type 2 diabetes", "treatment of type 1 diabetes",
       "treatment of diabetes"]
_HIV = ["treatment of hiv", "treatment of human immunodeficiency", "other antiretroviral", "antiretroviral agents",
        "sexually acquired hiv", "hiv 1 pre exposure", "pre exposure prophylaxis"]
_DEP_X = ["respiratory depression", "cns depression", "nervous system depression", "depression of the central nervous",
          "myocardial depression", "bone marrow depression", "respiratory and or central nervous system depression",
          "opioid depression", "narcotic depression", "postanesthesia", "drug induced depression",
          "depression of respiration"]
_FLU_X = ["haemophilus influenzae", "h influenzae", "hemophilus influenzae", "influenzae", "haemophilus influenza",
          "hemophilus influenza"]
DISEASE_TERMS = {
    "hypertension": (["hypertension", "lower blood pressure", "lowering blood pressure"], _HTN_X),
    "dyslipidemia": (_LIPIDS, _LIPID_X), "hyperlipidemia": (_LIPIDS, _LIPID_X),
    "hypercholesterolemia": (_LIPIDS, _LIPID_X),
    "hypertriglyceridemia": (["hypertriglyceridemia", "triglyceride"], _LIPID_X),
    "type 2 diabetes mellitus": (_DM, []), "type 1 diabetes mellitus": (_DM, []), "diabetes mellitus": (_DM, []),
    "obesity": (["obesity", "weight management", "weight reduction", "weight loss"], []),
    "angina pectoris": (["angina"], []),
    "deep vein thrombosis": (["deep vein thrombosis", "venous thromboembolism"], []),
    "venous thromboembolism": (["deep vein thrombosis", "venous thromboembolism"], []),
    "copd": (["chronic obstructive pulmonary", "copd"], []),
    "chronic obstructive pulmonary disease": (["chronic obstructive pulmonary", "copd"], []),
    "gastroesophageal reflux disease": (["gastroesophageal reflux", "gerd", "erosive esophagitis"], []),
    "peptic ulcer disease": (["duodenal ulcer", "gastric ulcer", "peptic ulcer"], []),
    "crohn disease": (["crohn"], []), "irritable bowel syndrome": (["irritable bowel"], []),
    "vomiting": (["nausea and vomiting", "vomiting"], []), "nausea": (["nausea and vomiting", "nausea"], []),
    "hepatitis c": (["hepatitis c", "hcv"], []), "hepatitis b": (["hepatitis b", "hbv"], []),
    "hiv": (_HIV, []), "hiv infection": (_HIV, []), "aids": (_HIV, []),
    "herpes zoster": (["herpes zoster", "shingles"], []),
    "influenza": (["influenza"], _FLU_X),
    "gout": (["gout", "hyperuricemia"], []), "hyperuricemia": (["hyperuricemia", "gout"], []),
    "psoriasis": (["psoriasis", "psoriatic"], []), "atopic eczema": (["atopic dermatitis"], []),
    "atopic dermatitis": (["atopic dermatitis"], []),
    "acne": (["acne"], ["propionibacterium acnes", "cutibacterium acnes", "p acnes", "c acnes"]),
    "systemic lupus erythematosus": (["lupus"], []),
    "major depressive disorder": (["depressive disorder", "depression"], _DEP_X),
    "depression": (["depressive disorder", "depression"], _DEP_X),
    "bipolar disorder": (["bipolar"], []), "anxiety disorder": (["anxiety"], []),
    "attention deficit hyperactivity disorder": (["attention deficit"], []), "adhd": (["attention deficit"], []),
    "alzheimer disease": (["alzheimer"], []), "parkinson disease": (["parkinson"], []),
    "epilepsy": (["epilep", "seizure"], []), "migraine disorder": (["migraine"], []), "migraine": (["migraine"], []),
    "neuropathic pain": (["neuropathic pain", "postherpetic neuralgia", "diabetic peripheral neuropathy"], []),
    "benign prostatic hyperplasia": (["benign prostatic hyperplasia", "bph"], []),
    "glaucoma": (["glaucoma", "ocular hypertension"], []),
    "age-related macular degeneration": (["macular degeneration"], []),
    "anemia": (["anemia"], ["refractory anemia", "aplastic anemia and or liver failure"]),
    "chronic lymphocytic leukemia": (["chronic lymphocytic leukemia"], []),
}
_ABBR = {"hiv", "copd", "gerd", "bph", "hcv", "hbv", "ldl", "pah", "ckd", "adhd", "ibs", "oab"}  # 약어는 단어 단위로만
# 사전(약물 재창출 표)에 없거나 넓게 잡히는 한글 병명
KO_EXTRA = {"심부정맥혈전증": "deep vein thrombosis", "정맥혈전색전증": "venous thromboembolism",
            "요로감염": "urinary tract infection", "간세포암": "hepatocellular carcinoma",
            "에이즈": "hiv", "만성림프구성백혈병": "chronic lymphocytic leukemia", "당뇨병": "diabetes mellitus",
            "당뇨": "diabetes mellitus", "고중성지방혈증": "hypertriglyceridemia"}


def _en_norm(s):
    """로봇 build_fda_ind.py 의 norm 과 똑같아야 한다 (문장 '_n' 칸과 비교)."""
    s = str(s).lower().replace("’", "'")
    s = re.sub(r"'s\b", "", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def _get(name, timeout=30):
    if BASE.startswith("http"):
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "product-review-app"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    with open(os.path.join(BASE, name), "rb") as f:
        return f.read()


def _norm(s):
    return re.sub(r"[\s\-·,/()]+", "", str(s).lower())


def _lab_norm(s):
    """라벨 문구 찾기용: 소문자 · 's 제거 · 하이픈/기호를 빈칸으로."""
    s = str(s).lower().replace("’", "'")
    s = re.sub(r"'s\b", "", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9가-힣]+", " ", s)).strip()


@st.cache_resource(ttl=3 * 3600, max_entries=1, show_spinner="FDA 비교 자료 불러오는 중…")
def load():
    """로봇이 만든 표 4개 + 기준일. 못 읽으면 예외(캐시에 남지 않음)."""
    d = {}
    for n in ("ing", "prod", "ind", "cmp"):
        df = pd.read_parquet(io.BytesIO(_get(f"{n}.parquet"))).fillna("")
        miss = [c for c in NEED[n] if c not in df.columns]
        if miss:
            raise ValueError(f"{n} 표에 칸이 없음: {miss}")
        d[n] = df
    try:
        d["meta"] = json.loads(_get("meta.json", timeout=10).decode("utf-8"))
    except Exception:
        d["meta"] = {}
    # 적응증 문장 (로봇 build_fda_ind.py) — 없거나 이상하면 ② 는 예전 방식으로 찾는다
    try:
        ix = pd.read_parquet(io.BytesIO(_get("indx.parquet")), columns=["주성분", "문장", "출처", "_n"]).fillna("")
        if len(ix) >= 8000 and ix["주성분"].nunique() >= 1500:
            d["indx"] = ix
    except Exception:
        pass
    ing, prod, ind = d["ing"], d["prod"], d["ind"]
    # 한글명에 붙은 성분코드·괄호 떼기 ('[M264293]리바록사반(미분화)|…' → '리바록사반')
    ing["한글명"] = ing["한글명"].map(lambda s: "+".join(list(dict.fromkeys(
        x for x in (re.sub(r"\[[^\]]*\]|\([^)]*\)|\s+", "", y) for y in str(s).split("|")) if x))[:4]))
    # '국내 성분 없음' 은 국내 비교 표(cmp)에 있는 것만 — 미국 제형을 못 읽어 비교에서 빠진 성분은 따로 표시
    gap = set(d["cmp"].loc[d["cmp"]["구분"] == "국내 성분 없음", "주성분"])
    ing.loc[(ing["국내 상황"] == "국내 성분 없음") & ~ing["주성분"].isin(gap), "국내 상황"] = "비교 못 함 (제형 미확인)"
    for c in ("오리지널 허가 수", "제네릭 허가 수", "국내 품목 수", "국내 임상 승인 수", "국내 임상 진행 중"):
        if c in ing.columns:
            ing[c] = pd.to_numeric(ing[c], errors="coerce").fillna(0).astype(int)
    brands = prod.groupby("주성분")["상표"].agg(lambda x: " ".join(sorted(set(x))))
    ing["_s"] = (ing["주성분"] + " " + ing["한글명"] + " " + ing["미국 상표"] + " "
                 + ing["주성분"].map(brands).fillna("")).map(_norm)
    ing["_o"] = ing["국내 상황"].map(ORDER).fillna(9).astype(int)
    ind["_t"] = ind["적응증"].map(_lab_norm)
    d["app2ing"] = prod.drop_duplicates("신청번호").set_index("신청번호")["주성분"]
    return d


def _md(s):
    """마크다운 특수문자 이스케이프 ($ 는 수식, * _ 는 굵게/기울임이 되므로)."""
    return re.sub(r"([\\`*_{}\[\]<>()#+!|$~])", r"\\\1", str(s))


def _badge(stt):
    col = {"국내 성분 없음": ("#FDE7C8", "#7A3E00"), "일부 제형 없음": ("#E8EEF9", "#163E78"),
           "국내에도 있음": ("#E6F4EA", "#1F5E40")}.get(stt, ("#EEF0F3", "#45485A"))
    return (f"<span style='background:{col[0]};color:{col[1]};padding:2px 9px;border-radius:5px;"
            f"font-size:13px'>{html.escape(stt)}</span>")


def _pivot(name):
    """다른 보기의 줄을 누르면 ① 성분 화면으로 넘어간다 (위젯을 만들기 전에 값을 바꿔 둔다)."""
    st.session_state["fda_pivot"] = name
    st.session_state["fda_nsel"] = st.session_state.get("fda_nsel", 0) + 1     # 표 선택 초기화
    st.rerun()


def _selected_row(key):
    sel = ((st.session_state.get(key) or {}).get("selection") or {}).get("rows") or []
    return sel[0] if sel else None


# ───────────────────────── ① 성분으로 찾기 ─────────────────────────
def _view_ing(d):
    ing, prod, ind = d["ing"], d["prod"], d["ind"]
    q = st.text_input("성분·상표 (한글·영문)", key="fda_q",
                      placeholder="예: 리바록사반 · rivaroxaban · XARELTO")
    if not q.strip():
        st.caption(f"미국에서 판매 중인 성분 {len(ing):,}개 중에서 찾습니다. 국내에 없는 성분은 한글 이름을 몰라 "
                   "영문 성분명이나 미국 상표로 찾아 주세요.")
        return
    qn = _norm(q)
    hit = ing[ing["_s"].str.contains(qn, regex=False)].copy()
    if hit.empty:
        st.info("FDA 판매 중 성분에서 찾지 못했습니다. 영문 성분명이나 미국 상표로 다시 찾아 보세요 "
                "(국내에 없는 성분은 한글 이름이 없습니다).")
        return
    nm, kn = hit["주성분"].map(_norm), hit["한글명"].map(_norm)
    hit["_r"] = ((nm == qn) | (kn == qn)).map({True: 0, False: 2}) - nm.str.startswith(qn).astype(int)
    hit["_c"] = (hit["단일/복합"] != "단일").astype(int) if "단일/복합" in hit.columns else 0
    hit = hit.sort_values(["_r", "_c", "_o", "주성분"]).head(60)
    labels = [f"{r['주성분']}" + (f" ({r['한글명']})" if r["한글명"] else "") + f" — {r['국내 상황']}"
              + (f" · {r['미국 상표'][:40]}" if r["미국 상표"] else "") for _, r in hit.iterrows()]
    want = st.session_state.pop("fda_pick_name", None)
    if want is not None:
        st.session_state["fda_pick"] = next((lb for lb, n in zip(labels, hit["주성분"]) if n == want), labels[0])
    if st.session_state.get("fda_pick") not in labels:
        st.session_state["fda_pick"] = labels[0]
    pick = st.selectbox(f"성분 선택 ({len(hit)}개 찾음)", labels, key="fda_pick")
    r = hit.iloc[labels.index(pick)]
    name = r["주성분"]
    p = prod[prod["주성분"] == name]
    nda = p[p["구분"] != "ANDA"]
    anda = p[p["구분"] == "ANDA"].sort_values("첫 허가", ascending=False)
    kr_forms = [x for x in str(r.get("국내 제형", "")).split(", ") if x]
    us_forms = [x for x in str(r["미국 제형"]).split(", ") if x]
    miss = [x for x in str(r.get("국내에 없는 미국 제형", "")).split(", ") if x]

    st.markdown(f"#### {html.escape(name)}" + (f" <span style='color:#6b6f80;font-size:15px'>"
                f"{html.escape(r['한글명'])}</span>" if r["한글명"] else "") + " &nbsp;" + _badge(r["국내 상황"]),
                unsafe_allow_html=True)
    stt = r["국내 상황"]
    if stt == "국내 성분 없음":
        st.warning("국내 허가 정상 품목(전문·일반) 중에 이 성분이 없습니다. 염만 다른 것은 같은 성분으로 봅니다.")
    elif stt == "일부 제형 없음":
        st.info(f"국내에 성분은 있고, 미국 제형 중 **{_md(', '.join(miss))}** 이(가) 국내에 없습니다.")
    elif stt == "국내에도 있음":
        st.success("국내에도 같은 성분·같은 제형 묶음이 있습니다.")
    elif stt.startswith("비교 못 함"):
        st.caption("미국 제형을 읽지 못해 국내와 비교하지 못했습니다. 국내 허가 화면에서 직접 확인해 주세요.")
    else:
        st.caption("수액·영양·전해질은 국내 비교에서 뺐습니다.")
    m = st.columns(4)
    top = (nda["상표"].value_counts().index[0] if not nda.empty else (r["미국 상표"].split(", ")[0] or "—"))
    m[0].metric("미국 오리지널", top)
    m[0].caption(f"첫 허가 {r.get('미국 첫 허가', '') or '—'} · 오리지널 허가 {int(r.get('오리지널 허가 수', 0))}건")
    m[1].metric("미국 제네릭 (판매 중)", f"{int(r.get('제네릭 허가 수', 0))}건")
    if not anda.empty:
        a0 = anda.iloc[0]
        m[1].caption(f"가장 최근 {a0['첫 허가'] or '—'} · {a0['회사']} {a0['함량']} {a0['제형']}")
    m[2].metric("국내 허가 (정상)", f"{int(r.get('국내 품목 수', 0))}품목")
    m[2].caption(" · ".join(kr_forms) if kr_forms else "국내 품목 없음")
    m[3].metric("국내 임상 승인 (식약처)", f"{int(r.get('국내 임상 승인 수', 0))}건")
    m[3].caption(f"진행 중 {int(r.get('국내 임상 진행 중', 0))}건 (모집중·모집완료·승인완료)")

    rows = []
    for f in us_forms:
        pf = p[p["제형"] == f]
        pn, pa = pf[pf["구분"] != "ANDA"], pf[pf["구분"] == "ANDA"]
        br = []
        for b, g in pn.groupby("상표"):
            br.append(f"{b} {' · '.join(sorted(set(g['함량']))[:5])}")
        cos = sorted(set(pa["회사"]))
        us_txt = " / ".join(br[:4]) or "—"
        if cos:
            us_txt += f" / 제네릭 {len(cos)}개사 ({', '.join(cos[:6])}{' …' if len(cos) > 6 else ''})"
        if stt == "국내 성분 없음":
            jd = "국내 성분 없음"
        else:
            jd = "국내에 이 제형 없음" if f in miss or f not in kr_forms else "국내에도 있음"
        rows.append({"제형": f, "미국 (상표 · 함량 · 제네릭)": us_txt,
                     "국내": "있음" if f in kr_forms else "없음", "판정": jd})
    for f in kr_forms:
        if f not in us_forms:
            rows.append({"제형": f, "미국 (상표 · 함량 · 제네릭)": "—", "국내": "있음", "판정": "국내에만 있음"})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     column_config={"미국 (상표 · 함량 · 제네릭)": st.column_config.TextColumn(width="large")})

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**미국 적응증** (FDA 라벨 앞부분)")
        apps = [a for a in str(r["신청번호"]).split() if a]
        li = ind[ind["신청번호"].isin(apps)]
        if li.empty:
            st.caption("라벨 적응증 문구를 찾지 못했습니다.")
        for i, (_, x) in enumerate(li.iterrows()):
            box = st if i < 2 else st.expander(f"{x['상표']} ({x['신청번호']})")
            box.markdown(f"<div style='background:#F6F7F9;border-radius:8px;padding:8px 12px;font-size:13px;"
                         f"line-height:1.6'><b>{html.escape(str(x['상표']))}</b> "
                         f"<span style='color:#6b6f80'>({html.escape(str(x['신청번호']))}"
                         f"{' · 라벨 ' + html.escape(str(x.get('라벨 기준일', ''))) if x.get('라벨 기준일') else ''})</span>"
                         f"<br>{html.escape(str(x['적응증']))}</div>", unsafe_allow_html=True)
        if r.get("질환군"):
            st.caption(f"질환군: {r['질환군']}")
    with c2:
        st.markdown("**국내 임상시험 승인** (식약처 승인현황)")
        st.caption(r.get("국내 임상시험 승인", "") or "승인된 임상시험 없음")
        lst = [x for x in str(r.get("국내 임상 목록", "")).split(" | ") if x.strip()]
        if lst:
            with st.expander(f"임상 목록 ({len(lst)}건{' · 최근 12건' if int(r.get('국내 임상 승인 수', 0)) > 12 else ''})"):
                st.markdown("\n".join(f"- {_md(x)}" for x in lst))
        if r.get("국내 상황 메모"):
            st.caption("⚠️ " + r["국내 상황 메모"])
    with st.expander(f"미국 판매 중 제품 전체 ({len(p)}개)"):
        st.dataframe(p.sort_values(["구분", "첫 허가"]).drop(columns=["주성분"]), hide_index=True,
                     use_container_width=True, height=min(35 * len(p) + 38, 400))
    st.caption("국내 효능·효과와의 적응증 비교는 다음 단계입니다. 제형은 묶음(정제 = 필름코팅정 포함 등)으로 비교합니다.")


# ───────────────────────── ② 적응증으로 찾기 ─────────────────────────
def _disease_en(q):
    import repurpose
    k = re.sub(r"\s+", "", q.strip())
    return KO_EXTRA.get(k) or repurpose.ko_disease(q)


def _view_ind(d):
    if "indx" not in d:
        return _view_ind_old(d)
    ing, ix = d["ing"], d["indx"]
    q = st.text_input("적응증 (한글·영문)", key="fda_dq", placeholder="예: 폐동맥고혈압 · pulmonary arterial hypertension")
    if not q.strip():
        st.caption("FDA 라벨 '적응증(INDICATIONS AND USAGE)'의 소제목과 허가 문장에서만 찾습니다 — 사용 제한·주의·경고 "
                   "문장은 뺍니다. 한글 병명은 사전(약물 재창출과 같은 표)으로 영문으로 바꿔 찾습니다.")
        return
    en = _disease_en(q)
    if not en:
        st.info("사전에 없는 한글 병명입니다. 영문으로 넣어 주세요 (예: gout, pulmonary arterial hypertension).")
        return
    el = en.strip().lower()
    terms, excl = DISEASE_TERMS.get(el, ([LABEL_WORD.get(el, el)], []))
    terms = [x for x in dict.fromkeys(_en_norm(w) for w in terms) if len(x) >= 3]
    excl = [x for x in (_en_norm(e) for e in excl) if x]
    if not terms:
        st.info("검색어가 너무 짧습니다.")
        return
    s = ix["_n"]
    for e in excl:
        s = s.str.replace(e, " ", regex=False)
    m = pd.Series(False, index=ix.index)
    for w in terms:
        m |= (s.str.contains(r"\b" + re.escape(w) + r"\b", regex=True) if w in _ABBR
              else s.str.contains(w, regex=False))
    hit = ix[m]
    st.caption(f"→ FDA 라벨 적응증 문장에서 {', '.join(repr(x) for x in terms[:6])}{' …' if len(terms) > 6 else ''} 을(를) 찾음"
               + (f" (한글 '{q.strip()}' → 영문 '{en}')" if en != q.strip() else ""))
    if hit.empty:
        st.info("이 적응증으로 허가된 미국 판매 성분을 찾지 못했습니다. 다른 표현(영문)으로도 찾아 보세요.")
        return
    g = hit.groupby("주성분", sort=False).agg(근거=("문장", "first"), 출처=("출처", "first"),
                                             문장수=("문장", "size")).reset_index()
    t = g.merge(ing, on="주성분", how="left").fillna("")
    t["_o"] = t["국내 상황"].map(ORDER).fillna(9)
    t = t.sort_values(["_o", "주성분"]).reset_index(drop=True)
    cnt = t["국내 상황"].value_counts()
    mc = st.columns(4)
    mc[0].metric("이 적응증 미국 판매 성분", f"{len(t)}개")
    mc[1].metric("국내 성분 없음", f"{cnt.get('국내 성분 없음', 0)}개")
    mc[2].metric("국내에 일부 제형 없음", f"{cnt.get('일부 제형 없음', 0)}개")
    mc[3].metric("국내에도 있음", f"{cnt.get('국내에도 있음', 0)}개")
    ev = [x[:220] + ("…" if len(x) > 220 else "") + (f"  (+{n - 1}문장)" if n > 1 else "")
          for x, n in zip(t["근거"], t["문장수"])]
    show = pd.DataFrame({
        "성분": t["주성분"], "국내 상황": t["국내 상황"], "미국 상표": t["미국 상표"].str.slice(0, 60),
        "미국 제형": t["미국 제형"], "국내에 없는 미국 제형": t["국내에 없는 미국 제형"],
        "국내 제형": t["국내 제형"], "국내 임상 승인": t["국내 임상시험 승인"],
        "근거 (FDA 라벨 적응증 문장)": ev, "라벨": t["출처"]})
    key = f"fda_tab2_{st.session_state.get('fda_nsel', 0)}"
    i = _selected_row(key)
    if i is not None and i < len(show):
        _pivot(show.iloc[i]["성분"])
    st.dataframe(show, key=key, on_select="rerun", selection_mode="single-row", hide_index=True,
                 use_container_width=True, height=min(35 * len(show) + 38, 520),
                 column_config={"근거 (FDA 라벨 적응증 문장)": st.column_config.TextColumn(width="large"),
                                "국내 임상 승인": st.column_config.TextColumn(width="medium")})
    st.caption("줄 왼쪽 칸을 누르면 ① 성분 화면으로 넘어갑니다. 신약(NDA·BLA) 라벨, 신약이 없으면 제네릭 라벨로 봅니다. "
               "국내에 성분이 있어도 이 적응증으로 허가됐는지는 국내 효능·효과를 봐야 합니다 (다음 단계). "
               "라벨 문장으로 찾으므로 드물게 맞지 않는 약이 섞일 수 있어 '근거' 문장을 함께 보세요.")
    st.download_button("CSV 받기", show.to_csv(index=False).encode("utf-8-sig"), "fda_적응증.csv", key="fda_csv2")


def _view_ind_old(d):
    """적응증 문장 파일이 없을 때(로봇 첫 실행 전 등) 쓰는 예전 방식."""
    import repurpose
    ing, ind = d["ing"], d["ind"]
    q = st.text_input("적응증 (한글·영문)", key="fda_dq", placeholder="예: 폐동맥고혈압 · pulmonary arterial hypertension")
    if not q.strip():
        st.caption("FDA 라벨의 적응증 문구에서 찾습니다. 한글 병명은 사전(약물 재창출과 같은 표)으로 영문으로 바꿔 찾습니다.")
        return
    en = repurpose.ko_disease(q)
    if not en:
        st.info("사전에 없는 한글 병명입니다. 영문으로 넣어 주세요 (예: gout, pulmonary arterial hypertension).")
        return
    el = en.lower()
    terms = [_lab_norm(x) for x in LABEL_TERMS.get(el, [LABEL_WORD.get(el, en)])]
    terms = [x for x in dict.fromkeys(terms) if len(x) >= 3]
    if not terms:
        st.info("검색어가 너무 짧습니다.")
        return
    # ⓐ 라벨 적응증 문구에서 찾기 (같은 병의 다른 표현도 함께)
    hit = ind[ind["_t"].map(lambda x: any(w in x for w in terms))]
    hit = hit.assign(주성분=hit["신청번호"].map(d["app2ing"]).fillna(""))
    hit = hit[hit["주성분"] != ""]
    # ⓑ 질환군(ATC·문구 분류)으로 찾기 — ③ 목록의 '질환군'과 같은 기준
    gk = GROUP_KEY.get(el, "")
    gm = ing[ing["질환군"].map(_norm).str.contains(_norm(gk), regex=False)] if gk else ing.iloc[0:0]
    st.caption(f"→ FDA 라벨 적응증 문구에서 {', '.join(repr(x) for x in terms[:6])} 을(를) 찾고"
               + (f", 질환군에 '{gk}' 이(가) 든 성분을 더함" if gk else "")
               + (f" (한글 '{q.strip()}' → 영문 '{en}')" if en != q.strip() else ""))
    if hit.empty and gm.empty:
        st.info("이 적응증으로 판매 중인 미국 신약(NDA·BLA)을 찾지 못했습니다. 다른 표현(영문)으로도 찾아 보세요.")
        return

    def snip(t):
        t = str(t)
        n = _lab_norm(t)
        i = min([n.find(w) for w in terms if n.find(w) >= 0] or [-1])
        lo = max(0, int(i * len(t) / max(1, len(n))) - 60) if i >= 0 else 0
        return ("…" if lo else "") + t[lo:lo + 180] + "…"

    g = hit.groupby("주성분").agg(상표=("상표", lambda x: ", ".join(dict.fromkeys(x))[:60]),
                                 문구=("적응증", lambda x: snip(x.iloc[0]))).reset_index()
    extra = sorted(set(gm["주성분"]) - set(g["주성분"]))
    if extra:
        g = pd.concat([g, pd.DataFrame({"주성분": extra, "상표": "", "문구": ""})], ignore_index=True)
    g["찾은 근거"] = [("라벨 문구 + 질환군" if n in set(gm["주성분"]) else "라벨 문구") if c else "질환군"
                   for n, c in zip(g["주성분"], g["문구"] != "")]
    t = g.merge(ing, on="주성분", how="left", suffixes=("", "_i")).fillna("")
    t["상표"] = [a or b for a, b in zip(t["상표"], t["미국 상표"])]
    t["_o"] = t["국내 상황"].map(ORDER).fillna(9)
    t = t.sort_values(["_o", "주성분"]).reset_index(drop=True)
    cnt = t["국내 상황"].value_counts()
    m = st.columns(4)
    m[0].metric("이 적응증 미국 판매 성분", f"{len(t)}개")
    m[1].metric("국내 성분 없음", f"{cnt.get('국내 성분 없음', 0)}개")
    m[2].metric("국내에 일부 제형 없음", f"{cnt.get('일부 제형 없음', 0)}개")
    m[3].metric("국내에도 있음", f"{cnt.get('국내에도 있음', 0)}개")
    show = pd.DataFrame({
        "성분": t["주성분"], "국내 상황": t["국내 상황"], "미국 상표 (이 적응증)": t["상표"],
        "미국 제형": t["미국 제형"], "국내에 없는 미국 제형": t["국내에 없는 미국 제형"],
        "국내 제형": t["국내 제형"], "국내 임상 승인": t["국내 임상시험 승인"], "질환군": t["질환군"],
        "찾은 근거": t["찾은 근거"], "라벨 문구 (일부)": t["문구"]})
    key = f"fda_tab2_{st.session_state.get('fda_nsel', 0)}"
    i = _selected_row(key)
    if i is not None and i < len(show):
        _pivot(show.iloc[i]["성분"])
    st.dataframe(show, key=key, on_select="rerun", selection_mode="single-row", hide_index=True,
                 use_container_width=True, height=min(35 * len(show) + 38, 520),
                 column_config={"라벨 문구 (일부)": st.column_config.TextColumn(width="large"),
                                "국내 임상 승인": st.column_config.TextColumn(width="medium")})
    st.caption("줄 왼쪽 칸을 누르면 ① 성분 화면으로 넘어갑니다. 국내에 성분이 있어도 이 적응증으로 허가됐는지는 "
               "국내 효능·효과를 봐야 합니다 (다음 단계). 라벨 문구는 앞부분(600자)만 찾으며, 문구에 병명이 "
               "부작용·주의로만 나온 약이 섞일 수 있어 '찾은 근거'와 문구를 함께 보세요.")
    st.download_button("CSV 받기", show.to_csv(index=False).encode("utf-8-sig"), "fda_적응증.csv", key="fda_csv2")


# ───────────────────────── ③ 국내에 없는 약 목록 ─────────────────────────
def _view_list(d):
    c = d["cmp"].copy()
    n_ing = int((c["구분"] == "국내 성분 없음").sum())
    n_form = int((c["구분"] == "국내에 이 제형 없음").sum())
    a, b, e = st.columns([1.3, 1, 1])
    kind = a.radio("국내 상황", [f"국내 성분 없음 ({n_ing})", f"국내에 이 제형 없음 ({n_form})"],
                   horizontal=True, key="fda_l_kind")
    c = c[c["구분"] == ("국내 성분 없음" if kind.startswith("국내 성분") else "국내에 이 제형 없음")]
    grp = sorted(x for x in c["질환군"].unique() if x)
    g = b.selectbox("질환군", ["전체"] + grp + (["(분류 없음)"] if (c["질환군"] == "").any() else []), key="fda_l_grp")
    if g == "(분류 없음)":
        c = c[c["질환군"] == ""]
    elif g != "전체":
        c = c[c["질환군"] == g]
    yr = e.selectbox("미국 첫 허가", ["전체", "2024년 이후", "2020년 이후", "2015년 이후", "2010년 이후"], key="fda_l_yr")
    if yr != "전체":
        y0 = int(yr[:4])
        c = c[pd.to_numeric(c["미국 첫 허가"], errors="coerce").fillna(0) >= y0]
    f = st.columns([1, 1, 1, 2])
    if f[0].checkbox("단일제만", value=True, key="fda_l_single") and "단일/복합" in c.columns:
        c = c[c["단일/복합"] == "단일"]
    if f[1].checkbox("미국 제네릭 있음", key="fda_l_gen"):
        c = c[pd.to_numeric(c["제네릭 허가 수"], errors="coerce").fillna(0) > 0]
    if f[2].checkbox("국내 임상 승인 있음", key="fda_l_trial"):
        c = c[c["국내 임상시험 승인"] != ""]
    kw = f[3].text_input("성분·상표 검색", key="fda_l_kw", label_visibility="collapsed", placeholder="성분·상표 검색")
    if kw.strip():
        k = _norm(kw)
        c = c[(c["주성분"] + c["미국 상표"]).map(_norm).str.contains(k, regex=False)]
    c = c.sort_values(["미국 첫 허가", "주성분"], ascending=[False, True]).reset_index(drop=True)
    cols = ["주성분", "미국 첫 허가", "미국 상표", "미국 제형"] + (
        ["국내에 있는 제형"] if not kind.startswith("국내 성분") else []) + [
        "질환군", "오리지널 허가 수", "제네릭 허가 수", "국내 임상시험 승인", "국내 상황 메모", "미국 적응증"]
    show = c[[x for x in cols if x in c.columns]]
    st.caption(f"{len(show):,}줄 · 미국 첫 허가가 최근인 순")
    key = f"fda_tab3_{st.session_state.get('fda_nsel', 0)}"
    i = _selected_row(key)
    if i is not None and i < len(show):
        _pivot(show.iloc[i]["주성분"])
    st.dataframe(show, key=key, on_select="rerun", selection_mode="single-row", hide_index=True,
                 use_container_width=True, height=min(35 * len(show) + 38, 560),
                 column_config={"미국 적응증": st.column_config.TextColumn(width="large"),
                                "국내 임상시험 승인": st.column_config.TextColumn(width="medium")})
    st.caption("줄 왼쪽 칸을 누르면 ① 성분 화면에서 미국 제품·적응증·국내 임상을 자세히 봅니다. "
               "국내 비교는 국내 허가 정상 품목(전문·일반) 기준이며, 염만 다른 것은 같은 성분으로 봅니다.")
    st.download_button("CSV 받기", show.to_csv(index=False).encode("utf-8-sig"), "fda_국내에없는약.csv", key="fda_csv3")


def render():
    st.markdown("#### FDA 비교")
    try:
        d = load()
    except Exception as e:
        st.info("FDA 비교 자료를 아직 받지 못했습니다. 로봇이 매일 아침 6시쯤 만들어 둡니다 — 잠시 뒤 다시 열어 주세요.")
        st.caption(f"({type(e).__name__})")
        return
    mt = d.get("meta") or {}
    st.caption(f"FDA 허가 {mt.get('Drugs@FDA 기준일', '—')} · FDA 라벨 {mt.get('라벨 기준일', '—')} · "
               f"국내 허가·식약처 임상 승인과 맞춘 시각 {mt.get('만든 시각(한국)', '—')} — 로봇이 매일 아침 정리한 표를 읽습니다.")
    pv = st.session_state.pop("fda_pivot", None)
    if pv:
        st.session_state["fda_mode"] = MODES[0]
        st.session_state["fda_q"] = pv
        st.session_state["fda_pick_name"] = pv
    mode = st.radio("찾는 방법", MODES, horizontal=True, key="fda_mode", label_visibility="collapsed")
    try:
        if mode == MODES[0]:
            _view_ing(d)
        elif mode == MODES[1]:
            _view_ind(d)
        else:
            _view_list(d)
    except Exception as e:          # 이 보기 안의 문제로 사이트 전체가 멈추지 않게
        if type(e).__name__ in ("RerunException", "StopException"):
            raise
        st.warning("이 보기를 그리다 문제가 생겼습니다. 다른 검색어로 다시 해 보세요.")
        st.caption(f"({type(e).__name__}: {str(e)[:120]})")
