"""원료사 동향(DMF) 계산 — 제품 제안 탭의 '🧪 원료사 동향' 보기에서 쓴다.

화면(streamlit)과 떨어진 순수 계산만 둔다. 입력은 DataFrame, 출력도 DataFrame 이라
사이트 없이도 시험할 수 있다(tests/check_dmf_trend.py).

용어
- 원료사 새 등록: 등록번호 끝에 (1),(A) 같은 꼬리가 없는 DMF. 새 원료(제조소)가 등록된 것.
- 추가 신청: 꼬리가 붙은 DMF. 이미 있는 원료에 신청인(대개 원료 상사)만 붙은 것.
- 오리지널사 본인 등록: 그 성분 신약을 가진 회사가 낸 DMF. 제네릭 원료 신호가 아니라 뺀다.
- 코드명 임상: 성분명 칸에 한글 성분명이 없고 숫자가 든 것(CKD-299, HUC3A-723 …).
"""
import re

import pandas as pd

import scout_sources as ss

ADD_RE = r"\([^)]*\)\s*$"
_HANGUL = r"[가-힣]"

# 질환 핵심어: (화면에 보일 이름, 찾을 글자들). 대상질환 문구는 띄어쓰기를 지우고 소문자로 맞춰 찾는다.
DISEASE_TERMS = [
    ("우울증", ["우울"]), ("조현병", ["조현", "정신분열"]), ("양극성장애", ["양극성", "조울"]),
    ("불안장애", ["불안장애", "공황"]), ("불면증", ["불면"]), ("ADHD", ["adhd", "주의력결핍"]),
    ("치매", ["치매", "알츠하이머"]), ("파킨슨병", ["파킨슨"]), ("뇌전증", ["뇌전증", "간질"]),
    ("편두통", ["편두통"]), ("신경병성통증", ["신경병", "신경통"]), ("섬유근육통", ["섬유근"]),
    ("고혈압", ["고혈압"]), ("이상지질혈증", ["이상지질", "고지혈", "고콜레스테롤", "콜레스테롤", "고중성지방"]),
    ("심부전", ["심부전"]), ("협심증", ["협심"]), ("심근경색", ["심근경색", "관상동맥"]),
    ("심방세동", ["심방세동"]), ("혈전색전증", ["혈전", "색전"]), ("뇌졸중", ["뇌졸중", "뇌경색"]),
    ("당뇨병", ["당뇨"]), ("비만", ["비만", "체중"]), ("갑상선", ["갑상선"]), ("골다공증", ["골다공"]),
    ("류마티스관절염", ["류마티스"]), ("골관절염", ["골관절", "퇴행성관절"]), ("통풍", ["통풍", "요산"]),
    ("건선", ["건선"]), ("아토피피부염", ["아토피"]), ("천식", ["천식"]), ("COPD", ["만성폐쇄", "copd"]),
    ("알레르기비염", ["비염"]), ("기침·가래", ["기침", "객담", "거담"]),
    ("위식도역류", ["위식도", "역류성식도", "역류질환"]), ("소화성궤양", ["위궤양", "십이지장궤양", "소화성궤양"]),
    ("기능성소화불량", ["소화불량"]), ("위염", ["위염"]), ("헬리코박터", ["헬리코박터"]),
    ("변비", ["변비"]), ("설사", ["설사"]), ("과민성장증후군", ["과민성장"]),
    ("염증성장질환", ["크론", "궤양성대장"]), ("B형간염", ["b형간염", "b형 간염"]), ("C형간염", ["c형간염"]),
    ("만성신장병", ["신부전", "신장병", "신질환", "신증"]), ("전립선비대증", ["전립선비대", "전립선 비대"]),
    ("과민성방광", ["과민성방광", "요실금", "빈뇨"]), ("발기부전", ["발기부전"]), ("탈모", ["탈모"]),
    ("녹내장", ["녹내장", "안압"]), ("안구건조증", ["안구건조"]), ("황반변성", ["황반"]),
    ("유방암", ["유방암", "에스트로겐", "프로게스테론수용체"]), ("폐암", ["폐암"]), ("대장암", ["대장암", "결장"]),
    ("전립선암", ["전립선암"]), ("혈액암", ["백혈병", "림프종", "골수종"]),
    ("HIV", ["hiv", "면역결핍"]), ("인플루엔자", ["인플루엔자", "독감"]), ("대상포진", ["대상포진"]),
    ("진균감염", ["진균"]), ("세균감염", ["세균", "폐렴", "요로감염"]), ("빈혈", ["빈혈"]),
    ("다발성경화증", ["다발성경화"]), ("폐경", ["폐경", "갱년기"]), ("자궁내막증", ["자궁내막"]),
    ("구역·구토", ["구토", "오심"]), ("두드러기", ["두드러기"]),
    ("호흡기(영문 분류)", ["respiratory"]), ("비뇨생식(영문 분류)", ["genitourinary"]),
    ("심혈관(영문 분류)", ["cardiovascular"]), ("중추신경(영문 분류)", ["nervoussystem", "cns"]),
]


