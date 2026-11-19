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
    x = d.get("gcp_service_account") or d
    b64 = (x.get("gcp_service_account_b64") if isinstance(x, dict) else None) or d.get("gcp_service_account_b64")
    if b64:                                   # Streamlit Secrets 의 gcp_service_account_b64 = "…" 한 줄 (사이트와 같은 모양)
        return json.loads(base64.b64decode("".join(str(b64).split())).decode("utf-8"))
    return x


def diagnose():
    """비밀값이 어떤 모양인지만 적는다 (값은 절대 적지 않음)."""
    raw = (os.environ.get("GCP_SERVICE_ACCOUNT") or "").strip()
    print(f"   비밀값 길이 {len(raw)}자 · 줄 수 {raw.count(chr(10)) + 1} · 첫 글자 {raw[:1]!r}")
    kind = "알 수 없음"
    d = None
    try:
        d = json.loads(raw)
        kind = "JSON"
    except Exception:
        try:
            d = json.loads(base64.b64decode(raw).decode("utf-8"))
            kind = "base64 JSON"
        except Exception:
            try:
                import tomllib
                d = tomllib.loads(raw if raw.lstrip().startswith("[") else "[gcp_service_account]\n" + raw)
                kind = "TOML"
            except Exception as e:
                print(f"   TOML 로 읽기 실패: {type(e).__name__} · {str(e)[:80]}")
    print(f"   모양: {kind} · 맨 위 이름들: {list(d.keys())[:8] if isinstance(d, dict) else '-'}")
    if isinstance(d, dict):
        x = d.get("gcp_service_account", d)
        if isinstance(x, dict):
            pk = str(x.get("private_key", ""))
            print(f"   칸 이름: {sorted(x.keys())}")
            print(f"   client_email 있음 {bool(x.get('client_email'))} · private_key 길이 {len(pk)}자"
                  f" · BEGIN/END 표시 {('BEGIN PRIVATE KEY' in pk, 'END PRIVATE KEY' in pk)} · '…' 견본 글자 {'…' in pk}")


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
    """{탭 이름: 표} 를 시트에 덮어쓴다. 새 결과는 '이름·새로' 탭에 다 쓴 뒤 한 번에 바꿔 끼운다
    (쓰는 도중에 사이트가 빈 표·반쯤 찬 표를 읽지 않게). 처음 생긴 빈 탭(시트1)은 지운다."""
    api = svc().spreadsheets()
    names = order or list(tabs)

    def tabs_now():
        m = _call(api.get(spreadsheetId=sheet_id, fields="sheets.properties(sheetId,title)"))
        return {x["properties"]["title"]: x["properties"]["sheetId"] for x in m["sheets"]}
    have = tabs_now()
    old_tmp = [have[t + "·새로"] for t in names if t + "·새로" in have]       # 지난번에 끊긴 임시 탭
    if old_tmp:
        _call(api.batchUpdate(spreadsheetId=sheet_id, body={"requests": [{"deleteSheet": {"sheetId": x}} for x in old_tmp]}))
    tmp = {}
    for t in names:
        df = tabs[t].astype(str).replace({"nan": "", "None": ""})
        nrow, ncol = len(df) + 1, max(1, df.shape[1])
        r = _call(api.batchUpdate(spreadsheetId=sheet_id, body={"requests": [{"addSheet": {"properties": {
            "title": t + "·새로", "gridProperties": {"rowCount": nrow + 1, "columnCount": ncol, "frozenRowCount": 1}}}}]}))
        sid = r["replies"][0]["addSheet"]["properties"]["sheetId"]
        tmp[t] = sid
        values = [list(df.columns)] + df.values.tolist()
        for st in range(0, len(values), chunk):
            _call(api.values().update(spreadsheetId=sheet_id, range=f"'{t}·새로'!A{st + 1}", valueInputOption="RAW",
                                      body={"values": values[st:st + chunk]}))
        print(f"   탭 '{t}': {len(df):,}행 × {df.shape[1]}칸 씀 (임시 탭)")
    # 한 번의 요청으로: 옛 탭 지우기 → 임시 탭 이름 바꾸기 → 순서 맞추기
    have = tabs_now()
    req = [{"deleteSheet": {"sheetId": have[t]}} for t in names if t in have]
    req += [{"deleteSheet": {"sheetId": sid}} for title, sid in have.items()
            if title in ("시트1", "Sheet1") and title not in names]
    req += [{"updateSheetProperties": {"properties": {"sheetId": tmp[t], "title": t, "index": i}, "fields": "title,index"}}
            for i, t in enumerate(names)]
    _call(api.batchUpdate(spreadsheetId=sheet_id, body={"requests": req}))
    print("   새 탭으로 한 번에 바꿔 끼움")


def modified_time(sheet_id):
    """구글 드라이브의 마지막 수정 시각 (UTC, datetime). 못 읽으면 None."""
    import datetime
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    try:
        creds = service_account.Credentials.from_service_account_info(
            _info(), scopes=["https://www.googleapis.com/auth/drive.metadata.readonly"])
        d = build("drive", "v3", credentials=creds, cache_discovery=False)
        m = d.files().get(fileId=sheet_id, fields="modifiedTime", supportsAllDrives=True).execute()
        return datetime.datetime.fromisoformat(m["modifiedTime"].replace("Z", "+00:00"))
    except Exception as e:
        print("   수정 시각 못 읽음:", type(e).__name__, str(e)[:120])
        return None


def wait_quiet(sheet_id, name, quiet_min=10, max_wait_min=40):
    """그 시트가 최근 quiet_min 분 동안 바뀌지 않을 때까지 기다린다 (갱신 중이면 최대 max_wait_min 분)."""
    import datetime
    t0 = time.time()
    while True:
        mt = modified_time(sheet_id)
        if mt is None:
            return True
        age = (datetime.datetime.now(datetime.timezone.utc) - mt).total_seconds() / 60
        if age >= quiet_min:
            print(f"   {name} 시트: 마지막 수정 {age:.0f}분 전 → 읽음")
            return True
        if (time.time() - t0) / 60 >= max_wait_min:
            print(f"   {name} 시트: {max_wait_min}분 기다려도 계속 바뀜 → 오늘은 쓰지 않음")
            return False
        print(f"   {name} 시트: {age:.0f}분 전에 바뀜(갱신 중) → 2분 뒤 다시 확인")
        time.sleep(120)
