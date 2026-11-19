"""로봇용 구글 시트 읽기·쓰기. 키는 GitHub 비밀값 GCP_SERVICE_ACCOUNT 에서만 읽고, 절대 화면에 적지 않는다.

비밀값 모양은 셋 다 받는다: service-account.json 내용 · 그 base64 · Streamlit Secrets 의 [gcp_service_account] 묶음(TOML).
"""
import base64
import json
import os
import time

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_SVC = {}


def _info():
    raw = (os.environ.get("GCP_SERVICE_ACCOUNT") or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception:
        pass
    import tomllib
    d = tomllib.loads(raw if raw.lstrip().startswith("[") else "[gcp_service_account]\n" + raw)
    return d.get("gcp_service_account") or d


def available():
    try:
        i = _info()
        return bool(i and i.get("client_email") and i.get("private_key"))
    except Exception as e:
        print("키 읽기 실패:", type(e).__name__)
        return False


def svc():
    if "s" not in _SVC:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_info(_info(), scopes=SCOPES)
        _SVC["s"] = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _SVC["s"]


def account_email():
    i = _info() or {}
    return i.get("client_email", "")


def read_sheet(sheet_id):
    """가장 큰 데이터 탭을 표(pandas)로 읽는다 (사이트 bootstrap 과 같은 방식)."""
    import pandas as pd
    api = svc()
    meta = api.spreadsheets().get(spreadsheetId=sheet_id, fields="properties(title),sheets.properties(title,gridProperties)").execute()
    cand = [s for s in meta["sheets"] if not s["properties"]["title"].startswith("_")] or meta["sheets"]
    tab = max(cand, key=lambda s: (s["properties"].get("gridProperties", {}).get("columnCount", 0),
                                   s["properties"].get("gridProperties", {}).get("rowCount", 0)))["properties"]["title"]
    vals = api.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"'{tab}'",
                                           valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    if not vals:
        return pd.DataFrame()
    hdr = [str(c).split("\n")[0].strip() for c in vals[0]]
    rows = [r + [""] * (len(hdr) - len(r)) for r in vals[1:]]
    return pd.DataFrame([r[:len(hdr)] for r in rows], columns=hdr).fillna("")


def _call(req, tries=5):
    for i in range(tries):
        try:
            return req.execute()
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"   다시 시도 ({type(e).__name__})")
            time.sleep(10 * (i + 1))


def write_tabs(sheet_id, tabs, order=None, chunk=8000):
    """{탭 이름: 표} 를 시트에 통째로 덮어쓴다. 없는 탭은 만들고, 처음 생긴 빈 탭(시트1)은 지운다."""
    api = svc().spreadsheets()
    meta = _call(api.get(spreadsheetId=sheet_id, fields="sheets.properties(sheetId,title)"))
    have = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    names = order or list(tabs)
    add = [{"addSheet": {"properties": {"title": t}}} for t in names if t not in have]
    if add:
        r = _call(api.batchUpdate(spreadsheetId=sheet_id, body={"requests": add}))
        for rep in r.get("replies", []):
            p = rep["addSheet"]["properties"]
            have[p["title"]] = p["sheetId"]
    for idx, t in enumerate(names):
        df = tabs[t].astype(str).replace({"nan": "", "None": ""})
        nrow, ncol = len(df) + 1, max(1, df.shape[1])
        _call(api.values().clear(spreadsheetId=sheet_id, range=f"'{t}'"))
        _call(api.batchUpdate(spreadsheetId=sheet_id, body={"requests": [
            {"updateSheetProperties": {"properties": {"sheetId": have[t], "index": idx,
                                                      "gridProperties": {"rowCount": nrow + 1, "columnCount": ncol,
                                                                         "frozenRowCount": 1}},
                                       "fields": "index,gridProperties(rowCount,columnCount,frozenRowCount)"}}]}))
        values = [list(df.columns)] + df.values.tolist()
        for s in range(0, len(values), chunk):
            _call(api.values().update(spreadsheetId=sheet_id, range=f"'{t}'!A{s + 1}", valueInputOption="RAW",
                                      body={"values": values[s:s + chunk]}))
        print(f"   탭 '{t}': {len(df):,}행 × {df.shape[1]}칸 씀")
    extra = [sid for title, sid in have.items() if title not in names and title in ("시트1", "Sheet1")]
    if extra:
        _call(api.batchUpdate(spreadsheetId=sheet_id, body={"requests": [{"deleteSheet": {"sheetId": x}} for x in extra]}))
