"""배포 서버에서 데이터 캐시를 '이미 주신 드라이브 소스'로부터 직접 생성한다.
- 로컬에 캐시가 있으면 아무것도 안 함(로컬 개발 그대로).
- 없으면 서비스계정으로: 매출(iqvia/ubist xlsx), 약가(통합 xlsx), 허가/특허/임상(구글시트),
  공단협상(NHIS 공개)에서 파싱하여 saved_data/*.pkl, scout_data/*.parquet 생성.
서비스계정 키: st.secrets["gcp_service_account"](배포) 또는 secrets/service-account.json(로컬).
추가 파일 업로드/파일ID 입력 불필요 — 소스 ID는 아래 상수로 고정.
"""
import io
import re
import urllib.request
from pathlib import Path
import pandas as pd
import streamlit as st

BASE = Path(__file__).parent
SD = BASE / "scout_data"
SAVED = BASE / "saved_data"

# 이미 공유해주신 드라이브 소스들
IQVIA_ID = "1AfN8A8XXJoulm6PPctjKWRsetGD5MSYt"
UBIST_ID = "1RsXTofGdZPVLRqClUlpa9JD0JOm67nPj"
PRICE_XLSX_ID = "1u9gfxs7NyuQebBn4WEOyBCUQ0zQvYqYf"
SHEETS = {
    "clinical": "1iFPo8V-JLFQEkFgkfAH2l8-FtUEia0on6Q8OjtPr6sE",
    "patent": "1tj75vy8KcJSmz6-rAqmPpLThNt7ZymuQfi12Vbzz1CE",
    "approval": "1x0lPMvsH0G-9cOdwi1kZy5yTAhAaz9N5BluK36iS8-Q",
}
SCOPES = ["https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/spreadsheets.readonly"]

def _creds():
    from google.oauth2 import service_account
    # 1) 배포: st.secrets["gcp_service_account"] 우선 사용
    raw = None
    try:
        raw = st.secrets["gcp_service_account"]
    except Exception:
        raw = None
    if raw is not None:
        info = dict(raw)
        # private_key가 TOML/따옴표 문제로 개행이 문자 그대로 "\n"으로 들어온 경우 복구
        pk = info.get("private_key", "")
        if "\\n" in pk and "\n" not in pk:
            info["private_key"] = pk.replace("\\n", "\n")
        try:
            return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        except Exception as e:
            raise RuntimeError(
                "Secrets의 [gcp_service_account]를 읽었지만 인증 정보 생성에 실패했습니다.\n"
                f"원인: {type(e).__name__}: {e}\n"
                f"들어있는 키 목록: {sorted(info.keys())}\n"
                "→ private_key 값(개행 \\n 포함)과 client_email/token_uri가 올바른지 확인하세요."
            ) from e
    # 2) 로컬: 파일 폴백
    path = BASE / "secrets" / "service-account.json"
    if not path.exists():
        raise RuntimeError(
            "서비스계정 자격증명을 찾을 수 없습니다.\n"
            "배포(Streamlit Cloud) 환경이면 Settings→Secrets에 TOML 형식으로 "
            "[gcp_service_account] 섹션을 넣어야 합니다. (JSON 형식이 아니라 key = \"value\" 형식)\n"
            f"로컬 개발이면 파일이 필요합니다: {path}"
        )
    return service_account.Credentials.from_service_account_file(str(path), scopes=SCOPES)

def _download(drive, fid):
    from googleapiclient.http import MediaIoBaseDownload
    req = drive.files().get_media(fileId=fid, supportsAllDrives=True)
    buf = io.BytesIO(); dl = MediaIoBaseDownload(buf, req, chunksize=10 * 1024 * 1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0); return buf

# ── 매출 파서 (app.py 로직 복제) ──────────────────────────────────────────
def _parse_iqvia(buf):
    df = pd.read_excel(buf, sheet_name=0, dtype=str)
    numcols = [c for c in df.columns if re.match(r"(LC-\d{2}Q\d|\d{4}년$|DU-\d{2}Q\d|DU_\d{4}년)", str(c))]
    drop = [c for c in df.columns if re.match(r"(CU|Unit|Price)[-_]", str(c))]
    df = df.drop(columns=drop, errors="ignore")
    for c in numcols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df.copy()

