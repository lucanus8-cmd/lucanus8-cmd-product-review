"""구글시트(허가·특허·임상) → scout_data/*.parquet 캐시 생성/갱신.

두 가지 접근 방식을 자동 선택한다:
  A) 서비스 계정(비공개, 권장): secrets/service-account.json 이 있으면 Sheets API로 읽음.
  B) 링크공개 CSV(폴백): 위 파일이 없으면 공개 export URL로 읽음.

시트가 자동 업데이트되므로 주기적으로 실행하면 최신 데이터가 앱에 반영된다.
사용:  python build_scout_cache.py
"""
import io
import sys
import urllib.request
from pathlib import Path
import pandas as pd

BASE = Path(__file__).parent
SD = BASE / "scout_data"
SD.mkdir(exist_ok=True)
KEYFILE = BASE / "secrets" / "service-account.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

SHEETS = {
    "clinical": "1iFPo8V-JLFQEkFgkfAH2l8-FtUEia0on6Q8OjtPr6sE",  # 임상시험 승인현황
    "patent":   "1tj75vy8KcJSmz6-rAqmPpLThNt7ZymuQfi12Vbzz1CE",  # 특허현황
    "approval": "1x0lPMvsH0G-9cOdwi1kZy5yTAhAaz9N5BluK36iS8-Q",  # 허가사항
}

def clean_cols(df):
    df.columns = [str(c).split("\n")[0].strip() for c in df.columns]
    return df.fillna("")

# ── A) 서비스 계정 (비공개, Sheets API) ─────────────────────────────────────
def fetch_via_api(sheet_id):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(str(KEYFILE), scopes=SCOPES)
    api = build("sheets", "v4", credentials=creds, cache_discovery=False)
    meta = api.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets.properties(title,gridProperties)").execute()
    # 데이터 탭 선택: '_' 접두 도우미 탭 제외 후, 컬럼 수가 가장 많은 탭
    def score(s):
        gp = s["properties"].get("gridProperties", {})
        return (gp.get("columnCount", 0), gp.get("rowCount", 0))
    cand = [s for s in meta["sheets"] if not s["properties"]["title"].startswith("_")] or meta["sheets"]
    data_tab = max(cand, key=score)["properties"]["title"]
    # FORMATTED_VALUE: 셀에 보이는 그대로 문자열로 (긴 코드가 지수표기로 깨지는 것 방지)
    vals = api.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=data_tab, valueRenderOption="FORMATTED_VALUE",
    ).execute().get("values", [])
    if not vals:
        raise RuntimeError("빈 시트")
    header = vals[0]
    rows = [r + [""] * (len(header) - len(r)) for r in vals[1:]]  # 행 길이 보정
    return clean_cols(pd.DataFrame(rows, columns=header).astype(str))

# ── B) 링크공개 CSV (폴백) ──────────────────────────────────────────────────
def fetch_via_csv(sheet_id):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
    if raw[:15].lstrip().lower().startswith(b"<!doctype") or raw[:6].lower() == b"<html":
        raise RuntimeError("비공개 시트 — 링크공개 또는 서비스계정(secrets/service-account.json) 필요")
    return clean_cols(pd.read_csv(io.BytesIO(raw), dtype=str, low_memory=False))

def main():
    use_api = KEYFILE.exists()
    print(f"모드: {'서비스계정(API)' if use_api else '링크공개(CSV)'}")
    for name, sid in SHEETS.items():
        try:
            df = fetch_via_api(sid) if use_api else fetch_via_csv(sid)
            df.to_parquet(SD / f"{name}.parquet", index=False)
            print(f"[OK] {name}: {len(df):,}행 → scout_data/{name}.parquet")
        except Exception as e:
            print(f"[FAIL] {name}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
