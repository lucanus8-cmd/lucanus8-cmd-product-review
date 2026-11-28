"""질병명 검색 시험 2: 효능·효과 PDF 글에서 질병이 '치료하는 병(주 적응증)'인지 '환자 설명(관련 언급)'인지 규칙으로 가려
성분 약 200개로 시험한다. 정답 대신 허가 시트의 ATC 코드(약효 분류)와 맞춰 본다.
공개 자료(식약처)만 쓴다. 사이트에 쓰지 않는다. 매출은 쓰지 않는다."""
import base64, io, json, os, re, sys, time, random, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

# 검색어 → (같은 뜻 단어들, 이 병의 약이 주로 속하는 ATC 앞자리)
TERMS = {
    "고혈압": (["고혈압"], ["C02", "C03", "C07", "C08", "C09"]),
    "당뇨": (["당뇨"], ["A10"]),
    "고지혈증": (["고지혈증", "이상지질혈증", "고콜레스테롤혈증", "고중성지방혈증"], ["C10"]),
    "골다공증": (["골다공증"], ["M05", "G03XC", "H05AA"]),
    "위식도역류": (["역류", "위식도역류"], ["A02"]),
    "천식": (["천식"], ["R03"]),
    "우울": (["우울"], ["N06A"]),
    "치매": (["치매", "알츠하이머"], ["N06D"]),
    "통풍": (["통풍", "고요산혈증"], ["M04"]),
    "전립선비대": (["전립선비대", "전립샘비대"], ["G04C"]),
    "뇌전증": (["뇌전증", "간질", "부분발작"], ["N03"]),
    "비염": (["비염"], ["R01", "R06"]),
    "류마티스관절염": (["류마티스관절염", "류마티스성관절염"], ["M01", "L04", "L01", "M01C", "H02"]),
    "편두통": (["편두통"], ["N02C"]),
    "과민성방광": (["과민성방광"], ["G04BD"]),
    "탈모": (["탈모"], ["D11AX", "G04CB"]),
    "B형간염": (["B형간염", "B형만성간염"], ["J05AF"]),
    "혈전": (["혈전", "색전"], ["B01"]),
    "심부전": (["심부전"], ["C01", "C03", "C07", "C09", "C10", "A10BK"]),
    "조현병": (["조현병", "정신분열"], ["N05A"]),
}
GENERIC_AFTER = ("치료", "혈당", "증상", "보조", "관리", "개선", "완화", "조절", "단독", "병용", "투여", "사용", "예방",
                 "1차", "2차", "유지", "초기", "보존", "통증", "발생", "나타나", "동반되는", "생기는")
POPULATION_AFTER = ("을가진", "를가진", "이있는", "가있는", "을동반한", "를동반한", "을앓는", "를앓는", "을동반하는", "이동반된",
                    "을갖는", "를갖는", "인")


def clean(t):
    t = t.replace("\x00", " ")
    return re.sub(r"\s+", " ", t).strip()


def clauses(compact):
    """번호(1. 2.)·줄표·쌍반점으로 나눈 조각."""
    return [c for c in re.split(r"(?:(?<![0-9])\d{1,2}\.(?!\d))|[;·]|-(?=[가-힣])", compact) if c]


def role(compact, words):
    """'주' / '관련' / None 과 그 근거 조각."""
    best, ev = None, ""
    for cl in clauses(compact):
        for w in words:
            for m in re.finditer(re.escape(w), cl):
                tail = cl[m.end():m.end() + 30]
                # 단어 뒤로 병명 끝(병·증·염 등)을 지나 '환자' 가 바로 오는지
                mm = re.match(r"[가-힣A-Za-z0-9()]{0,12}?환자(의|에서|에게|에|들)?", tail)
                pop = any(tail.startswith(p) for p in POPULATION_AFTER) or (
                    re.match(r"[가-힣]{0,6}?(을|를)가진", tail) is not None)
                if mm and not pop:
                    after = tail[mm.end():]
                    # '성인' 같은 꾸밈말 건너뛰기
                    after = re.sub(r"^(성인|소아|청소년|만?\d+세이상의?|남성|여성)", "", after)
                    pop = not after.startswith(GENERIC_AFTER) and len(after) > 0
                r = "관련" if pop else "주"
                if best != "주":
                    best, ev = r, cl[max(0, m.start() - 15):m.end() + 30]
                if r == "주":
                    return "주", cl[max(0, m.start() - 15):m.end() + 30]
    return best, ev


