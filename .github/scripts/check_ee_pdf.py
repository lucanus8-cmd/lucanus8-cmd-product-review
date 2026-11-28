"""질병명 검색 시험 A: 허가 시트의 효능·효과 PDF 주소(EE_DOC_ID)에서 글을 뽑아 성분 30개쯤으로 시험.
공개 자료(식약처)만 쓴다. 사이트에 쓰지 않는다. 매출은 쓰지 않는다."""
import base64, io, json, os, re, sys, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

# 성분(영문 첫 단어) → 기대 질병 (맞게 잡는지 보려는 정답표)
EXPECT = {
    "amlodipine": "고혈압", "losartan": "고혈압", "valsartan": "고혈압", "metformin": "당뇨",
    "sitagliptin": "당뇨", "dapagliflozin": "당뇨", "atorvastatin": "고지혈증|이상지질혈증|고콜레스테롤",
    "rosuvastatin": "고지혈증|이상지질혈증|고콜레스테롤", "ezetimibe": "고지혈증|이상지질혈증|고콜레스테롤",
    "alendronic": "골다공증", "esomeprazole": "역류", "montelukast": "천식", "escitalopram": "우울",
    "donepezil": "치매|알츠하이머", "febuxostat": "통풍", "tamsulosin": "전립선", "clopidogrel": "뇌졸중|심근경색|동맥",
    "fexofenadine": "비염", "celecoxib": "관절염", "tofacitinib": "류마티스", "sumatriptan": "편두통",
    "mirabegron": "과민성 방광|과민성방광", "finasteride": "탈모|전립선", "edoxaban": "색전|혈전",
    "rivaroxaban": "색전|혈전", "levetiracetam": "간질|뇌전증|발작", "pregabalin": "통증", "solifenacin": "과민성 방광|과민성방광",
    "tadalafil": "발기|전립선", "entecavir": "B형 간염|B형간염",
}
TERMS = ["고혈압", "당뇨", "이상지질혈증", "고지혈증", "골다공증", "역류", "천식", "우울", "치매", "알츠하이머",
         "통풍", "전립선비대", "뇌졸중", "비염", "류마티스", "편두통", "과민성 방광", "탈모", "간질", "뇌전증", "B형 간염"]


def text_of(data, ctype):
    if data[:4] == b"%PDF":
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in r.pages)
    s = data.decode("utf-8", "ignore")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"&[a-z]+;", " ", s)


def main():
    info = gsheet._info()
    os.makedirs(".streamlit", exist_ok=True)
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
    import pandas as pd, requests
    import bootstrap, scout_sources as ss
    from googleapiclient.discovery import build
    sheets = build("sheets", "v4", credentials=bootstrap._creds(), cache_discovery=False)
    ap = bootstrap._sheet_df(sheets, bootstrap.SHEETS["approval"])
    ap["_key"] = ss.ing_key_series(ap["주성분(영문)"].fillna("")).str.lower()
    single = ss.is_single_series(ap["주성분"].fillna(""), ap["주성분(영문)"].fillna(""))
    ok_state = ~ap["상태"].astype(str).str.contains("취하|취소|만료", na=False)

    # 변경일자: 주 1회 갱신 때 몇 개나 다시 받아야 하는지
    ch = pd.to_datetime(ap["변경일자"].astype(str).str.slice(0, 10), errors="coerce")
    print(f"변경일자 읽힘 {ch.notna().mean()*100:.0f}% · 최근 7일 바뀐 품목 {int((ch >= pd.Timestamp.now() - pd.Timedelta(days=7)).sum())}개 · "
          f"최근 30일 {int((ch >= pd.Timestamp.now() - pd.Timedelta(days=30)).sum())}개 · 가장 최근 {ch.max()}")
    hd = pd.to_datetime(ap["허가일자"].astype(str).str.slice(0, 10), errors="coerce")
    print(f"최근 30일 새 허가 품목 {int((hd >= pd.Timestamp.now() - pd.Timedelta(days=30)).sum())}개")
    print(f"단일 성분 정상 품목의 성분 수(전체 받기 대상) {ap.loc[single & ok_state & (ap['_key'].str.len()>0), '_key'].nunique():,}개\n")

    ses = requests.Session()
    ses.headers["User-Agent"] = "Mozilla/5.0 (product-review research robot)"
    rows, t_all = [], time.time()
    for key, exp in EXPECT.items():
        sub = ap[(ap["_key"] == key) & single & ok_state]
        if sub.empty:
            print(f"[{key}] 허가 품목 없음"); continue
        it = sub.sort_values("허가일자").iloc[-1]          # 가장 최근 허가 품목 하나
        seq = str(it["품목기준코드"]).strip()
        url = f"https://nedrug.mfds.go.kr/pbp/cmn/pdfDownload/{seq}/EE"
        t = time.time()
        try:
            r = ses.get(url, timeout=30)
            txt = text_of(r.content, r.headers.get("content-type", ""))
            kind = "PDF" if r.content[:4] == b"%PDF" else (r.headers.get("content-type", "?").split(";")[0])
        except Exception as e:
            txt, kind = "", f"실패 {type(e).__name__}"
        txt = re.sub(r"\s+", " ", txt).strip()
        sec = time.time() - t
        good = bool(re.search(exp, txt))
        rows.append({"key": key, "core": ss.ing_core_series(pd.Series([it["주성분"]])).iloc[0], "txt": txt, "good": good})
        print(f"[{key}] {it['품목명'][:24]} · {kind} · {len(r.content) if txt else 0:,}바이트 · {sec:.1f}초 · 글 {len(txt)}자 · "
              f"기대 '{exp}' {'맞음' if good else '없음'}")
        print("     " + txt[:170])
        time.sleep(0.5)
    print(f"\n받기 {len(rows)}개 · 총 {time.time()-t_all:.0f}초 · 기대 질병 맞음 {sum(r['good'] for r in rows)}/{len(rows)}")

    print("\n── 질병명으로 찾기 (이 30개 성분 안에서) ──")
    for term in TERMS:
        hit = [r["core"] or r["key"] for r in rows if term in r["txt"]]
        print(f"[{term}] {len(hit)}개: {', '.join(hit)}")
    idx = pd.Series({r["key"]: r["txt"][:4000] for r in rows})
    print(f"\n이 {len(idx)}개 색인 메모리 {idx.memory_usage(deep=True)/1e6:.2f} MB → 성분 2천 개면 약 {idx.memory_usage(deep=True)/max(len(idx),1)*2000/1e6:.1f} MB 예상")
    return 0


if __name__ == "__main__":
    sys.exit(main())
