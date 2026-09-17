"""규제 데이터 소스 로더 (허가·특허·임상) + 성분 조인 키.
구글시트 → scout_data/*.parquet 캐시를 읽는다. (build_scout_cache.py 로 갱신)
성분 조인은 영문 성분명의 '첫 단어'(예: Atorvastatin Calcium → ATORVASTATIN)를 키로 사용해
염·수화물 표기 차이를 흡수한다. 조합제는 첫 성분 기준(근사)."""
import re
from pathlib import Path
import pandas as pd
import streamlit as st

SD = Path(__file__).parent / "scout_data"

def ing_key(s):
    """영문 성분명 → 조인 키 (첫 알파벳 단어, 3자 이상 대문자)."""
    s = str(s or "")
    for tok in re.split(r"[^A-Za-z]+", s):
        if len(tok) >= 3:
            return tok.upper()
    return ""

_SALT_SUFFIX = sorted([
    "브롬화수소산염", "메탄술폰산염", "타르타르산염", "말레산염", "푸마르산염", "숙신산염",
    "베실산염", "메실산염", "토실산염", "글루콘산염", "구연산염", "시트르산염", "주석산염",
    "아세트산염", "초산염", "젖산염", "염산염", "황산염", "인산염", "질산염", "탄산염", "중탄산염",
    "이나트륨", "일나트륨", "칼슘", "칼륨", "나트륨", "마그네슘", "아연",
    "삼수화물", "이수화물", "일수화물", "반수화물", "사수화물", "수화물", "무수물", "무수", "염",
], key=len, reverse=True)

def ing_core(s):
    """한글 성분명 정규화 → 코어 토큰. '[코드]'·괄호·공백·염/수화물 접미어 제거, 조합제는 첫 성분."""
    s = re.sub(r"\[[^\]]*\]", "", str(s or ""))   # [M270797] 등 코드 제거
    s = re.sub(r"\([^)]*\)", "", s)                # (…) 제거
    s = re.sub(r"\s+", "", s)
    s = re.split(r"[/,]|및|\+", s)[0]              # 조합제 → 첫 성분
    changed = True
    while changed:                                 # 염+수화물 중첩 표기 반복 제거
        changed = False
        for suf in _SALT_SUFFIX:
            if s.endswith(suf) and len(s) > len(suf) + 1:
                s = s[: -len(suf)]; changed = True
                break
    return s.strip()

def available():
    return (SD / "approval.parquet").exists()

@st.cache_data(show_spinner="허가 로딩 중…")
def load_approval():
    df = pd.read_parquet(SD / "approval.parquet")
    df["_key"] = df["주성분(영문)"].map(ing_key)
    return df

@st.cache_data(show_spinner="특허 로딩 중…")
def load_patent():
    df = pd.read_parquet(SD / "patent.parquet")
    df["_key"] = df["INGR_ENG_NAME"].map(ing_key)
    # 영문 성분명이 비어 있는 행은 키가 없어 한 덩어리로 뭉친다.
    # 같은 한글 성분명(INGR_NAME)을 쓰는 다른 행 / 허가자료로 키를 채운다.
    if "INGR_NAME" in df.columns:
        kor = df["INGR_NAME"].astype(str).map(ing_core)
        k2 = {}
        for c, k in zip(kor, df["_key"]):
            if k and len(c) >= 2:
                k2.setdefault(c, k)
        try:
            ap = load_approval()
            for c, k in zip(ap.get("주성분", pd.Series(dtype=str)).astype(str).map(ing_core),
                            ap["_key"]):
                if k and len(c) >= 2:
                    k2.setdefault(c, k)
        except Exception:
            pass
        blank = df["_key"].astype(str).str.len() == 0
        if blank.any() and k2:
            df.loc[blank, "_key"] = kor[blank].map(lambda c: k2.get(c, ""))
    df["_exp"] = pd.to_datetime(df["DOMESTIC_END_DATE"], errors="coerce")
    return df

