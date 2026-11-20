"""유럽 EMA 의약품 자료로 'FDA 비교'와 비슷한 화면을 만들 수 있는지 검토한다 (읽기만 · 사이트와 상관없음).

EMA 가 공개하는 의약품 자료 파일(JSON, 하루 두 번 갱신 · 없으면 표 파일)을 받아
  A. 제품 수 · 허가 상태 · 적응증 글 있는 비율
  B. 성분을 한국 허가(식약처 시트)·미국(사이트 FDA 표)과 짝짓기 → 유럽에만 있는 성분
  C. 적응증 글에서 질병 66개 찾기 (FDA 검토 규칙 check_ind_headings.TESTS 그대로) · FDA 결과와 맞춰 보기
  D. 검토 표본
를 출력하고 out/ema_*.csv 로 남긴다.
"""
import html
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

import pandas as pd

sys.path.insert(0, ".github/scripts")
import check_overseas as ov          # noqa: E402  (영문 염 떼기 재사용)
import check_ind_headings as ih      # noqa: E402  (질병 찾기 규칙 재사용)

OUT = "out"
RAW = "https://raw.githubusercontent.com/lucanus8-cmd/lucanus8-cmd-product-review/data/"
APPROVAL_ID = "1x0lPMvsH0G-9cOdwi1kZy5yTAhAaz9N5BluK36iS8-Q"
UA = {"User-Agent": "Mozilla/5.0 (product-review feasibility check)"}


def get(url, timeout=180):
    req = urllib.request.Request(url, headers=UA)
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), r.headers.get("Content-Type", ""), r.geturl()
        except Exception:
            if i == 2:
                raise
            time.sleep(5 * (i + 1))


def head(t):
    print("\n" + "=" * 8, t, flush=True)


def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))
    return re.sub(r"\s+", " ", s).strip()


# ═══════════ A. EMA 자료 받기 ═══════════
PAGES = ["https://www.ema.europa.eu/en/about-us/about-website/download-website-data-json-data-format",
         "https://www.ema.europa.eu/en/medicines/download-medicine-data"]


def find_links():
    links = []
    for p in PAGES:
        try:
            b, ct, _ = get(p, timeout=60)
            t = b.decode("utf-8", "ignore")
            ls = sorted(set(re.findall(r'href="([^"]+\.(?:json|xlsx))"', t)))
            ls = [urllib.parse.urljoin(p, x) for x in ls]
            print(f"{p}\n   파일 링크 {len(ls)}개:")
            for x in ls:
                print("     ", x)
            links += ls
        except Exception as e:
            print(p, "못 읽음", str(e)[:100])
        time.sleep(1)
    return links


def pick_medicines(links):
    """사람 의약품 목록 파일 고르기 — JSON 먼저, 없으면 엑셀."""
    def score(u):
        s = u.lower()
        bad = any(w in s for w in ("veterinary", "herbal", "post-authorisation", "post_authorisation", "paediatric",
                                   "orphan", "referral", "shortage", "dhpc", "psusa", "pip", "opinion", "outside",
                                   "document", "epar-document", "variation"))
        good = ("medicines" in s or "epar" in s) and not bad
        return (good, s.endswith(".json"), "medicines_json" in s or "medicines-output-medicines" in s)
    c = sorted(links, key=score, reverse=True)
    return c[:3]


def load_ema(links):
    tries = pick_medicines(links) + [
        "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json",
        "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines-report_en.xlsx"]
    for u in dict.fromkeys(tries):
        try:
            b, ct, fu = get(u, timeout=300)
        except Exception as e:
            print("못 받음", u, str(e)[:80])
            continue
        print(f"받음 {u} · {len(b) / 1e6:.1f}MB · {ct}")
        try:
            if u.endswith(".json") or "json" in ct:
                d = json.loads(b.decode("utf-8-sig"))
                if isinstance(d, dict):
                    print("   JSON 맨 위 열쇠:", list(d)[:20])
                    d = next((v for v in d.values() if isinstance(v, list) and v and isinstance(v[0], dict)), [])
                df = pd.DataFrame(d)
            else:
                raw = pd.read_excel(io.BytesIO(b), header=None, dtype=str)
                hr = next(i for i in range(min(30, len(raw))) if raw.iloc[i].astype(str).str.contains(
                    "Medicine name|Name of medicine|medicine name", case=False).any())
                df = pd.read_excel(io.BytesIO(b), header=hr, dtype=str)
            df = df.fillna("").astype(str)
            if len(df) > 500:
                return df, u
            print("   줄이 너무 적음", len(df))
        except Exception as e:
            print("   읽기 실패", type(e).__name__, str(e)[:120])
    return None, None