def text_of(data):
    if data[:4] == b"%PDF":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    return re.sub(r"<[^>]+>", " ", data.decode("utf-8", "ignore"))


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
    ap["_atc"] = ap["ATC코드"].fillna("").astype(str).str.upper().str.extract(r"([A-Z]\d\d[A-Z0-9]{0,4})", expand=False).fillna("")
    single = ss.is_single_series(ap["주성분"].fillna(""), ap["주성분(영문)"].fillna(""))
    ok = ~ap["상태"].astype(str).str.contains("취하|취소|만료", na=False)
    base = ap[single & ok & (ap["_key"].str.len() > 0)].copy()
    print(f"ATC 코드 읽힘 {(base['_atc'].str.len() > 0).mean()*100:.0f}% (단일제 정상 품목)")
    katc = base[base["_atc"] != ""].groupby("_key")["_atc"].agg(lambda x: x.value_counts().index[0])

    # 성분 고르기: 병마다 ATC 가 맞는 성분 8개 + 아무 성분 40개 (엉뚱하게 걸리는지 보려고)
    random.seed(7)
    pick = []
    for t, (_, pre) in TERMS.items():
        ks = [k for k, a in katc.items() if any(a.startswith(p) for p in pre)]
        random.shuffle(ks)
        pick += ks[:8]
    rest = [k for k in katc.index if k not in pick]
    random.shuffle(rest)
    pick = list(dict.fromkeys(pick + rest[:40]))
    print(f"시험 성분 {len(pick)}개")

    ses = requests.Session()
    ses.headers["User-Agent"] = "Mozilla/5.0 (product-review research robot)"
    texts, n_pdf, t0, fails = {}, 0, time.time(), 0
    for k in pick:
        sub = base[base["_key"] == k].copy()
        sub["_st"] = sub["품목명"].astype(str).str.extract(r"(\d+(?:\.\d+)?)\s*(?:밀리그램|밀리그람|mg|그램|마이크로그램)", expand=False).fillna("")
        sub = sub.sort_values("허가일자", ascending=False).drop_duplicates("_st").head(4)   # 함량별 최대 4개
        parts = []
        for seq in sub["품목기준코드"].astype(str).str.strip():
            try:
                r = ses.get(f"https://nedrug.mfds.go.kr/pbp/cmn/pdfDownload/{seq}/EE", timeout=30)
                parts.append(clean(text_of(r.content)))
                n_pdf += 1
            except Exception:
                fails += 1
            time.sleep(0.3)
        texts[k] = " | ".join(dict.fromkeys(p for p in parts if p))
    print(f"PDF {n_pdf}개 받음 · 실패 {fails}개 · {time.time()-t0:.0f}초 (PDF 하나 평균 {(time.time()-t0)/max(n_pdf,1):.2f}초)")
    core = base.drop_duplicates("_key").set_index("_key")["주성분"].astype(str).map(lambda s: ss.ing_core_series(pd.Series([s])).iloc[0])

    tot = {"주_맞음": 0, "주_의심": 0, "관련_ATC맞음": 0, "관련_ATC다름": 0, "글없음_ATC맞음": 0}
    for term, (words, pre) in TERMS.items():
        wc = [w.replace(" ", "") for w in words]
        main_, rel = [], []
        for k, tx in texts.items():
            compact = tx.replace(" ", "")
            r, ev = role(compact, wc)
            if not r:
                continue
            atc_ok = any(katc.get(k, "").startswith(p) for p in pre)
            (main_ if r == "주" else rel).append((k, atc_ok, ev))
        expect = [k for k in texts if any(katc.get(k, "").startswith(p) for p in pre)]
        miss = [k for k in expect if k not in {x[0] for x in main_ + rel}]
        tot["주_맞음"] += sum(a for _, a, _ in main_); tot["주_의심"] += sum(not a for _, a, _ in main_)
        tot["관련_ATC맞음"] += sum(a for _, a, _ in rel); tot["관련_ATC다름"] += sum(not a for _, a, _ in rel)
        tot["글없음_ATC맞음"] += len(miss)
        nm = lambda k: core.get(k, k)
        print(f"\n[{term}] 주 적응증 {len(main_)}개 (ATC 맞음 {sum(a for _,a,_ in main_)}) · 관련 언급 {len(rel)}개 · ATC는 맞는데 글에 없음 {len(miss)}개")
        print("   주: " + ", ".join(nm(k) + ("" if a else "⚠") for k, a, _ in main_))
        for k, a, ev in [x for x in main_ if not x[1]][:4]:
            print(f"     ⚠ {nm(k)} ({katc.get(k,'')}): …{ev}…")
        if rel:
            print("   관련: " + ", ".join(nm(k) + ("✱" if a else "") for k, a, _ in rel))
            for k, a, ev in rel[:3]:
                print(f"     · {nm(k)} ({katc.get(k,'')}): …{ev}…")
        if miss:
            print("   글에 없음(ATC만 맞음): " + ", ".join(f"{nm(k)}({katc.get(k,'')})" for k in miss[:8]))
    print("\n── 합계 ──")
    print(f"주 적응증으로 잡힘: ATC 맞음 {tot['주_맞음']} · ATC 다름(확인 필요) {tot['주_의심']}")
    print(f"관련 언급으로 돌림: ATC 다름(제대로 뺀 것으로 보임) {tot['관련_ATC다름']} · ATC 맞음(주 적응증인데 빠졌을 수 있음 ✱) {tot['관련_ATC맞음']}")
    print(f"ATC는 그 병 계열인데 효능 글에 그 병이 없음: {tot['글없음_ATC맞음']} (계열 안의 다른 용도 약일 수 있음)")
    idx = pd.Series({k: v[:4000] for k, v in texts.items()})
    print(f"색인 {len(idx)}개 메모리 {idx.memory_usage(deep=True)/1e6:.2f} MB → 성분 1,642개면 약 {idx.memory_usage(deep=True)/len(idx)*1642/1e6:.1f} MB")
    print(f"전체 받기 어림: 성분 1,642개 × 함량별 평균 {n_pdf/max(len(pick),1):.1f}개 × {(time.time()-t0)/max(n_pdf,1):.2f}초 ≈ {1642*n_pdf/max(len(pick),1)*(time.time()-t0)/max(n_pdf,1)/60:.0f}분")
    return 0


if __name__ == "__main__":
    sys.exit(main())