@st.cache_data(show_spinner="임상 로딩 중…")
def load_clinical():
    return pd.read_parquet(SD / "clinical.parquet")

@st.cache_data
def competition_by_key():
    """성분(키)별 허가 경쟁강도: 정상 허가 품목수·업체수, 생동 임상 수."""
    ap = load_approval()
    act = ap[ap["상태"] == "정상"]
    g = act.groupby("_key")
    comp = pd.DataFrame({"허가품목수": g.size(), "허가업체수": g["업체명"].nunique()})
    # 제네릭 개발활동: 생동 임상 건수
    cl = load_clinical().copy()
    cl["_key"] = cl["성분명"].map(ing_key)
    bio = cl[cl["CLINIC_STEP_NM"].astype(str).str.contains("생동", na=False)]
    comp["생동수"] = bio.groupby("_key").size()
    comp["생동수"] = comp["생동수"].fillna(0).astype(int)
    return comp.reset_index()

@st.cache_data
def patent_by_key():
    """성분(키)별 등록특허 만료: 물질특허 만료(최종), 전체 만료(최종), 등록특허수."""
    pt = load_patent()
    reg = pt[pt["DOMESTIC_PATENT_STATUS"].astype(str).str.contains("등록", na=False) & pt["_exp"].notna()]
    g = reg.groupby("_key")
    out = pd.DataFrame({
        "특허만료_전체": g["_exp"].max(),
        "등록특허수": g.size(),
    })
    mat = reg[reg["PATENT_GB_CODE"].astype(str).str.contains("물질", na=False)]
    out["특허만료_물질"] = mat.groupby("_key")["_exp"].max()
    use = reg[reg["PATENT_GB_CODE"].astype(str).str.contains("용도", na=False)]
    out["특허만료_용도"] = use.groupby("_key")["_exp"].max()
    return out.reset_index()

def resolve_keys(q):
    """검색어 → 성분 조인키 집합. 한글 성분명은 허가데이터(주성분 한글↔영문)로 영문키 변환."""
    q = str(q).strip()
    keys = set()
    k0 = ing_key(q)
    if re.search(r"[A-Za-z]", q) and k0:
        keys.add(k0)
    if available():
        ap = load_approval()
        hit = ap[ap["주성분"].astype(str).str.contains(q, na=False) |
                 ap["주성분(영문)"].astype(str).str.contains(q, case=False, na=False)]
        keys |= {ing_key(x) for x in hit["주성분(영문)"].dropna() if ing_key(x)}
    return {k for k in keys if k}

def original_products(keys):
    """성분키에 해당하는 오리지널(신약) 품목명 리스트 (허가 신약구분=='신약')."""
    if not available() or not keys:
        return []
    ap = load_approval()
    o = ap[(ap["신약구분"] == "신약") & (ap["_key"].isin(keys))]
    return o["품목명"].dropna().astype(str).tolist()

# ── 약가 (심평원 통합본, Drive API 캐시) ─────────────────────────────────────
def price_available():
    return (SD / "price.parquet").exists()

@st.cache_data(show_spinner="약가 로딩 중…")
def load_price():
    df = pd.read_parquet(SD / "price.parquet")
    df["금액"] = pd.to_numeric(df["금액"], errors="coerce")
    df["적용일자"] = pd.to_datetime(df["적용일자"], errors="coerce")
    df["_key"] = df["주성분명"].map(ing_key)
    return df

@st.cache_data
def price_by_key():
    """성분(키)별 최신 급여 상한금액 중앙값 + 품목수 (약가·마진 신호용)."""
    p = load_price()
    act = p[p["급여구분"] == "급여"].sort_values("적용일자")
    latest = act.groupby("제품코드").tail(1)
    g = latest.groupby("_key")
    return pd.DataFrame({"약가중앙값": g["금액"].median(), "약가품목수": g.size()}).reset_index()

def nego_available():
    return (SD / "nego.parquet").exists()

@st.cache_data
def load_nego():
    return pd.read_parquet(SD / "nego.parquet")