def col(df, *keys):
    """칸 이름을 느슨하게 찾기 (JSON 과 엑셀 칸 이름이 조금 달라도)."""
    cs = {c: re.sub(r"[^a-z]", "", c.lower()) for c in df.columns}
    for k in keys:
        k2 = re.sub(r"[^a-z]", "", k.lower())
        for c, n in cs.items():
            if n == k2:
                return c
    for k in keys:
        k2 = re.sub(r"[^a-z]", "", k.lower())
        for c, n in cs.items():
            if k2 in n:
                return c
    return None


# ═══════════ B. 성분 짝짓기 ═══════════
SPLIT = re.compile(r"\s*(?:/|;|\+|,(?=\s*[A-Za-z])|\band\b)\s*", re.I)


U2I = {}


def load_u2i():
    try:
        b, _, _ = get(RAW + "fda/fda_usan_inn_pairs.csv", timeout=60)
        p = pd.read_csv(io.BytesIO(b), dtype=str).fillna("")
        for u, i in zip(p.iloc[:, 0], p.iloc[:, 1]):
            a, c = ov.en_moiety(u), ov.en_moiety(i)
            if a and c and a != c:
                U2I[a] = c
        print(f"미국 이름 → 국제 이름 짝 {len(U2I):,}개")
    except Exception as e:
        print("미국·국제 이름 표 못 읽음", str(e)[:80])


def key(x):
    m = ov.en_moiety(x)
    m = re.sub(r"\s+l\s+pyroglutamic(\s+acid)?$", "", m)      # 'ertugliflozin l pyroglutamic acid' → 'ertugliflozin'
    return U2I.get(m, m)


def moieties(s):
    s = re.sub(r"\([^)]*\)", " ", str(s))
    return tuple(sorted({key(x) for x in SPLIT.split(s) if x.strip() and len(x.strip()) > 2}))


def korea_keys():
    kr = None
    try:
        import gsheet
        if gsheet.available():
            kr = gsheet.read_sheet(APPROVAL_ID)
            print(f"국내 허가 시트를 서비스 계정 키로 읽음: {len(kr):,}행")
    except Exception as e:
        print("키로 허가 시트 읽기 실패:", type(e).__name__, str(e)[:120])
    if kr is None:
        kr = ov.korea()
    if kr is None:
        return set(), set()
    kr.columns = [str(c).split("\n")[0].strip() for c in kr.columns]
    st = kr["상태"].astype(str).eq("정상") if "상태" in kr.columns else True
    k = kr[st]
    eng = k["주성분(영문)"].astype(str).map(lambda x: re.sub(r"\([^)]*(\)|$)", " ", x))
    split = lambda s: [x for x in re.split(r"/|;|\+|,(?=\s*[A-Za-z\[])", s) if x.strip()]  # noqa: E731
    combos = {tuple(sorted({key(x) for x in split(s)})) for s in eng if s.strip()}
    singles = {m for t in combos for m in t}
    past = set()
    if "상태" in kr.columns:
        p = kr[~kr["상태"].astype(str).eq("정상")]["주성분(영문)"].astype(str).map(lambda x: re.sub(r"\([^)]*(\)|$)", " ", x))
        past = {key(x) for s in p for x in split(s)} - singles
    print(f"국내 정상 품목 성분 조합 {len(combos):,} · 단일 성분 {len(singles):,} · 취하·만료로만 있는 성분 {len(past):,}")
    KR_COMBOS.clear()
    KR_COMBOS.update(combos)
    return singles, past


KR_COMBOS = set()   # 국내 정상 품목의 성분 조합 (복합제 판정용)