# 사이트 공용 성분 코어(ing_core)가 못 떼는 염 이름 — 이 화면에서만 더 뗀다(다른 화면은 그대로)
_EXTRA_SALT = sorted(["벤조산", "숙신산", "말레산", "타르타르산", "푸마르산", "옥살산", "살리실산", "아디프산",
                      "헤미푸마르산", "헤미타르타르산", "나파디실산", "디메글루민", "메글루민", "트로메타민",
                      "올라민", "헤미에탄올레이트", "에탄올레이트", "브롬화", "염화", "요오드화", "모노하이드레이트",
                      "하이드레이트", "에스테르"], key=len, reverse=True)
_EXTRA_RE = "(?:" + "|".join(_EXTRA_SALT) + ")$"


def core_of(sr):
    """성분명 → 이 화면에서 묶는 성분 코어. 공용 코어에서 염 이름을 한 번 더 뗀다(너무 짧아지면 그대로)."""
    c = ss.ing_core_series(sr.astype(str))
    for _ in range(3):
        cut = c.str.replace(_EXTRA_RE, "", regex=True)
        c = cut.where(cut.str.len() >= 3, c)
    return c


def _norm_text(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def disease_terms(text):
    """대상질환 문구에서 질환 핵심어(화면 이름) 집합."""
    t = _norm_text(text)
    if not t or t in ("nan", "none"):
        return frozenset()
    return frozenset(name for name, pats in DISEASE_TERMS if any(p in t for p in pats))


def _norm_co(s):
    s = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한책임회사|유한회사|\s+", "", str(s or ""))
    return s.lower()


def _to_dt(sr):
    a = pd.to_datetime(sr, format="%Y%m%d", errors="coerce")
    b = pd.to_datetime(sr, errors="coerce", format="mixed") if a.isna().any() else a
    return a.fillna(b)


# ── DMF ──────────────────────────────────────────────────────────────────────
def originator_names(ap):
    """성분 코어 → 그 성분 신약 품목을 가진 회사 이름(정규화) 집합."""
    need = {"신약구분", "주성분", "업체명"}
    if ap is None or ap.empty or not need.issubset(ap.columns):
        return {}
    new = ap[ap["신약구분"].astype(str).str.strip() == "신약"]
    if new.empty:
        return {}
    core = core_of(new["주성분"])
    out = {}
    for c, co in zip(core, new["업체명"]):
        if c:
            out.setdefault(c, set()).add(_norm_co(co))
    return out


def prepare_dmf(dmf, ap=None):
    """식약처 DMF 목록(사이트 load_dmf 결과) → 계산용 표. 식약처 최신 목록이 아니면(상태 칸 없음) 빈 표."""
    st_c = ss.dmf_status_col(dmf) if dmf is not None else None
    if dmf is None or dmf.empty or not st_c:
        return pd.DataFrame()
    ic = ss._dmf_ing_col(dmf)
    g = lambda c: dmf[c].astype(str) if c in dmf.columns else pd.Series("", index=dmf.index)
    core = core_of(g(ic))
    core = core.where(core.str.len() > 0, g(ic).str.strip())
    x = pd.DataFrame({
        "성분": core,
        "성분명": g(ic),
        "신청인": g("ENTP_NAME"),
        "제조소": g("MNFCTR_NAME"),
        "제조국": g("MANUF_COUNTRY_CODE_NM"),
        "등록번호": g("DMF_PERMIT_NO"),
        "대상": g("대상의약품"),
        "상태": g(st_c).str.strip(),
        "문서번호": g("문서번호"),
        "연계심사문서번호": g(ss.DMF_LINK_COL),
        "등록일": pd.to_datetime(g("DMF_PERMIT_DATE"), errors="coerce"),
    })
    x["추가"] = x["등록번호"].str.contains(ADD_RE, regex=True, na=False)
    x["_mfr"] = (x["제조소"].str.split(" / ").str[0].str.lower()
                 .str.replace(r"[^0-9a-z가-힣]", "", regex=True).str[:25])
    # 오리지널사 본인 등록: ① 그 성분 신약 회사와 이름이 같다 ② 신약 허가 전이라 허가자료에 없으면,
    # '한국…' 법인이 그 성분 첫 DMF 날짜에 낸 신물질 DMF(다국적사 한국법인의 신약 허가용)
    on = originator_names(ap)
    co = [_norm_co(v) for v in x["신청인"]]
    by_name = pd.Series([c in on.get(k, ()) for k, c in zip(x["성분"], co)], index=x.index, dtype=bool)
    first = x.groupby("성분")["등록일"].transform("min")
    by_first = (x["신청인"].str.replace(r"\(주\)|㈜|주식회사", "", regex=True).str.strip().str.startswith("한국")
                & (x["등록일"] == first) & x["대상"].str.contains("신물질", na=False))
    x["오리지널사"] = (by_name | by_first.astype(bool)).astype(bool)
    return x[x["성분"].str.len() > 0].reset_index(drop=True)


def trend(x, today, months=12, drop_orig=True):
    """성분별 원료사 동향. 반환: (표, 뺀 오리지널사 등록 수)."""
    if x is None or x.empty:
        return pd.DataFrame(), 0
    today = pd.Timestamp(today).normalize()
    w = pd.Timedelta(days=int(round(months * 30.44)))
    start, pstart = today - w, today - 2 * w
    rec_all = x[(x["등록일"] > start) & (x["등록일"] <= today + pd.Timedelta(days=1))]
    n_orig = int(rec_all["오리지널사"].sum())
    y = x[~x["오리지널사"]] if drop_orig else x
    rec = y[(y["등록일"] > start) & (y["등록일"] <= today + pd.Timedelta(days=1))]
    prev = y[(y["등록일"] > pstart) & (y["등록일"] <= start)]
    before = set(zip(y.loc[y["등록일"] <= start, "성분"], y.loc[y["등록일"] <= start, "_mfr"]))
    fresh = pd.Series([(c, m) not in before for c, m in zip(rec["성분"], rec["_mfr"])],
                      index=rec.index, dtype=bool)
    new_m = rec[fresh & ~rec["추가"]]
    out = pd.DataFrame({
        "원료사_최근": rec[~rec["추가"]].groupby("성분").size(),
        "원료사_그전": prev[~prev["추가"]].groupby("성분").size(),
        "새제조소": new_m.groupby("성분")["_mfr"].nunique(),
        "추가_최근": rec[rec["추가"]].groupby("성분").size(),
        "추가_그전": prev[prev["추가"]].groupby("성분").size(),
        "정상DMF": y[y["상태"] == "정상"].groupby("성분").size(),
    }).fillna(0).astype(int)
    first_rec = rec.groupby("성분")["등록일"].min()
    out["첫등록_최근"] = first_rec.reindex(out.index)
    out["원료사_증감"] = out["원료사_최근"] - out["원료사_그전"]
    out["추가_증감"] = out["추가_최근"] - out["추가_그전"]
    return out.reset_index().rename(columns={"index": "성분"}), n_orig


def quarterly(x, core, since):
    """한 성분의 분기별 원료사 새 등록·추가 신청 건수(오리지널사 등록 제외)."""
    s = x[(x["성분"] == core) & ~x["오리지널사"] & (x["등록일"] >= since)]
    if s.empty:
        return pd.DataFrame(columns=["분기", "원료사", "추가"])
    q = s.assign(분기=s["등록일"].dt.to_period("Q").astype(str))
    t = q.groupby(["분기", "추가"]).size().unstack(fill_value=0)
    return pd.DataFrame({"분기": t.index,
                         "원료사": t.get(False, pd.Series(0, index=t.index)).values,
                         "추가": t.get(True, pd.Series(0, index=t.index)).values})


# ── 임상 ─────────────────────────────────────────────────────────────────────
def prepare_clinical(cl):
    """사이트 임상 자료 → 계산용 표(성분 코어·날짜·생동/1상·코드명·질환 핵심어)."""
    if cl is None or cl.empty or "성분명" not in cl.columns:
        return pd.DataFrame()
    g = lambda c: cl[c].astype(str) if c in cl.columns else pd.Series("", index=cl.index)
    name = g("성분명").str.strip()
    core = core_of(name)
    step = g("CLINIC_STEP_NM").str.strip()
    dis = g("TRGT_DISS_NM")
    x = pd.DataFrame({
        "성분": core, "성분명": name, "제품명": g("제품명"), "단계": step,
        "승인일": _to_dt(g("CLST_APRV_DT")), "대상질환": dis,
        "제목": g("CLNC_TEST_TITLE"), "개발사": g("원개발사"),
    })
    x["생동"] = step.str.contains("생동", na=False)
    x["1상"] = step.str.match(r"^1(b)?상", na=False)
    x["코드명"] = ~name.str.contains(_HANGUL, regex=True, na=False) & name.str.contains(r"\d", regex=True, na=False)
    x["질환"] = pd.Series([disease_terms(v) for v in dis], index=x.index, dtype=object)
    # 메모리: 글자 칸은 코드명 생동·1상(의심 목록에 보일 줄)에만 남긴다
    keep_txt = x["코드명"] & (x["생동"] | x["1상"])
    for c in ("대상질환", "제목", "제품명", "개발사"):
        x[c] = x[c].where(keep_txt, "")
    x["성분명"] = x["성분명"].where(x["코드명"], "")
    x = x[(x["성분"].str.len() > 0) | keep_txt]
    return x.reset_index(drop=True)


def ingredient_terms(cx, core, top=3, share=0.3):
    """그 성분 이름이 적힌 임상들의 대상질환에서 질환 핵심어를 뽑는다. 반환: (핵심어 목록, 근거 건수)."""
    s = cx[(cx["성분"] == core) & ~cx["코드명"]]
    s = s[pd.Series([len(v) > 0 for v in s["질환"]], index=s.index, dtype=bool)]
    if s.empty:
        return [], 0
    cnt = pd.Series([t for ts in s["질환"] for t in ts]).value_counts()
    keep = [t for t, n in cnt.items() if n / len(s) >= share][:top] or [cnt.index[0]]
    return keep, len(s)


def named_recent(cx, core, today, months=24):
    """그 성분 이름이 적힌 최근 생동·1상 건수."""
    t0 = pd.Timestamp(today) - pd.DateOffset(months=months)
    s = cx[(cx["성분"] == core) & ~cx["코드명"] & (cx["생동"] | cx["1상"]) & (cx["승인일"] >= t0)]
    return int(len(s))


def parse_terms(text):
    """사람이 적은 핵심어 글 → (표준 핵심어 집합, 글자 그대로 찾을 낱말들)."""
    words = [w for w in (_norm_text(t) for t in re.split(r"[,;/·]+", str(text or ""))) if len(w) >= 2]
    canon = set(disease_terms(" ".join(words)))
    canon |= {name for name, _ in DISEASE_TERMS if _norm_text(name) in words}
    return canon, words


def suspects(cx, terms, since, until=None, raw=()):
    """코드명 생동·1상 중 질환 핵심어가 겹치는 것. 의심 정도와 단서를 붙인다.
    terms = 표준 핵심어, raw = 대상질환 문구에서 글자 그대로 찾을 낱말."""
    if cx is None or cx.empty or not (terms or raw):
        return pd.DataFrame()
    want = set(terms)
    raw = [r for r in raw if r]
    s = cx[cx["코드명"] & (cx["생동"] | cx["1상"]) & (cx["승인일"] >= pd.Timestamp(since))]
    if until is not None:
        s = s[s["승인일"] <= pd.Timestamp(until)]
    txt = [_norm_text(v) for v in s["대상질환"]]
    by_term = pd.Series([bool(want & ts) for ts in s["질환"]], index=s.index, dtype=bool)
    by_raw = pd.Series([any(r in t for r in raw) for t in txt] if raw else False, index=s.index, dtype=bool)
    s = s[by_term | by_raw]
    if s.empty:
        return pd.DataFrame()
    hit = [sorted(want & ts) or [r for r in raw if r in _norm_text(d)][:2]
           for ts, d in zip(s["질환"], s["대상질환"])]
    other = [sorted(ts - want) for ts in s["질환"]]
    rep = [bool(re.search("4기|반복", _norm_text(t))) for t in s["제목"]]

    def _clue(h, o, r):
        c = ["질환 일치(" + ", ".join(h) + ")"]
        if o:
            c.append("다른 질환도 적힘(" + ", ".join(o[:2]) + ")")
        if r:
            c.append("4기·반복 설계")
        return " · ".join(c)

    def _lvl(h, o):
        if len(h) >= 2 and not o:
            return "높음"
        return "중간" if not o else "낮음"

    out = pd.DataFrame({
        "승인일": s["승인일"].dt.date.astype("string"),
        "개발사": s["개발사"].replace({"nan": "", "None": ""}),
        "코드": s["성분명"],
        "단계": s["단계"],
        "대상질환": s["대상질환"].str.slice(0, 60),
        "단서": [_clue(h, o, r) for h, o, r in zip(hit, other, rep)],
        "의심 정도": [_lvl(h, o) for h, o in zip(hit, other)],
        "임상시험 제목": s["제목"].str.slice(0, 90),
    })
    order = {"높음": 0, "중간": 1, "낮음": 2}
    out["_o"] = out["의심 정도"].map(order)
    return out.sort_values(["_o", "승인일"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)


def judge(t, today):
    """표 전체에 단계를 붙인다."""
    if t is None or t.empty:
        return t
    return t.assign(단계=[stage(r, today) for r in t.to_dict("records")])


# ── 판정 ─────────────────────────────────────────────────────────────────────
def stage(row, today):
    """원료 진입기 / 개발 진행기 / 출시 임박 / 지나감 / (빈칸)."""
    exp = row.get("물질만료")
    yrs = None
    if exp is not None and pd.notna(exp):
        yrs = (pd.Timestamp(exp) - pd.Timestamp(today)).days / 365.25
    clin = int(row.get("성분생동1상", 0) or 0)
    sus = int(row.get("코드명의심", 0) or 0)
    up = row.get("원료사_증감", 0) > 0 or row.get("추가_증감", 0) > 0
    if row.get("원료사_증감", 0) < 0 and row.get("추가_증감", 0) <= 0 and (yrs is None or yrs < 0):
        return "지나감"
    if yrs is not None and yrs <= 1 and clin >= 2:
        return "출시 임박"
    if clin > 0 or sus > 0:
        return "개발 진행기"
    if up:
        return "원료 진입기"
    return ""