def _parse_ubist(buf):
    raw = pd.read_excel(buf, sheet_name=0, header=None)
    row0, row1 = raw.iloc[0].tolist(), raw.iloc[1].tolist()
    METRICS = ("처방조제액(원)", "처방건수_P", "처방량_P")
    cols = []
    for r0, r1 in zip(row0, row1):
        r0 = str(r0).strip() if pd.notna(r0) else ""
        r1 = str(r1).strip() if pd.notna(r1) else ""
        if r0 in METRICS:
            mq = re.search(r"(\d{4})\s*년\s*(\d)\s*분기", r1)
            my = re.search(r"(\d{4})\s*년", r1)
            cols.append(f"{r0}_{mq.group(1)}Q{mq.group(2)}" if mq else
                        (f"{r0}_{my.group(1)}Q0" if my else f"{r0}_{r1}"))
        else:
            cols.append(r1 if r1 else r0)
    df = raw.iloc[2:].copy(); df.columns = cols
    df = df.rename(columns={"제품": "제품명", "제조사": "제조사명"})
    seen, final = {}, []
    for c in df.columns:
        seen[c] = seen.get(c, -1) + 1
        final.append(c if seen[c] == 0 else f"{c}_{seen[c]}")
    df.columns = final
    for c in [c for c in df.columns if any(m in c for m in ("처방조제액", "처방건수", "처방량"))]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df = df.dropna(subset=["제품명"]); df = df[df["제품명"].astype(str).str.strip() != ""]
    for metric in METRICS:
        qc = {}
        for c in df.columns:
            m = re.match(rf"{re.escape(metric)}_(\d{{4}})Q([1-4])$", c)
            if m:
                qc.setdefault(m.group(1), []).append(c)
        for yr, cs in qc.items():
            df[f"{metric}_{yr}년"] = df[cs].sum(axis=1)
    if "성분" in df.columns:
        pat = re.compile(r"(\d+\.?\d*\s?(?:mg|mcg|µg|ug|g|ml|mL|L|IU|단위|%|㎎|㎍|㎕|㎖|㏖|㎏|㎗))", re.IGNORECASE)
        df["용량"] = df["성분"].astype(str).apply(lambda s: "/".join(dict.fromkeys(m.replace(" ", "") for m in pat.findall(s))) or "")
    return df.copy()

def _sheet_df(sheets_api, sid):
    meta = sheets_api.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties(title,gridProperties)").execute()
    def score(s):
        gp = s["properties"].get("gridProperties", {}); return (gp.get("columnCount", 0), gp.get("rowCount", 0))
    cand = [s for s in meta["sheets"] if not s["properties"]["title"].startswith("_")] or meta["sheets"]
    tab = max(cand, key=score)["properties"]["title"]
    vals = sheets_api.spreadsheets().values().get(spreadsheetId=sid, range=tab, valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    cols = [str(c).split("\n")[0].strip() for c in vals[0]]
    rows = [r + [""] * (len(cols) - len(r)) for r in vals[1:]]
    return pd.DataFrame(rows, columns=cols)

def _nego_df():
    board = "https://www.nhis.or.kr/nhis/together/wbhaec05500m01.do"
    H = {"User-Agent": "Mozilla/5.0", "Referer": board}
    def g(u):
        return urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=90).read()
    html = g(board).decode("utf-8", "ignore")
    art = re.search(r"articleNo=(\d+)", html).group(1)
    view = g(f"{board}?mode=view&articleNo={art}&article.offset=0&articleLimit=10").decode("utf-8", "ignore")
    att = re.search(r"attachNo=(\d+)", view).group(1)
    df = pd.read_excel(io.BytesIO(g(f"{board}?mode=download&articleNo={art}&attachNo={att}")), dtype=str).fillna("")
    df.columns = [str(c).split("\n")[0].strip() for c in df.columns]
    ren = {}
    for c in df.columns:
        if "연도" in c or "완료" in c: ren[c] = "연도"
        elif "제품" in c: ren[c] = "제품명"
        elif "회사" in c or "업체" in c: ren[c] = "회사명"
        elif "결과" in c: ren[c] = "협상결과"
    df = df.rename(columns=ren)
    df["_nm"] = df["제품명"].astype(str).str.replace(r"\s+", "", regex=True)
    df["연도"] = df["연도"].astype(str).str.extract(r"(\d{4})")[0]
    keep = [c for c in ["연도", "제품명", "회사명", "협상결과", "_nm"] if c in df.columns]
    return df[keep]

@st.cache_resource(show_spinner="데이터 준비 중… (최초 1회, 드라이브에서 생성 — 1~2분)")
def ensure_data():
    SD.mkdir(exist_ok=True); SAVED.mkdir(exist_ok=True)
    need_scout = {n: not (SD / f"{n}.parquet").exists() for n in ["approval", "patent", "clinical", "price", "nego"]}
    need_iqvia = not (SAVED / "iqvia.pkl").exists()
    need_ubist = not (SAVED / "ubist.pkl").exists()
    if not any(need_scout.values()) and not need_iqvia and not need_ubist:
        return "local"
    from googleapiclient.discovery import build
    creds = _creds()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    # 허가/특허/임상
    for name in ["approval", "patent", "clinical"]:
        if need_scout[name]:
            _sheet_df(sheets, SHEETS[name]).to_parquet(SD / f"{name}.parquet", index=False)
    # 약가
    if need_scout["price"]:
        buf = _download(drive, PRICE_XLSX_ID)
        pd.read_excel(buf, sheet_name="가격이력(변동)", dtype=str).fillna("").to_parquet(SD / "price.parquet", index=False)
    # 공단협상
    if need_scout["nego"]:
        try:
            _nego_df().to_parquet(SD / "nego.parquet", index=False)
        except Exception:
            pd.DataFrame(columns=["연도", "제품명", "회사명", "협상결과", "_nm"]).to_parquet(SD / "nego.parquet", index=False)
    # 매출
    if need_iqvia:
        _parse_iqvia(_download(drive, IQVIA_ID)).to_pickle(SAVED / "iqvia.pkl")
    if need_ubist:
        _parse_ubist(_download(drive, UBIST_ID)).to_pickle(SAVED / "ubist.pkl")
    return "built"