# 유럽 글은 영국식 철자 → FDA 규칙(미국식)에 맞게 바꿔서 찾는다
UK = [(r"anaemi", "anemi"), (r"haem", "hem"), (r"oesophag", "esophag"), (r"leukaem", "leukem"), (r"tumour", "tumor"),
      (r"oedema", "edema"), (r"paediatric", "pediatric"), (r"glycaemi", "glycemi"), (r"diarrhoea", "diarrhea"),
      (r"oestrogen", "estrogen"), (r"ischaemi", "ischemi"), (r"lipidaemi", "lipidemi"), (r"cholesterolaemi", "cholesterolemi"),
      (r"glyceridaemi", "glyceridemi"), (r"uricaemi", "uricemi"), (r"anaesthe", "anesthe"), (r"foetal", "fetal")]
# 유럽 적응증은 '제2형 당뇨병 치료' 식으로 병명을 바로 쓴다 (FDA 는 'glycemic control') → 유럽에만 덧붙이는 말
EU_EXTRA = {"당뇨병": ["type 2 diabetes", "type 1 diabetes", "diabetes mellitus"],
            "폐암": ["lung carcinoma", "nsclc", "sclc", "non small cell lung", "small cell lung"],
            "대장암": ["colorectal carcinoma", "carcinoma of the colon", "colon or rectum", "colon cancer", "rectal cancer", "mcrc"],
            "전립선암": ["prostate carcinoma", "prostate adenocarcinoma", "mcrpc", "mhspc", "castration resistant"],
            "유방암": ["breast carcinoma"],
            "비만": ["management of weight", "obese patients"], "심부전": ["cardiac failure"],
            "고혈압": ["blood pressure is not adequately controlled"]}
# 유럽 글에서 빼고 볼 말 (질병별)
EU_X = {"여드름": ["acne inversa"], "이상지질혈증": ["apolipoprotein"], "고혈압": ["arterialhypertens"],
        "비만": ["diet and exercise alone", "diet weight reduction and exercise", "weight reduction and exercise"]}
# 이런 말이 든 문장은 그 질병 근거로 쓰지 않는다 (합병증·동반 질환 문맥)
EU_SENT_X = {"당뇨병": ["kidney disease", "renal disease", "nephropathy", "hypoglyc", "atrial fibrillation", "macular",
                     "retinopathy", "neuropathy", "foot ulcer", "skin ulcer", "cardiovascular morbidity", "target organ damage"],
             "심부전": ["thromboembolism"], "유방암": ["skeletal"]}
# 문장 전체를 빼는 경우: 주의 문장 · '5.1 참고' 안내 · 진단용
DROP = re.compile(r"^\s*(for (trial|study) results|see section)|should not be (given|used)|not recommended|"
                  r"has not been established|not been studied", re.I)
RISK = re.compile(r"(with )?(one or more )?(additional )?risk factors?\b[^.]*", re.I)
DIAG = re.compile(r"for diagnostic use only|diagnostic agent|radiopharmaceutical|positron emission tomography|\bpet\b imaging|"
                  r"scintigraph|in vivo diagnosis", re.I)


def us_spell(s):
    for a, b in UK:
        s = re.sub(a, b, s, flags=re.I)
    return s


# ═══════════ 질병 10개 검토 (사이트 '② 적응증으로 찾기' 화면 모양 그대로) ═══════════
TEN = {   # 질병: EMA 치료 분야(MeSH) 에서 이 말이 든 제품 = 빠진 것 찾기용 대조
    "당뇨병": ["Diabetes Mellitus"], "비만": ["Obesity"], "고혈압": ["Hypertension"],
    "심부전": ["Heart Failure"], "천식": ["Asthma"], "아토피피부염": ["Dermatitis, Atopic"],
    "류마티스관절염": ["Arthritis, Rheumatoid"], "다발성경화증": ["Multiple Sclerosis"],
    "폐암": ["Lung Neoplasms", "Carcinoma, Non-Small-Cell Lung", "Small Cell Lung Carcinoma"],
    "유방암": ["Breast Neoplasms"]}
MESH_X = {"고혈압": ["Pulmonary", "Ocular"]}


