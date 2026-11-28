"""식약처 허가 효능·효과 원문 모으기 (사이트 '질병명으로 좁히기' 용).

허가 시트의 정상 품목마다 효능·효과 PDF(nedrug pdfDownload/{품목기준코드}/EE)에서 글을 뽑는다.
원문은 고치지 않는다 — PDF 에서 뽑을 때 생기는 빈 글자(\\x00)를 띄어쓰기로, 연속 공백을 하나로만 맞춘다.
같은 글은 한 번만 저장(효능번호). 지난번 파일이 있으면 새 품목·변경일자가 바뀐 품목만 받는다.

사용: python build_efficacy.py <지난번 폴더 또는 ''> <저장 폴더> [최대 분]
"""
import base64, io, json, os, re, sys, time, threading, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402


def clean(t):
    return re.sub(r"\s+", " ", t.replace("\x00", " ")).strip()


def text_of(data):
    if data[:4] == b"%PDF":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    if data[:1] in (b"<", b"\xef"):
        s = data.decode("utf-8", "ignore")
        if "효능" in s or "<" in s[:50]:
            return re.sub(r"<[^>]+>", " ", s)
    return ""


def main():
    prev_dir = sys.argv[1] if len(sys.argv) > 1 else ""
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "efficacy_site"
    max_min = float(sys.argv[3]) if len(sys.argv) > 3 else 300
    os.makedirs(out_dir, exist_ok=True)
    info = gsheet._info()
    os.makedirs(".streamlit", exist_ok=True)
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
    import pandas as pd, requests
    import bootstrap
    from googleapiclient.discovery import build
    sheets = build("sheets", "v4", credentials=bootstrap._creds(), cache_discovery=False)
    ap = bootstrap._sheet_df(sheets, bootstrap.SHEETS["approval"])
    ok = ~ap["상태"].astype(str).str.contains("취하|취소|만료", na=False)
    ap = ap[ok].copy()
    ap["품목기준코드"] = ap["품목기준코드"].astype(str).str.strip()
    ap["변경일자"] = ap["변경일자"].astype(str).str.slice(0, 10).replace({"nan": "", "None": ""})
    ap = ap[ap["품목기준코드"].str.len() > 0].drop_duplicates("품목기준코드")
    print(f"정상 품목 {len(ap):,}개")

    old_item, old_text = None, None
    if prev_dir and os.path.exists(os.path.join(prev_dir, "ee_item.parquet")):
        old_item = pd.read_parquet(os.path.join(prev_dir, "ee_item.parquet"))
        old_text = pd.read_parquet(os.path.join(prev_dir, "ee_text.parquet"))
        print(f"지난번: 품목 {len(old_item):,}개 · 글 {len(old_text):,}개")
    have = {}
    if old_item is not None:
        t = old_text.set_index("효능번호")["효능효과"]
        for code, n, ch in zip(old_item["품목기준코드"].astype(str), old_item["효능번호"], old_item["변경일자"].astype(str)):
            have[code] = (t.get(n, ""), ch)
    need = [c for c, ch in zip(ap["품목기준코드"], ap["변경일자"])
            if c not in have or (ch and ch > (have[c][1] or ""))]
    print(f"이번에 받을 품목 {len(need):,}개 (새 품목·변경된 품목)")

    ses = requests.Session()
    ses.headers["User-Agent"] = "Mozilla/5.0 (product-review research robot)"
    lock = threading.Lock()
    got, fails, stop_at = {}, [], time.time() + max_min * 60

    def get(code):
        if time.time() > stop_at:
            return code, None
        for attempt in range(3):
            try:
                r = ses.get(f"https://nedrug.mfds.go.kr/pbp/cmn/pdfDownload/{code}/EE", timeout=40)
                if r.status_code == 200:
                    time.sleep(0.15)
                    return code, clean(text_of(r.content))
            except Exception:
                pass
            time.sleep(2 * (attempt + 1))
        return code, False

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(get, c) for c in need]
        for f in as_completed(futs):
            code, txt = f.result()
            done += 1
            if txt is None:
                continue                     # 시간 다 됨 → 다음 번에
            if txt is False:
                fails.append(code)
            else:
                got[code] = txt
            if done % 2000 == 0:
                print(f"  {done:,}/{len(need):,} · {time.time()-t0:.0f}초 · 실패 {len(fails)}", flush=True)
    left = len(need) - len(got) - len(fails)
    print(f"받음 {len(got):,} · 실패 {len(fails)} · 시간이 다 돼 다음으로 미룸 {left:,} · {time.time()-t0:.0f}초")

    # 합치기: 이번에 받은 것 > 지난번 것 (실패·미룬 품목은 지난번 글 유지)
    ch_of = dict(zip(ap["품목기준코드"], ap["변경일자"]))
    rows = []
    for code in ap["품목기준코드"]:
        if code in got:
            rows.append((code, got[code], ch_of.get(code, "")))
        elif code in have:
            rows.append((code, have[code][0], have[code][1]))
    df = pd.DataFrame(rows, columns=["품목기준코드", "효능효과", "변경일자"])
    uniq = pd.Series(df["효능효과"].unique())
    tid = {t: i for i, t in enumerate(uniq)}
    text_tab = pd.DataFrame({"효능번호": range(len(uniq)), "효능효과": uniq.values})
    item_tab = pd.DataFrame({"품목기준코드": df["품목기준코드"], "효능번호": df["효능효과"].map(tid).astype("int32"),
                             "변경일자": df["변경일자"]})
    text_tab.to_parquet(os.path.join(out_dir, "ee_text.parquet"), compression="zstd", index=False)
    item_tab.to_parquet(os.path.join(out_dir, "ee_item.parquet"), compression="zstd", index=False)
    empty = int((df["효능효과"].str.len() == 0).sum())
    meta = {"기준일": time.strftime("%Y-%m-%d"), "품목": int(len(item_tab)), "글": int(len(text_tab)),
            "빈 글": empty, "이번에 받음": len(got), "실패": len(fails), "미룸": int(left),
            "정상 품목": int(len(ap))}
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    sz = sum(os.path.getsize(os.path.join(out_dir, n)) for n in ("ee_text.parquet", "ee_item.parquet")) / 1e6
    mem = (text_tab.memory_usage(deep=True).sum() + item_tab.memory_usage(deep=True).sum()) / 1e6
    print(f"저장: 품목 {len(item_tab):,} · 서로 다른 글 {len(text_tab):,} · 빈 글 {empty:,} · 파일 {sz:.2f} MB · 메모리 {mem:.1f} MB")
    print("meta:", meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
