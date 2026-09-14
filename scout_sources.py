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

# ── 재심사 (구글시트, Apps Script가 매일 05시 갱신) ──────────────────────────
REJDGE_SHEET_ID = "1UDZJamAl9UJnnNNgb-HfuSdLu3Cb_-ybVQvmuVqYfQE"
REJDGE_TAB = "재심사"

@st.cache_data(ttl=6 * 3600, show_spinner="재심사 로딩 중…")
def load_rejdge():
    """구글시트 '재심사' 탭을 서비스계정으로 읽어 DataFrame 반환.
    Apps Script가 매일 시트를 갱신하므로 TTL(6h) 캐시로 최신 반영. 실패 시 빈 DF."""
    try:
        import bootstrap
        from googleapiclient.discovery import build
        creds = bootstrap._creds()
        sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        vals = (sheets.spreadsheets().values()
                .get(spreadsheetId=REJDGE_SHEET_ID, range=REJDGE_TAB,
                     valueRenderOption="FORMATTED_VALUE").execute().get("values", []))
        if not vals or len(vals) < 2:
            return pd.DataFrame()
        cols = [str(c).split("\n")[0].strip() for c in vals[0]]
        # 빈 헤더 컬럼(갱신 메모 등) 제거
        keep = [i for i, c in enumerate(cols) if c]
        cols = [cols[i] for i in keep]
        rows = [[(r[i] if i < len(r) else "") for i in keep] for r in vals[1:]]
        return pd.DataFrame(rows, columns=cols)
    except Exception:
        return pd.DataFrame()

def rejdge_available():
    try:
        return not load_rejdge().empty
    except Exception:
        return False

# ── DMF 원료의약품 등록 (구글시트, 동일 스프레드시트의 DMF 탭 자동 탐지) ────────
DMF_SHEET_ID = REJDGE_SHEET_ID  # 재심사와 같은 스프레드시트로 가정(다르면 이 값만 교체)

@st.cache_data(ttl=6 * 3600, show_spinner="DMF 로딩 중…")
def load_dmf():
    """구글시트의 DMF 탭을 서비스계정으로 읽어 DataFrame 반환.
    탭 이름은 'DMF'/'원료' 포함 또는 헤더(INGR_KOR_NA/DMF_)로 자동 탐지. 실패 시 빈 DF."""
    try:
        import bootstrap
        from googleapiclient.discovery import build
        creds = bootstrap._creds()
        sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        meta = (sheets.spreadsheets().get(spreadsheetId=DMF_SHEET_ID,
                fields="sheets.properties(title)").execute())
        titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
        cand = [t for t in titles if not t.startswith("_") and t != REJDGE_TAB]
        # 1) 이름으로 탐지
        pick = next((t for t in cand if "DMF" in t.upper() or "원료" in t), None)
        # 2) 헤더로 탐지
        if pick is None:
            for t in cand:
                head = (sheets.spreadsheets().values()
                        .get(spreadsheetId=DMF_SHEET_ID, range=f"{t}!1:1").execute()
                        .get("values", [[]]))
                hdr = " ".join(str(c).upper() for c in (head[0] if head else []))
                if "DMF_" in hdr or "INGR_KOR_NA" in hdr:
                    pick = t; break
        if pick is None:
            return pd.DataFrame()
        vals = (sheets.spreadsheets().values()
                .get(spreadsheetId=DMF_SHEET_ID, range=pick,
                     valueRenderOption="FORMATTED_VALUE").execute().get("values", []))
        if not vals or len(vals) < 2:
            return pd.DataFrame()
        cols = [str(c).split("\n")[0].strip() for c in vals[0]]
        keep = [i for i, c in enumerate(cols) if c]
        cols = [cols[i] for i in keep]
        rows = [[(r[i] if i < len(r) else "") for i in keep] for r in vals[1:]]
        return pd.DataFrame(rows, columns=cols)
    except Exception:
        return pd.DataFrame()

def dmf_available():
    try:
        return not load_dmf().empty
    except Exception:
        return False

def _dmf_ing_col(df):
    """DMF 데이터의 성분(한글) 컬럼명 반환."""
    return next((c for c in ["INGR_KOR_NAME", "INGR_KOR_NA", "INGR_NAME", "INGR_KOR"]
                 if c in df.columns), None)

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