def review10(auth, kr, kr_past, us_state, c):
    head("질병 10개 검토 — 화면에 나올 표")
    diag = auth["_ind"].map(lambda t: bool(DIAG.search(t)))
    a = auth[~diag].copy()

    def kind(r):
        if c["atc"] and str(r[c["atc"]]).startswith("J07") or re.search(r"vaccin", str(r[c["name_src"]]), re.I):
            return "백신"
        if c["adv"] and str(r[c["adv"]]).lower() == "yes":
            return "첨단(세포·유전자)"
        if c["gen"] and str(r[c["gen"]]).lower() == "yes":
            return "제네릭"
        if c["bio"] and str(r[c["bio"]]).lower() == "yes":
            return "바이오시밀러"
        return "신약"

    def kr_state(keys):
        st = []
        for m in keys:
            u = us_state.get(m, "")
            if m in kr or u in ("국내에도 있음", "일부 제형 없음"):
                st.append("있음")
            elif m in kr_past:
                st.append("취하·만료만")
            else:
                st.append("없음")
        if not st:
            return "확인 못 함"
        if all(x == "있음" for x in st):
            return "국내 성분 있음"
        if len(st) > 1 and any(x == "있음" for x in st):
            return "일부 성분만 있음"
        return "국내 취하·만료만" if "취하·만료만" in st else "국내 성분 없음"

    a["_kind"] = a.apply(kind, axis=1)
    a["_kr"] = a["_keys"].map(kr_state)
    a["_sent"] = a["_ind"].map(lambda t: [RISK.sub(" ", us_spell(x)) for x in re.split(r"(?<=[.;])\s*(?=[A-Z])|•", t)
                                         if len(x) > 3 and not DROP.search(x)])
    out = []
    for k, mesh in TEN.items():
        terms, _, excl = ih.TESTS[k]
        sx = [ih.norm(w) for w in EU_SENT_X.get(k, [])]
        hits = []
        for _, r in a.iterrows():
            ev = ih.hit([t for t in r["_sent"] if not any(w in ih.norm(t) for w in sx)], terms + EU_EXTRA.get(k, []),
                        excl + EU_X.get(k, []))
            if ev:
                hits.append((r, ev))
        # 성분 단위로 묶기 (같은 성분 여러 제품 → 한 줄)
        by = {}
        for r, ev in hits:
            key = " + ".join(r["_keys"]) or r[c["name_src"]]
            g = by.setdefault(key, {"성분": key, "국내": r["_kr"], "구분": set(), "유럽 제품": [], "첫 허가": "", "희귀": False,
                                    "근거": ev})
            g["구분"].add(r["_kind"])
            g["유럽 제품"].append(r[c["name"]])
            d = str(r[c["date"]])[:10] if c["date"] else ""
            if d and (not g["첫 허가"] or d[-4:] + d[3:5] + d[:2] < g["첫 허가"][-4:] + g["첫 허가"][3:5] + g["첫 허가"][:2]):
                g["첫 허가"] = d
            g["희귀"] |= bool(c["orph"]) and str(r[c["orph"]]).lower() == "yes"
        rows = sorted(by.values(), key=lambda g: (g["국내"] != "국내 성분 없음", g["성분"]))
        miss = []
        if c["area"]:
            mm = a[a[c["area"]].map(lambda t: any(m.lower() in str(t).lower() for m in mesh)
                                     and not any(x.lower() in str(t).lower() for x in MESH_X.get(k, [])))]
            got = {id(r) for r, _ in hits}
            hit_names = {r[c["name"]] for r, _ in hits}
            miss = [(r[c["name"]], " + ".join(r["_keys"]), r["_ind"][:200]) for _, r in mm.iterrows() if r[c["name"]] not in hit_names]
        n_gap = sum(1 for g in rows if g["국내"] == "국내 성분 없음")
        print(f"\n■ {k} — 성분 {len(rows)}개 (제품 {len(hits)}개) · 국내 성분 없음 {n_gap}개 · 치료분야(MeSH)엔 있는데 못 찾은 제품 {len(miss)}개")
        for g in rows:
            print(f"   {g['국내']:9s} | {g['성분'][:34]:34s} | {'/'.join(sorted(g['구분'])):8s} | {g['첫 허가']} |"
                  f" {', '.join(dict.fromkeys(g['유럽 제품']))[:40]} | {g['근거'][:130]}")
            out.append({"질병": k, "성분": g["성분"], "국내 상황": g["국내"], "구분": "/".join(sorted(g["구분"])),
                        "유럽 첫 허가": g["첫 허가"], "희귀의약품": "예" if g["희귀"] else "",
                        "유럽 제품": ", ".join(dict.fromkeys(g["유럽 제품"])), "근거 (EMA 허가 적응증)": g["근거"][:600]})
        for nm, ks, ind in miss[:15]:
            print(f"   ↳ 못 찾음? {nm[:30]} | {ks[:30]} | {ind[:150]}")
    pd.DataFrame(out).to_csv(f"{OUT}/ema_disease10.csv", index=False, encoding="utf-8-sig")
    print(f"\n저장: {OUT}/ema_disease10.csv · {len(out)}줄")