def nego_years(prodname):
    """제품명이 NHIS '약가협상 완료' 목록에 있으면 해당 연도 리스트(공식). 접두 매칭."""
    if not nego_available():
        return []
    df = load_nego()
    # 데이터가 비었거나 필요한 컬럼(_nm/연도)이 없으면 안전하게 빈 결과
    if df is None or df.empty or "_nm" not in df.columns or "연도" not in df.columns:
        return []
    p = re.sub(r"\s+", "", str(prodname))
    if len(p) < 3:
        return []
    hit = df[df["_nm"].apply(lambda n: len(n) >= 3 and p.startswith(n))]
    return sorted({y for y in hit["연도"].dropna().astype(str).tolist() if y})

# ── 재심사(PMS) · DMF (구글시트, 서비스계정으로 읽음) ────────────────────────
REJDGE_SHEET_ID = "1UDZJamAl9UJnnNNgb-HfuSdLu3Cb_-ybVQvmuVqYfQE"
REJDGE_TAB = "재심사"
DMF_SHEET_ID = REJDGE_SHEET_ID  # 같은 스프레드시트로 가정(다르면 이 값만 교체)
DMF_XLSX_ID = "1Zi2IJmuCCFRWusctSWn42FE2fZ1CYLti"  # 업로드한 DMF 전체 목록 엑셀

def _sheet_values(sheet_id, tab):
    import bootstrap
    from googleapiclient.discovery import build
    creds = bootstrap._creds()
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return (sheets.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=tab,
                 valueRenderOption="FORMATTED_VALUE").execute().get("values", []))

def _values_to_df(vals):
    if not vals or len(vals) < 2:
        return pd.DataFrame()
    cols = [str(c).split("\n")[0].strip() for c in vals[0]]
    keep = [i for i, c in enumerate(cols) if c]
    cols = [cols[i] for i in keep]
    rows = [[(r[i] if i < len(r) else "") for i in keep] for r in vals[1:]]
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=6 * 3600, show_spinner="재심사 로딩 중…")
def load_rejdge():
    """재심사(PMS) 구글시트 탭을 읽어 DataFrame. 실패 시 빈 DF."""
    try:
        return _values_to_df(_sheet_values(REJDGE_SHEET_ID, REJDGE_TAB))
    except Exception:
        return pd.DataFrame()

def rejdge_available():
    try:
        return not load_rejdge().empty
    except Exception:
        return False

def _norm_dmf_cols(df):
    """DMF 자료의 다양한 컬럼명을 표준명으로 정규화(성분/업체/제조원/제조국/등록일/번호)."""
    ren, used = {}, set()
    for c in df.columns:
        cs = str(c).strip(); u = cs.upper()
        if ("성분" in cs or "원료의약품" in cs or "원료명" in cs or "INGR" in u) and "INGR_KOR_NAME" not in used:
            ren[c] = "INGR_KOR_NAME"; used.add("INGR_KOR_NAME")
        elif ("제조원" in cs or "제조소" in cs or "원제조" in cs or "MNFCTR" in u) and "MNFCTR_NAME" not in used:
            ren[c] = "MNFCTR_NAME"; used.add("MNFCTR_NAME")
        elif ("제조국" in cs or "국가" in cs or "원산지" in cs or "COUNTRY" in u) and "MANUF_COUNTRY_CODE_NM" not in used:
            ren[c] = "MANUF_COUNTRY_CODE_NM"; used.add("MANUF_COUNTRY_CODE_NM")
        elif ("업소" in cs or "업체" in cs or "수입" in cs or "신고" in cs or "ENTP" in u) and "ENTP_NAME" not in used:
            ren[c] = "ENTP_NAME"; used.add("ENTP_NAME")
        elif ("등록일" in cs or "일자" in cs or ("DATE" in u)) and "DMF_PERMIT_DATE" not in used:
            ren[c] = "DMF_PERMIT_DATE"; used.add("DMF_PERMIT_DATE")
        elif ("등록번호" in cs or "공고번호" in cs or ("번호" in cs) or ("DMF" in u)) and "DMF_PERMIT_NO" not in used:
            ren[c] = "DMF_PERMIT_NO"; used.add("DMF_PERMIT_NO")
    return df.rename(columns=ren)

