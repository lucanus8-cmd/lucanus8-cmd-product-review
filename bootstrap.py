"""배포 서버에서 데이터 캐시를 '이미 주신 드라이브 소스'로부터 직접 생성한다.
- 로컬에 캐시가 있으면 아무것도 안 함(로컬 개발 그대로).
- 없으면 서비스계정으로: 매출(iqvia/ubist xlsx), 약가(통합 xlsx), 허가/특허/임상(구글시트),
  공단협상(NHIS 공개)에서 파싱하여 saved_data/*.pkl, scout_data/*.parquet 생성.
서비스계정 키: st.secrets["gcp_service_account"](배포) 또는 secrets/service-account.json(로컬).
추가 파일 업로드/파일ID 입력 불필요 — 소스 ID는 아래 상수로 고정.
"""
import io
import re
import time
import urllib.request
from pathlib import Path
import pandas as pd
import streamlit as st

BASE = Path(__file__).parent
SD = BASE / "scout_data"
SAVED = BASE / "saved_data"

# 이미 공유해주신 드라이브 소스들
IQVIA_ID = "1AfN8A8XXJoulm6PPctjKWRsetGD5MSYt"
UBIST_ID = "1Mfzz4qFjd1xtBGDt30xM2NHaXWvN8Ycv"  # 2023 2분기 포함 새 파일
PRICE_XLSX_ID = "1u9gfxs7NyuQebBn4WEOyBCUQ0zQvYqYf"
# 매월 고시 약가 엑셀을 넣는 드라이브 폴더(HIRA). 파일명에 시행일(예: (2026.9.1.))이 들어감.
HIRA_PRICE_FOLDER_ID = "10LCp9oVtdJPBf34stPbqqzlAm3W1_lPG"
SHEETS = {
    "clinical": "1iFPo8V-JLFQEkFgkfAH2l8-FtUEia0on6Q8OjtPr6sE",
    "patent": "1tj75vy8KcJSmz6-rAqmPpLThNt7ZymuQfi12Vbzz1CE",
    "approval": "1x0lPMvsH0G-9cOdwi1kZy5yTAhAaz9N5BluK36iS8-Q",
}
SCOPES = ["https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/spreadsheets.readonly"]