def kr_probe():
    """국내 허가 시트에서 '국내 없음'으로 나온 성분이 실제로 어떻게 적혀 있는지 보기 (공개 허가 정보만)."""
    head("국내 허가 시트 확인")
    import gsheet
    kr = gsheet.read_sheet(APPROVAL_ID)
    kr.columns = [str(c).split("\n")[0].strip() for c in kr.columns]
    print(f"{len(kr):,}행 · 칸 {list(kr.columns)}")
    print("상태 값:", Counter(kr["상태"]).most_common(8) if "상태" in kr.columns else "")
    words = ["golimumab", "골리무맙", "certolizumab", "세르톨리주맙", "sarilumab", "사릴루맙", "ofatumumab", "오파투무맙",
             "siponimod", "시포니모드", "interferon beta-1b", "인터페론베타-1b", "베타페론", "canagliflozin", "카나글리플로진",
             "ertugliflozin", "에르투글리플로진", "aliskiren", "알리스키렌", "detemir", "디터미어", "심퍼니", "심지아", "케브자라"]
    txt = kr.astype(str).apply(lambda r: " | ".join(r.values), axis=1).str.lower()
    show = [c for c in ("품목명", "상태", "전문/일반", "주성분(영문)", "주성분", "허가일", "취소/취하일자", "업체명") if c in kr.columns]
    for w in words:
        h = kr[txt.str.contains(w.lower(), regex=False)]
        print(f"\n[{w}] {len(h)}행")
        for _, r in h.head(6).iterrows():
            print("   ", {c: str(r[c])[:60] for c in show})