def _load_dmf_sheet():
    """구글시트의 DMF 탭(API 수집분) 자동 탐지해 읽음."""
    import bootstrap
    from googleapiclient.discovery import build
    creds = bootstrap._creds()
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    meta = (sheets.spreadsheets().get(spreadsheetId=DMF_SHEET_ID,
            fields="sheets.properties(title)").execute())
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    cand = [t for t in titles if not t.startswith("_") and t != REJDGE_TAB]
    pick = next((t for t in cand if "DMF" in t.upper() or "원료" in t or "원약" in t), None)
    if pick is None:
        for t in cand:
            head = (sheets.spreadsheets().values()
                    .get(spreadsheetId=DMF_SHEET_ID, range=f"{t}!1:1").execute()
                    .get("values", [[]]))
            hdr = " ".join(str(c).upper() for c in (head[0] if head else []))
            if any(k in hdr for k in ("DMF", "INGR_KOR", "INGR_NAME", "원료", "제조원", "제조소", "성분")):
                pick = t; break
    if pick is None and len(cand) == 1:
        pick = cand[0]
    if pick is None:
        return pd.DataFrame()
    return _norm_dmf_cols(_values_to_df(_sheet_values(DMF_SHEET_ID, pick)))

def _load_dmf_xlsx():
    """업로드한 DMF 전체 목록 엑셀을 서비스계정으로 읽어 정규화."""
    import bootstrap
    from googleapiclient.discovery import build
    creds = bootstrap._creds()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    df = pd.read_excel(bootstrap._download(drive, DMF_XLSX_ID), dtype=str).fillna("")
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
    df = df[[c for c in df.columns if c and not c.startswith("Unnamed")]]
    return _norm_dmf_cols(df)

@st.cache_data(ttl=6 * 3600, show_spinner="DMF 로딩 중…")
def load_dmf():
    """DMF = 구글시트(API 수집분) + 업로드 엑셀(전체 목록)을 합쳐서 반환. 실패분은 건너뜀."""
    frames = []
    for _fn in (_load_dmf_sheet, _load_dmf_xlsx):
        try:
            d = _fn()
            if d is not None and not d.empty:
                frames.append(d)
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    # 성분·업체·번호 기준 중복 제거(가능한 컬럼만)
    keys = [c for c in ["INGR_KOR_NAME", "ENTP_NAME", "DMF_PERMIT_NO"] if c in out.columns]
    if keys:
        out = out.drop_duplicates(subset=keys)
    else:
        out = out.drop_duplicates()
    return out.reset_index(drop=True)

def dmf_available():
    try:
        return not load_dmf().empty
    except Exception:
        return False

def _dmf_ing_col(df):
    # 영문/한글 성분 컬럼 폭넓게 인식
    cand = ["INGR_KOR_NAME", "INGR_KOR_NA", "INGR_NAME", "INGR_KOR", "성분명", "주성분", "원료성분", "성분"]
    hit = next((c for c in cand if c in df.columns), None)
    if hit:
        return hit
    # 컬럼명에 '성분'이 포함된 첫 컬럼
    return next((c for c in df.columns if "성분" in str(c) or "INGR" in str(c).upper()), None)

@st.cache_data
def pva_official():
    """공식 사용량-약가 연동(PVA) 품목 목록. scout_data/pva_official.csv 있을 때만.
    형식: 제품코드, 시행일(YYYY-MM-DD)[, 인하율]. 없으면 None → 앱은 해당 열 미표시."""
    f = SD / "pva_official.csv"
    if not f.exists():
        return None
    try:
        return pd.read_csv(f, dtype=str)
    except Exception:
        return None