def _creds():
    import base64
    import json
    from google.oauth2 import service_account
    # 0) 가장 견고한 방식: 전체 JSON을 base64로 인코딩한 단일 값.
    #    (여러 줄 PEM 붙여넣기에서 생기는 문자 손상/스마트문장부호 문제를 원천 차단)
    b64 = None
    try:
        b64 = st.secrets.get("gcp_service_account_b64", None)
    except Exception:
        b64 = None
    if b64:
        try:
            info = json.loads(base64.b64decode(str(b64).strip()))
            return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        except Exception as e:
            raise RuntimeError(
                "Secrets의 gcp_service_account_b64 처리에 실패했습니다.\n"
                f"원인: {type(e).__name__}: {e}\n"
                "→ base64 값이 잘리지 않고 한 줄로 온전히 들어갔는지 확인하세요."
            ) from e
    # 1) 배포: st.secrets["gcp_service_account"] 테이블 사용
    raw = None
    try:
        raw = st.secrets["gcp_service_account"]
    except Exception:
        raw = None
    if raw is not None:
        info = dict(raw)
        pk = info.get("private_key", "")
        if isinstance(pk, str):
            # 개행이 문자 그대로 "\n"으로 들어온 경우 복구
            if "\\n" in pk and "\n" not in pk:
                pk = pk.replace("\\n", "\n")
            # 붙여넣기 자동변환(스마트 문장부호)로 섞인 비ASCII 문자를 정리.
            # 정상 PEM/base64 키는 전부 ASCII이므로 이 정규화는 안전하다.
            import unicodedata
            out = []
            for ch in pk:
                if ch.isascii():
                    out.append(ch)
                elif unicodedata.category(ch) == "Pd" or ch == "\u2212":
                    out.append("-")              # 각종 유니코드 대시/마이너스 -> 하이픈
                elif ch in "\u2018\u2019\u201a\u201b\u2032":
                    out.append("'")              # 스마트 작은따옴표
                elif ch in "\u201c\u201d\u201e\u201f\u2033":
                    out.append('"')              # 스마트 큰따옴표
                elif ch == "\u00a0":
                    out.append(" ")              # 비분리 공백
                # 그 외 비ASCII(제로폭 공백/BOM 등)는 제거
            info["private_key"] = "".join(out)
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
    def _pull(req):
        buf = io.BytesIO(); dl = MediaIoBaseDownload(buf, req, chunksize=10 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
        buf.seek(0); return buf
    try:
        return _pull(drive.files().get_media(fileId=fid, supportsAllDrives=True))
    except Exception:
        # 구글 시트(네이티브)로 저장된 경우 xlsx로 내보내기(＜10MB 제한)
        return _pull(drive.files().export_media(
            fileId=fid,
            mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))

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
            # 분기 표기 변형 허용: "2023년 4분기", "2023년 4/4분기", "2023 4Q", "2023년4/4" 등
            mq = (re.search(r"(\d{4})\s*년?\s*([1-4])\s*/?\s*4?\s*분기", r1)
                  or re.search(r"(\d{4})\D*?Q\s*([1-4])", r1, re.IGNORECASE)
                  or re.search(r"(\d{4})\D*?([1-4])\s*/\s*4", r1))
            my = re.search(r"(\d{4})", r1)
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
        # 연간 합계 = 해당 metric의 그 연도 모든 기간 컬럼 합(Q1~Q4는 물론 분기표기 인식 실패분 Q0,
        # 중복표기 _1 등도 포함) → 특정 분기 헤더가 조금 달라도 연간에서 누락되지 않도록.
        yc = {}
        for c in df.columns:
            if not c.startswith(metric + "_"):
                continue
            if re.search(r"\d{4}년$", c):      # 이미 만든 연간 결과 컬럼은 제외
                continue
            ym = re.search(r"(\d{4})", c)
            if ym:
                yc.setdefault(ym.group(1), []).append(c)
        for yr, cs in yc.items():
            df[f"{metric}_{yr}년"] = df[cs].sum(axis=1)
    if "성분" in df.columns:
        pat = re.compile(r"(\d+\.?\d*\s?(?:mg|mcg|µg|ug|g|ml|mL|L|IU|단위|%|㎎|㎍|㎕|㎖|㏖|㎏|㎗))", re.IGNORECASE)
        df["용량"] = df["성분"].astype(str).apply(lambda s: "/".join(dict.fromkeys(m.replace(" ", "") for m in pat.findall(s))) or "")
    return df.copy()

def _sheet_df(sheets_api, sid):
    """스프레드시트에서 '데이터가 있는' 주 탭을 읽는다.
    컬럼 수가 많은 탭부터 시도하되, 값이 비어 있는 탭(빈 시트·유지용 등)은 건너뛴다."""
    meta = sheets_api.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties(title,gridProperties)").execute()
    def score(s):
        gp = s["properties"].get("gridProperties", {})
        return (gp.get("columnCount", 0), gp.get("rowCount", 0))
    cand = [s for s in meta["sheets"] if not s["properties"]["title"].startswith("_")] or meta["sheets"]
    for sh in sorted(cand, key=score, reverse=True):
        tab = sh["properties"]["title"]
        vals = (sheets_api.spreadsheets().values()
                .get(spreadsheetId=sid, range=tab, valueRenderOption="FORMATTED_VALUE")
                .execute().get("values", []))
        if len(vals) < 2:          # 헤더+데이터가 없으면 빈 탭 → 다음 후보
            continue
        cols = [str(c).split("\n")[0].strip() for c in vals[0]]
        n = len(cols)
        rows = [(r + [""] * n)[:n] for r in vals[1:]]   # 짧은/긴 행 모두 안전하게
        return pd.DataFrame(rows, columns=cols)
    raise RuntimeError(f"데이터가 있는 탭을 찾지 못했습니다 (스프레드시트 {sid})")

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

def _parse_price_date(name):
    """파일명에서 시행일 추출. 예: '..._(2026.9.1.)_...' → Timestamp('2026-09-01').
    (2026.9.) 처럼 일이 없으면 1일로. 없으면 None."""
    s = str(name)
    m = re.search(r"\(?\s*(\d{4})[.\-/]\s*(\d{1,2})(?:[.\-/]\s*(\d{1,2}))?", s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    try:
        return pd.Timestamp(year=y, month=mo, day=d)
    except Exception:
        return None

def _price_from_folder(drive):
    """HIRA 폴더의 월별 고시 약가 엑셀들을 표준 스키마로 읽어 하나의 DF로.
    약가 파일 식별: '제품코드'와 '상한금액' 컬럼이 있는 파일. 실패는 조용히 건너뜀."""
    def _find(cols, cands):
        for cand in cands:
            for c in cols:
                if cand in str(c):
                    return c
        return None
    try:
        res = drive.files().list(
            q=f"'{HIRA_PRICE_FOLDER_ID}' in parents and trashed=false",
            fields="files(id,name)", pageSize=500,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
    except Exception:
        return None
    frames = []
    for f in files:
        d = _parse_price_date(f.get("name", ""))
        if d is None:
            continue
        try:
            df = pd.read_excel(_download(drive, f["id"]), dtype=str).fillna("")
        except Exception:
            continue
        df.columns = [str(c).replace("\n", "").strip() for c in df.columns]
        code_c = _find(df.columns, ["제품코드"])
        amt_c = _find(df.columns, ["상한금액표금액", "상한금액", "금액"])
        if not code_c or not amt_c:
            continue  # 약가 파일이 아님
        name_c = _find(df.columns, ["제품명"])
        ing_c = _find(df.columns, ["주성분명"])
        out = pd.DataFrame({
            "제품코드": df[code_c].astype(str).str.strip(),
            "제품명": df[name_c].astype(str) if name_c else "",
            "주성분명": df[ing_c].astype(str) if ing_c else "",
            "금액": df[amt_c].astype(str).str.replace(r"[^0-9.]", "", regex=True),
            "적용일자": d.strftime("%Y-%m-%d"),
            "급여구분": "급여",
        })
        out = out[out["제품코드"] != ""]
        frames.append(out)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)

def _compress_price(df):
    """제품코드별로 금액이 바뀐 시점만 남겨 이력을 압축(메모리 절약)."""
    df = df.copy()
    df["_amt"] = pd.to_numeric(df["금액"], errors="coerce")
    df["_dt"] = pd.to_datetime(df["적용일자"], errors="coerce")
    df = df.dropna(subset=["_amt"]).sort_values(["제품코드", "_dt"])
    # 같은 (제품코드,적용일자) 중복 제거 후, 직전과 금액 같으면 제거
    df = df.drop_duplicates(subset=["제품코드", "적용일자", "_amt"])
    prev = df.groupby("제품코드")["_amt"].shift()
    changed = df[prev.isna() | (prev != df["_amt"])]
    return changed.drop(columns=["_amt", "_dt"])

# ── 자동 새로고침 주기(시간) ────────────────────────────────────────────────
# 저장된 캐시가 이 시간보다 오래되면 원본(구글시트/드라이브)에서 다시 생성한다.
MAX_AGE_H = {
    "approval": 24, "patent": 24, "clinical": 24,   # 식약처 구글시트(매일 수집됨)
    "price": 24, "nego": 24,                        # 약가·공단협상
    "iqvia": 24 * 7, "ubist": 24 * 7,               # 매출(대용량·수동 업로드 → 주 1회)
}

def _cache_path(name):
    return (SAVED / f"{name}.pkl") if name in ("iqvia", "ubist") else (SD / f"{name}.parquet")

def _stale(name):
    """캐시 파일이 없거나 MAX_AGE_H 보다 오래됐으면 True(=다시 만들어야 함)."""
    p = _cache_path(name)
    if not p.exists():
        return True
    try:
        return (time.time() - p.stat().st_mtime) > MAX_AGE_H[name] * 3600
    except Exception:
        return True

@st.cache_resource(ttl=3600, show_spinner="데이터 준비 중… (드라이브에서 생성 — 1~2분)")
def ensure_data():
    """캐시가 오래됐으면 원본에서 다시 생성(자동 새로고침).
    ttl=1시간마다 점검하지만, 대부분은 파일 시각만 확인하고 바로 끝난다.
    자격증명이 없거나 갱신에 실패하면 기존 데이터를 그대로 사용해 앱이 죽지 않게 한다."""
    SD.mkdir(exist_ok=True); SAVED.mkdir(exist_ok=True)
    # 자가복구: 이전 버전이 '투여' 등 컬럼을 누락한 채 저장한 price.parquet면 삭제해 재생성 유도
    _pp = SD / "price.parquet"
    if _pp.exists():
        try:
            pd.read_parquet(_pp, columns=["투여"])
        except Exception:
            try:
                _pp.unlink()
            except Exception:
                pass
    SCOUT = ["approval", "patent", "clinical", "price", "nego"]
    need = {n: _stale(n) for n in SCOUT + ["iqvia", "ubist"]}
    if not any(need.values()):
        return "local"

    have_all = all(_cache_path(n).exists() for n in SCOUT + ["iqvia", "ubist"])
    try:
        from googleapiclient.discovery import build
        creds = _creds()
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception:
        if have_all:
            return "local"      # 로컬/자격증명 없음 → 기존 데이터 그대로 사용
        raise

    rebuilt = []

    def _try(name, fn):
        """갱신 시도. 실패해도 기존 파일이 있으면 유지하고, 다음 주기까지 재시도를 미룬다."""
        try:
            fn()
            rebuilt.append(name)
        except Exception:
            p = _cache_path(name)
            if not p.exists():
                raise           # 최초 생성 실패는 알려야 함
            try:
                p.touch()       # 기존 데이터 유지 + 매 시간 재시도 방지
            except Exception:
                pass

    # 허가/특허/임상 (구글시트)
    for name in ["approval", "patent", "clinical"]:
        if need[name]:
            _try(name, lambda n=name: _sheet_df(sheets, SHEETS[n]).to_parquet(SD / f"{n}.parquet", index=False))

    # 약가: 기존 이력 엑셀 + HIRA 폴더의 월별 고시 엑셀을 합쳐 이력 생성
    if need["price"]:
        def _build_price():
            base = pd.read_excel(_download(drive, PRICE_XLSX_ID),
                                 sheet_name="가격이력(변동)", dtype=str).fillna("")
            merged = base
            try:
                folder = _price_from_folder(drive)   # 월별 고시(없으면 None)
                if folder is not None and not folder.empty:
                    fc = _compress_price(folder)     # 폴더(월별 스냅샷)만 변동점으로 압축
                    # 기존(base)은 컬럼·행 그대로 보존하고, base에 없는 (제품코드,적용일자)만 추가
                    if {"제품코드", "적용일자"}.issubset(base.columns):
                        have = set(zip(base["제품코드"].astype(str), base["적용일자"].astype(str)))
                        keep = [not ((str(a), str(b)) in have)
                                for a, b in zip(fc["제품코드"], fc["적용일자"])]
                        fc = fc[keep]
                    merged = pd.concat([base, fc], ignore_index=True)
            except Exception:
                merged = base   # 폴더 처리 실패 시 기존 이력만 사용(안전)
            merged.to_parquet(SD / "price.parquet", index=False)
        _try("price", _build_price)

    # 공단협상(NHIS 공개자료)
    if need["nego"]:
        def _build_nego():
            try:
                _nego_df().to_parquet(SD / "nego.parquet", index=False)
            except Exception:
                p = SD / "nego.parquet"
                if p.exists():
                    p.touch()   # 스크래핑 실패 → 기존 유지
                else:
                    pd.DataFrame(columns=["연도", "제품명", "회사명", "협상결과", "_nm"]).to_parquet(p, index=False)
        _try("nego", _build_nego)

    # 매출(대용량)
    if need["iqvia"]:
        _try("iqvia", lambda: _parse_iqvia(_download(drive, IQVIA_ID)).to_pickle(SAVED / "iqvia.pkl"))
    if need["ubist"]:
        _try("ubist", lambda: _parse_ubist(_download(drive, UBIST_ID)).to_pickle(SAVED / "ubist.pkl"))

    if rebuilt:
        # 새로 만든 데이터가 화면에 바로 반영되도록 로더 캐시를 비운다
        try:
            st.cache_data.clear()
        except Exception:
            pass
    return "built" if rebuilt else "local"