def main():
    os.makedirs(OUT, exist_ok=True)
    if os.environ.get("TEN") == "kr":
        kr_probe()
        return 0
    if os.environ.get("TEN", "").startswith("pat:"):      # 특허 시트 칸 보기 (공개 특허 정보, 읽기만)
        import gsheet
        sid = "1tj75vy8KcJSmz6-rAqmPpLThNt7ZymuQfi12Vbzz1CE"
        api = gsheet.svc()
        meta = gsheet._call(api.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties(title,gridProperties)"))
        tabs = [(t["properties"]["title"], t["properties"].get("gridProperties", {})) for t in meta["sheets"]]
        print("탭:", [(n, g.get("rowCount"), g.get("columnCount")) for n, g in tabs])
        pt = pd.DataFrame()
        for n, g in sorted(tabs, key=lambda x: -x[1].get("rowCount", 0)):
            vals = gsheet._call(api.spreadsheets().values().get(spreadsheetId=sid, range=f"'{n}'")).get("values", [])
            if len(vals) > 1:
                hdr = [str(c).split("\n")[0].strip() for c in vals[0]]
                pt = pd.DataFrame([(r + [""] * len(hdr))[:len(hdr)] for r in vals[1:]], columns=hdr)
                print(f"읽은 탭: {n}")
                break
        print(f"특허 시트 {len(pt):,}행 · 칸 {list(pt.columns)}")
        for c in pt.columns:
            v = pt[c].astype(str)
            nz = v[v.str.strip() != ""]
            print(f"   {c} · 빈칸 아님 {len(nz):,} · 예 {[x[:70] for x in nz.drop_duplicates().head(3)]}")
        for w in os.environ["TEN"][4:].split(","):
            h = pt[pt.astype(str).apply(lambda r: w.strip() in " ".join(r.values), axis=1)]
            print(f"\n[{w}] {len(h)}행")
            for _, r in h.drop_duplicates([c for c in pt.columns if "PATENT_NO" in c] or None).head(20).iterrows():
                print("   ", {c: str(r[c])[:90] for c in pt.columns if str(r[c]).strip()})
        return 0
    if os.environ.get("TEN", "").startswith("seq:"):      # 품목명 → 품목기준코드 (의약품안전나라 상세 화면 열기용)
        import gsheet
        kr = gsheet.read_sheet(APPROVAL_ID)
        kr.columns = [str(c).split("\n")[0].strip() for c in kr.columns]
        for w in os.environ["TEN"][4:].split(","):
            h = kr[kr["품목명"].astype(str).str.contains(w.strip(), regex=False)]
            for _, r in h.head(8).iterrows():
                print(f"{w} | {r['품목기준코드']} | {r['품목명']} | {r['업체명']} | {r['상태']} | {r['허가일자']}")
        return 0
    head("A. EMA 의약품 자료")
    links = find_links()
    df, src = load_ema(links)
    if df is None:
        print("EMA 자료를 받지 못함")
        return 2
    print(f"줄 {len(df):,} · 칸 {len(df.columns)}개:")
    for c in df.columns:
        ex = df[c][df[c].str.len() > 0].head(2).tolist()
        print(f"   {c} · 빈칸 아님 {int((df[c].str.len() > 0).sum()):,} · 예 {[x[:80] for x in ex]}")
    c_cat = col(df, "category")
    c_name = col(df, "name_of_medicine", "medicine_name", "name")
    c_inn = col(df, "international_non_proprietary_name_common_name", "international_non_proprietary_name_inn_common_name",
                "inn_common_name", "nonproprietary", "common_name")
    c_sub = col(df, "active_substance")
    c_st = col(df, "medicine_status", "authorisation_status", "status")
    c_ind = col(df, "therapeutic_indication", "condition_indication", "indication")
    c_area = col(df, "therapeutic_area_mesh", "therapeutic_area")
    c_atc = col(df, "atc_code_human", "atc_code")
    c_date = col(df, "marketing_authorisation_date", "european_commission_decision_date", "authorisation_date")
    c_gen = col(df, "generic_or_hybrid", "generic")
    c_bio = col(df, "biosimilar")
    c_orph = col(df, "orphan_medicine", "orphan")
    c_url = col(df, "medicine_url", "url")
    print("고른 칸:", dict(분류=c_cat, 이름=c_name, INN=c_inn, 성분=c_sub, 상태=c_st, 적응증=c_ind, 치료분야=c_area, ATC=c_atc,
                          허가일=c_date, 제네릭=c_gen, 바이오시밀러=c_bio, 희귀=c_orph, 주소=c_url))
    if c_cat:
        print("분류:", Counter(df[c_cat]).most_common(6))
        df = df[df[c_cat].str.lower().str.contains("human")]
    if c_st:
        print("허가 상태 (사람 약):", Counter(df[c_st]).most_common(8))
    auth = df[df[c_st].str.lower().str.contains("authori") & ~df[c_st].str.lower().str.contains("withdrawn|refus|suspend|not")] \
        if c_st else df
    print(f"사람 약 {len(df):,} · 현재 허가 {len(auth):,}")
    if c_ind:
        auth = auth.assign(_ind=auth[c_ind].map(clean))
        L = auth["_ind"].str.len()
        print(f"   적응증 글 있음 {int((L > 0).sum()):,} ({(L > 0).mean():.1%}) · 길이 중앙값 {int(L.median())}자 · 1000자 넘음 {(L > 1000).mean():.0%}")
        for x in auth["_ind"].head(3):
            print("   예:", x[:300])
    for c, nm in ((c_gen, "제네릭·하이브리드"), (c_bio, "바이오시밀러"), (c_orph, "희귀")):
        if c:
            print(f"   {nm}: {Counter(auth[c]).most_common(3)}")

    head("B. 성분 짝짓기 — 한국 허가 · 미국 FDA 표")
    load_u2i()
    name_src = c_inn or c_sub
    auth = auth.assign(_keys=auth[name_src].map(moieties))
    if c_sub and c_inn:
        empty = auth["_keys"].map(len) == 0
        auth.loc[empty, "_keys"] = auth.loc[empty, c_sub].map(moieties)
    print("성분 이름 예:", [(a[:50], k) for a, k in zip(auth[name_src].head(8), auth["_keys"].head(8))])
    kr, kr_past = korea_keys()
    try:
        b, _, _ = get(RAW + "fda_site/ing.parquet", timeout=120)
        ing = pd.read_parquet(io.BytesIO(b))
        us_single = {x for x in ing["주성분"].str.lower() if "+" not in x}
        us_state = dict(zip(ing["주성분"].str.lower(), ing["국내 상황"]))
    except Exception as e:
        print("FDA 표 못 읽음", str(e)[:100])
        us_single, us_state = set(), {}
    flip = sorted(m for m, v in us_state.items() if v == "국내 성분 없음" and "+" not in m and m in kr)
    print(f"   ⚠ 사이트 FDA 표 '국내 성분 없음' 중 괄호 지우고 보면 국내 있음: {len(flip)}개 · {flip[:80]}")
    eu_single = {m for t in auth["_keys"] for m in t if m}
    print(f"유럽 허가 성분(단일 기준) {len(eu_single):,} · 미국 판매 단일 성분 {len(us_single):,} · 국내 단일 성분 {len(kr):,}")
    in_us, in_kr = eu_single & us_single, eu_single & kr
    print(f"   유럽∩미국 {len(in_us):,} · 유럽∩국내 {len(in_kr):,}")
    gap = sorted(eu_single - kr)
    gap_us = [m for m in gap if m in us_single]
    gap_only = [m for m in gap if m not in us_single]
    print(f"   유럽에 있고 국내 없음 {len(gap):,} (그중 미국에도 있음 {len(gap_us):,} · 미국에도 없음 {len(gap_only):,})")
    # 미국 표의 '국내 상황'(더 정교한 FDA 성분 사전 짝짓기)과 어긋나는 것 = 이 간이 짝짓기의 오류 후보
    dis = [m for m in gap_us if us_state.get(m) in ("국내에도 있음", "일부 제형 없음")]
    print(f"   ⚠ 간이 짝짓기로는 국내 없음인데 FDA 표는 국내 있음: {len(dis)}개 · 예 {dis[:40]}")
    print("   국내 취하·만료로만 있는 것:", [m for m in gap if m in kr_past][:30])
    rows = []
    for _, r in auth.iterrows():
        for m in r["_keys"] or ():
            rows.append({"성분": m, "유럽 제품": r[c_name] if c_name else "", "INN": r[name_src],
                         "국내": "있음" if m in kr else ("국내 취하·만료만" if m in kr_past else "없음"),
                         "미국": "있음" if m in us_single else "없음", "FDA 표 국내 상황": us_state.get(m, ""),
                         "허가일": r[c_date][:10] if c_date else "", "치료분야": r[c_area][:120] if c_area else "",
                         "ATC": r[c_atc] if c_atc else "", "제네릭": r[c_gen] if c_gen else "",
                         "바이오시밀러": r[c_bio] if c_bio else "", "희귀": r[c_orph] if c_orph else "",
                         "적응증": r.get("_ind", "")[:600], "주소": r[c_url] if c_url else ""})
    eu = pd.DataFrame(rows)
    if os.environ.get("TEN"):
        c_adv = col(df, "advanced_therapy")
        review10(auth, kr, kr_past, us_state, {"atc": c_atc, "name_src": name_src, "adv": c_adv, "gen": c_gen, "bio": c_bio,
                                               "name": c_name, "date": c_date, "orph": c_orph, "area": c_area})
        return 0
    eu.to_csv(f"{OUT}/ema_products.csv", index=False, encoding="utf-8-sig")
    g = eu[(eu["국내"] != "있음")].drop_duplicates("성분")
    if c_gen:
        g = g[~g["제네릭"].str.lower().isin(("true", "yes", "1"))]
    print(f"\n▶ 유럽에만 있고 국내 없음 (제네릭 아닌 제품 기준) {len(g):,}개 — 무작위 40개 (미국 여부 · 허가일 · 이름 · 적응증 앞부분)")
    for _, r in g.sample(min(40, len(g)), random_state=5).iterrows():
        print(f"   {r['성분'][:32]:32s} | 미국 {r['미국']} | {r['허가일']} | {str(r['유럽 제품'])[:22]} | {r['적응증'][:90]}")

    head("C. 적응증 글로 질병 찾기 (FDA 검토 규칙) · FDA 와 맞춰 보기")
    if "_ind" not in auth.columns:
        print("적응증 칸 없음")
        return 0
    eu_txt = {}
    diag = auth["_ind"].map(lambda t: bool(DIAG.search(t)))
    print(f"진단용(영상·검사) 제품 {int(diag.sum())}개 — 질병 찾기에서 뺌 · 예 {auth.loc[diag, c_name].head(8).tolist() if c_name else ''}")
    for _, r in auth[~diag].iterrows():
        if not r["_ind"]:
            continue
        for m in r["_keys"] or ():
            eu_txt.setdefault(m, []).append(r["_ind"])
    # 적응증이 한 덩어리 글이라 문장으로 나눈다 (FDA 쪽과 같은 단위로)
    eu_sent = {m: [RISK.sub(" ", us_spell(s)) for t in ts for s in re.split(r"(?<=[.;])\s+(?=[A-Z])|•", t)
                   if len(s) > 3 and not DROP.search(s)] for m, ts in eu_txt.items()}
    try:
        b, _, _ = get(RAW + "fda_site/indx.parquet", timeout=120)
        ix = pd.read_parquet(io.BytesIO(b))
        fda_txt = ix.groupby("주성분")["문장"].apply(list).to_dict()
    except Exception as e:
        print("FDA 문장 파일 못 읽음:", str(e)[:100])
        fda_txt = {}
    both = set(eu_sent) & set(fda_txt)
    print(f"적응증 글 있는 유럽 성분 {len(eu_sent):,} · FDA 문장 있는 성분 {len(fda_txt):,} · 둘 다 {len(both):,}")
    out, tot = [], Counter()
    for k, (terms, _, excl) in ih.TESTS.items():
        sx = [ih.norm(w) for w in EU_SENT_X.get(k, [])]
        eh = {m: ih.hit([t for t in ts if not any(w in ih.norm(t) for w in sx)], terms + EU_EXTRA.get(k, []), excl + EU_X.get(k, []))
              for m, ts in eu_sent.items()}
        eh = {m: s for m, s in eh.items() if s}
        fh = {m: ih.hit(fda_txt[m], terms, excl) for m in both}
        fh = {m: s for m, s in fh.items() if s}
        a = {m for m in eh if m in fh}
        eo = {m for m in eh if m in both and m not in fh}
        fo = {m for m in fh if m not in eh}
        tot.update(eu=len(eh), agree=len(a), eu_only=len(eo), fda_only=len(fo))
        print(f"{k:10s} 유럽 {len(eh):3d} · 일치 {len(a):3d} · 유럽만 {len(eo):2d} {sorted(eo)[:5]} · FDA만 {len(fo):2d} {sorted(fo)[:5]}")
        for m, s in eh.items():
            out.append({"질병": k, "성분": m, "유럽 근거": s[:400],
                        "FDA": "같음" if m in a else ("FDA 없음" if m in eo else "미국 라벨 없음"), "FDA 문장": fh.get(m, "")[:300]})
        for m in fo:
            out.append({"질병": k, "성분": m, "유럽 근거": "", "FDA": "FDA만", "FDA 문장": fh[m][:300],
                        "유럽 적응증": " | ".join(eu_txt.get(m, []))[:400]})
    print(f"\n합계: 유럽 찾은 쌍 {tot['eu']:,} · 일치 {tot['agree']:,} · 유럽만 {tot['eu_only']:,} · FDA만 {tot['fda_only']:,}")
    d = pd.DataFrame(out)
    d.to_csv(f"{OUT}/ema_ind_pairs.csv", index=False, encoding="utf-8-sig")

    head("D. 검토 표본")
    x = d[d["FDA"] != "FDA만"]
    print(f"▶ 유럽에서 찾은 쌍 무작위 60 (정확도 보기)")
    for _, r in x.sample(min(60, len(x)), random_state=11).iterrows():
        print(f"   {r['질병']} | {r['성분'][:28]} | {r['FDA']} | {r['유럽 근거'][:150]}")
    for lab_, n in (("FDA 없음", 25), ("FDA만", 25)):
        y = d[d["FDA"] == lab_]
        y = y.sample(min(n, len(y)), random_state=3) if len(y) else y
        print(f"\n▶ 표본 [{lab_}] {len(d[d['FDA'] == lab_])}쌍 중 {len(y)}")
        for _, r in y.iterrows():
            print(f"   {r['질병']} | {r['성분'][:28]} | EU: {str(r['유럽 근거'])[:120]} | "
                  f"{'US: ' + str(r['FDA 문장'])[:110] if r['FDA 문장'] else ''}"
                  f"{' EU전체: ' + str(r.get('유럽 적응증', ''))[:160] if lab_ == 'FDA만' else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
