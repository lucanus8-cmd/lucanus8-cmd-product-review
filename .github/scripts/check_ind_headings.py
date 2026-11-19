"""FDA 라벨의 '적응증 소제목'(1.1 ○○, 1.2 ○○)만으로 질병을 찾으면 얼마나 잘 맞는지 검토한다. 읽기만 한다.

비교: ⓗ 소제목만 · ⓡ 요약 문구 '…( 1.x )' 앞 구절 · ⓣ 적응증 글 전체(지금 사이트 방식)
기준: WHO ATC 질환군(③ 목록과 같은 분류)에 든 성분을 얼마나 잡는지 / 엉뚱한 약이 섞이는지
"""
import io
import json
import os
import random
import re
import sys
import time
import urllib.request
import zipfile
from collections import Counter

import pandas as pd

RAW = "https://raw.githubusercontent.com/lucanus8-cmd/lucanus8-cmd-product-review/data/fda_site/"


def get(url, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "product-review-check"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def stream(parts):
    import ijson
    for p in parts:
        z = zipfile.ZipFile(io.BytesIO(get(p["file"], timeout=900)))
        for fn in z.namelist():
            with z.open(fn) as fp:
                yield from ijson.items(fp, "results.item")


def norm(s):
    s = str(s).lower().replace("’", "'")
    s = re.sub(r"'s\b", "", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


STOP = re.compile(r"\b(?:is|are)\s+(?:also\s+)?indicated\b|\bindicated\b|\bLimitations? of Use\b", re.I)


def headings(t, brands, generics):
    """'1.1 Primary Hyperlipidemia LIPITOR is indicated …' → 'Primary Hyperlipidemia'"""
    out = []
    for m in re.finditer(r"(?:^|\s)1\.(\d{1,2})\s+(?=[A-Za-z(])", t):
        ch = t[m.end():m.end() + 260]
        cut = len(ch)
        mm = STOP.search(ch)
        if mm:
            cut = mm.start()
        for b in brands:
            i = ch.find(b)
            if 0 < i < cut:
                cut = i
        low = ch.lower()
        for g in generics:
            i = low.find(g)
            if 3 < i < cut:
                cut = i
        nx = re.search(r"\s1\.\d{1,2}\s", ch)
        if nx and nx.start() < cut:
            cut = nx.start()
        h = ch[:cut].strip(" :-–—,;.•")
        h = re.sub(r"(\s+(The|This|A|An|In|For|Use|Oral|Intramuscular|Intravenous|Injection|Injectable|Tablets?|Capsules?))+$", "", h)
        if 3 <= len(h) <= 160:
            out.append(h)
    return list(dict.fromkeys(out))


def refs(t):
    """요약형 '… treatment of deep vein thrombosis (DVT) ( 1.2 )' → 그 앞 구절"""
    out = []
    segs = re.split(r"\(\s*1\.\d{1,2}(?:\s*,\s*1\.\d{1,2})*\s*\)", t)
    for seg in segs[:-1]:                       # 마지막 조각 뒤에는 표시가 없다
        p = re.split(r"[•;:]|\.\s", seg)[-1].strip(" ,.-")[-220:]
        if len(p) >= 6:
            out.append(p)
    return list(dict.fromkeys(out))


CAUTION = re.compile(r"\b(not indicated|consider|potential for|not been established|not recommended|not approved|"
                     r"should not|should be part of|risk management|as appropriate|caution|contraindicat\w*|absolute benefit|"
                     r"independent of their|expected to benefit|for example, patients with|"
                     r"warning|precaution|monitor|not effective|not of value|not been shown|not been demonstrated|"
                     r"because of the risks?|substantial risk|primarily strokes and myocardial|permit the administration of lower)\b", re.I)


def clauses(t):
    """소제목이 없는 라벨: '사용 제한(Limitations of Use)' 앞까지에서, 주의 문장을 뺀 '허가 문장'만"""
    t = re.split(r"\bLimitations? of Use\b|\bImportant Limitations?\b", t, flags=re.I)[0]
    parts = [p.strip() for p in re.split(r"(?<=[.])\s+(?=[A-Z])|•", t) if p.strip()]
    return [p for p in parts if not CAUTION.search(p)]


_HTN_X = ["ocular hypertension", "pulmonary arterial hypertension", "pulmonary hypertension",
          "intracranial hypertension", "portal hypertension", "intraocular"]
_LIPID = ["hyperlipidemia", "hypercholesterolemia", "dyslipidemia", "hypertriglyceridemia", "ldl",
          "hyperlipoproteinemia", "triglyceride", "chylomicronemia", "lipoprotein", "sitosterolemia"]
_DM = ["glycemic control", "glycaemic control", "treatment of type 2 diabetes", "treatment of type 1 diabetes",
       "treatment of diabetes"]
TESTS = {   # 한글: (라벨에서 찾을 말들, ATC 질환군 글자, 빼고 볼 말들)
    # 순환기·대사
    "고혈압": (["hypertension", "lower blood pressure", "lowering blood pressure"], "고혈압", _HTN_X),
    "이상지질혈증": (_LIPID, "이상지질혈증", []),
    "당뇨병": (_DM, "당뇨병", []),
    "비만": (["obesity", "weight management", "weight reduction", "weight loss"], "비만", []),
    "심부전": (["heart failure"], "", []),
    "협심증": (["angina"], "", []),
    "심근경색": (["myocardial infarction"], "", []),
    "심방세동": (["atrial fibrillation"], "", []),
    "뇌졸중": (["stroke"], "", []),
    "심부정맥혈전증": (["deep vein thrombosis", "venous thromboembolism"], "", []),
    "폐동맥고혈압": (["pulmonary arterial hypertension"], "", []),
    "만성신장병": (["chronic kidney disease"], "", []),
    # 호흡기
    "천식": (["asthma"], "천식", []),
    "COPD": (["chronic obstructive pulmonary", "copd"], "", []),
    "알레르기비염": (["allergic rhinitis"], "", []),
    "특발성폐섬유증": (["idiopathic pulmonary fibrosis"], "", []),
    # 소화기
    "위식도역류질환": (["gastroesophageal reflux", "gerd", "erosive esophagitis"], "위산", []),
    "소화성궤양": (["duodenal ulcer", "gastric ulcer", "peptic ulcer"], "", []),
    "크론병": (["crohn"], "", []),
    "궤양성대장염": (["ulcerative colitis"], "", []),
    "과민성대장증후군": (["irritable bowel"], "", []),
    "변비": (["constipation"], "변비", []),
    "구역·구토": (["nausea and vomiting", "vomiting"], "구토", []),
    # 감염
    "C형간염": (["hepatitis c", "hcv"], "", []),
    "B형간염": (["hepatitis b", "hbv"], "", []),
    "HIV": (["treatment of hiv", "treatment of human immunodeficiency", "other antiretroviral", "antiretroviral agents",
             "sexually acquired hiv", "hiv 1 pre exposure", "pre exposure prophylaxis"], "", []),
    "대상포진": (["herpes zoster", "shingles"], "", []),
    "인플루엔자": (["influenza"], "", ["haemophilus influenzae", "h influenzae", "hemophilus influenzae", "influenzae"]),
    "요로감염": (["urinary tract infection"], "", []),
    "결핵": (["tuberculosis"], "결핵", []),
    # 근골격·피부·면역
    "류마티스관절염": (["rheumatoid arthritis"], "", []),
    "골관절염": (["osteoarthritis"], "", []),
    "골다공증": (["osteoporosis"], "골다공증", []),
    "통풍": (["gout", "hyperuricemia"], "통풍", []),
    "강직성척추염": (["ankylosing spondylitis"], "", []),
    "건선": (["psoriasis", "psoriatic"], "", []),
    "아토피피부염": (["atopic dermatitis"], "", []),
    "여드름": (["acne"], "여드름", []),
    "루푸스": (["lupus"], "", []),
    "다발성경화증": (["multiple sclerosis"], "", []),
    # 정신·신경
    "우울증": (["depressive disorder", "depression"], "", ["respiratory depression", "cns depression", "nervous system depression",
                                                     "depression of the central nervous", "myocardial depression",
                                                     "bone marrow depression", "respiratory and or central nervous system depression",
                                                     "opioid depression", "narcotic depression", "postanesthesia",
                                                     "drug induced depression", "depression of respiration"]),
    "조현병": (["schizophrenia"], "", []),
    "양극성장애": (["bipolar"], "", []),
    "불안장애": (["anxiety"], "", []),
    "불면증": (["insomnia"], "", []),
    "ADHD": (["attention deficit"], "", []),
    "알츠하이머병": (["alzheimer"], "", []),
    "파킨슨병": (["parkinson"], "파킨슨", []),
    "뇌전증": (["epilep", "seizure"], "뇌전증", []),
    "편두통": (["migraine"], "", []),
    "신경병증성통증": (["neuropathic pain", "postherpetic neuralgia", "diabetic peripheral neuropathy"], "", []),
    # 비뇨·안과·내분비·혈액
    "전립선비대증": (["benign prostatic hyperplasia", "bph"], "", []),
    "과민성방광": (["overactive bladder"], "", []),
    "발기부전": (["erectile dysfunction"], "", []),
    "녹내장": (["glaucoma", "ocular hypertension"], "", []),
    "황반변성": (["macular degeneration"], "", []),
    "갑상선기능저하증": (["hypothyroidism"], "", []),
    "빈혈": (["anemia"], "", ["refractory anemia", "aplastic anemia and or liver failure"]),
    "혈우병": (["hemophilia"], "", []),
    # 암
    "유방암": (["breast cancer"], "", []),
    "폐암": (["lung cancer"], "", []),
    "대장암": (["colorectal cancer"], "", []),
    "전립선암": (["prostate cancer"], "", []),
    "다발골수종": (["multiple myeloma"], "", []),
    "만성림프구성백혈병": (["chronic lymphocytic leukemia"], "", []),
    "간세포암": (["hepatocellular carcinoma"], "", []),
}


_EG = re.compile(r"\((?:e\.?g\.?|for example|such as|including|i\.e\.)[^)]*\)|\b(?:e\.g\.|for example|such as)[^.;:]*", re.I)


def hit(texts, terms, excl=()):
    """찾으면 그 문장(근거), 못 찾으면 빈 값. 괄호 속 '예: ○○' 나열과 빼고 볼 말은 지우고 찾는다."""
    for x in texts:
        n = norm(_EG.sub(" ", str(x)))
        for e in excl:
            n = n.replace(norm(e), " ")
        if any((re.search(r"\b" + re.escape(w) + r"\b", n) if len(w) <= 4 else w in n) for w in terms):
            return str(x)
    return ""


# ── MED-RT (미국 보훈부·국립의학도서관): 성분 → '치료함/예방함' 질병 (검증용) ──
MEDRT_KEY = {   # 질병: (MED-RT 질병 이름에서 찾을 말들, 빼고 볼 말들)
    "고혈압": (["hypertension"], ["pulmonary", "ocular", "intracranial", "portal", "renovascular hypertension, malignant"]),
    "이상지질혈증": (["hyperlipid", "hypercholesterol", "dyslipid", "hypertriglycerid", "lipoprotein", "hyperlipoprotein"], []),
    "당뇨병": (["diabetes mellitus"], ["insipidus"]), "비만": (["obesity", "overweight"], []),
    "심부전": (["heart failure"], []), "협심증": (["angina"], []), "심근경색": (["myocardial infarction"], []),
    "심방세동": (["atrial fibrillation"], []), "뇌졸중": (["stroke"], []),
    "심부정맥혈전증": (["venous thrombosis", "thromboembolism", "venous thromboembolism", "pulmonary embolism"], []),
    "폐동맥고혈압": (["pulmonary arterial hypertension", "hypertension, pulmonary"], []),
    "만성신장병": (["renal insufficiency, chronic", "kidney failure, chronic", "chronic kidney"], []),
    "천식": (["asthma"], []), "COPD": (["pulmonary disease, chronic obstructive", "emphysema", "bronchitis, chronic"], []),
    "알레르기비염": (["rhinitis, allergic", "allergic rhinitis"], []), "특발성폐섬유증": (["idiopathic pulmonary fibrosis"], []),
    "위식도역류질환": (["gastroesophageal reflux", "esophagitis"], []),
    "소화성궤양": (["peptic ulcer", "duodenal ulcer", "stomach ulcer"], []), "크론병": (["crohn"], []),
    "궤양성대장염": (["colitis, ulcerative"], []), "과민성대장증후군": (["irritable bowel"], []),
    "변비": (["constipation"], []), "구역·구토": (["vomiting", "nausea"], []),
    "C형간염": (["hepatitis c"], []), "B형간염": (["hepatitis b"], []), "HIV": (["hiv infections", "hiv-1", "hiv"], []),
    "대상포진": (["herpes zoster"], []), "인플루엔자": (["influenza, human", "influenza"], ["haemophilus"]),
    "요로감염": (["urinary tract infections"], []), "결핵": (["tuberculosis"], []),
    "류마티스관절염": (["arthritis, rheumatoid"], []), "골관절염": (["osteoarthritis"], []),
    "골다공증": (["osteoporosis"], []), "통풍": (["gout"], []), "강직성척추염": (["spondylitis, ankylosing"], []),
    "건선": (["psoriasis", "arthritis, psoriatic"], []), "아토피피부염": (["dermatitis, atopic"], []),
    "여드름": (["acne"], []), "루푸스": (["lupus"], []), "다발성경화증": (["multiple sclerosis"], []),
    "우울증": (["depressive disorder", "depression"], []), "조현병": (["schizophrenia"], []),
    "양극성장애": (["bipolar"], []), "불안장애": (["anxiety"], []), "불면증": (["sleep initiation", "insomnia"], []),
    "ADHD": (["attention deficit"], []), "알츠하이머병": (["alzheimer"], []), "파킨슨병": (["parkinson"], []),
    "뇌전증": (["epilep", "seizure"], []), "편두통": (["migraine"], []), "신경병증성통증": (["neuralgia", "neuropath"], []),
    "전립선비대증": (["prostatic hyperplasia"], []), "과민성방광": (["urinary bladder, overactive"], []),
    "발기부전": (["erectile dysfunction"], []), "녹내장": (["glaucoma", "ocular hypertension"], []),
    "황반변성": (["macular degeneration"], []), "갑상선기능저하증": (["hypothyroidism"], []),
    "빈혈": (["anemia"], []), "혈우병": (["hemophilia"], []), "유방암": (["breast neoplasms"], []),
    "폐암": (["lung neoplasms", "non-small-cell lung", "small cell lung"], []),
    "대장암": (["colorectal neoplasms", "colonic neoplasms", "rectal neoplasms"], []),
    "전립선암": (["prostatic neoplasms"], []), "다발골수종": (["multiple myeloma"], []),
    "만성림프구성백혈병": (["leukemia, lymphocytic, chronic"], []),
    "간세포암": (["carcinoma, hepatocellular", "liver neoplasms"], []),
}
KNOWN_BAD = [("비만", "gemfibrozil"), ("심근경색", "azilsartan medoxomil"), ("심근경색", "hydrochlorothiazide + valsartan"),
             ("뇌졸중", "eplerenone"), ("폐동맥고혈압", "sepiapterin"), ("구역·구토", "carbidopa"),
             ("B형간염", "berahyaluronidase alfa + pembrolizumab"), ("HIV", "desloratadine"), ("인플루엔자", "cefuroxime"),
             ("인플루엔자", "dexamethasone + neomycin + polymyxin b"), ("인플루엔자", "gatifloxacin"), ("불면증", "buspirone"),
             ("뇌전증", "clozapine"), ("빈혈", "cedazuridine + decitabine"), ("유방암", "dexrazoxane")]


def medrt(names):
    """성분 이름 → {(MED-RT 질병 이름, 관계)}. RxNav(미국 국립의학도서관) 공개 API."""
    from concurrent.futures import ThreadPoolExecutor
    import urllib.parse
    try:
        pairs = pd.read_csv(io.BytesIO(get(RAW.replace("fda_site/", "fda/fda_usan_inn_pairs.csv"))), dtype=str)
        i2u = dict(zip(pairs.iloc[:, 1].str.lower(), pairs.iloc[:, 0].str.lower()))
    except Exception:
        i2u = {}
    i2u.update({"paracetamol": "acetaminophen", "salbutamol": "albuterol", "glibenclamide": "glyburide",
                "colestyramine": "cholestyramine", "chlortalidone": "chlorthalidone", "indometacin": "indomethacin",
                "valproic": "valproic acid", "zoledronic": "zoledronic acid", "risedronic": "risedronate",
                "ibandronic": "ibandronate", "alendronic": "alendronate", "tranexamic": "tranexamic acid",
                "meclofenamic": "meclofenamate", "dicycloverine": "dicyclomine", "etacrynic": "ethacrynic acid",
                "metamfetamine": "methamphetamine", "amfetamine": "amphetamine", "dexamfetamine": "dextroamphetamine",
                "cromoglicic": "cromolyn", "orciprenaline": "metaproterenol", "isoprenaline": "isoproterenol",
                "benzfetamine": "benzphetamine", "tiotixene": "thiothixene", "torasemide": "torsemide"})

    def one(nm):
        out = set()
        for q in dict.fromkeys([i2u.get(nm, nm), nm]):
            try:
                j = json.loads(get("https://rxnav.nlm.nih.gov/REST/rxclass/class/byDrugName.json?"
                                   + urllib.parse.urlencode({"drugName": q, "relaSource": "MEDRT"}), timeout=40))
            except Exception:
                continue
            for it in ((j.get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo") or []):
                rel = it.get("rela", "")
                c = (it.get("rxclassMinConceptItem") or {})
                if rel in ("may_treat", "may_prevent") and c.get("classType", "DISEASE") == "DISEASE":
                    out.add((c.get("className", ""), rel))
            if out:
                break
        return nm, out
    t0 = time.time()
    with ThreadPoolExecutor(6) as ex:
        res = dict(ex.map(one, names))
    print(f"MED-RT 조회 {len(names):,}개 성분 · 질병 연결 있는 성분 {sum(1 for v in res.values() if v):,} · {time.time() - t0:.0f}초")
    return res


def main():
    prod = pd.read_parquet(io.BytesIO(get(RAW + "prod.parquet")))
    ing = pd.read_parquet(io.BytesIO(get(RAW + "ing.parquet")))
    a2i = prod.drop_duplicates("신청번호").set_index("신청번호")["주성분"]
    kind = prod.drop_duplicates("신청번호").set_index("신청번호")["구분"]
    want = set(prod["신청번호"])
    print(f"미국 판매 중 신청 {len(want):,}건 (신약 {int((kind != 'ANDA').sum()):,} · 제네릭 {int((kind == 'ANDA').sum()):,})"
          f" · 성분 {prod['주성분'].nunique():,}개")
    idx = json.loads(get("https://api.fda.gov/download.json", timeout=60))["results"]
    lab, t0 = {}, time.time()
    for r in stream(idx["drug"]["label"]["partitions"]):
        of = r.get("openfda") or {}
        apps = [a for a in (of.get("application_number") or []) if a in want]
        if not apps:
            continue
        txt = re.sub(r"\s+", " ", " ".join(r.get("indications_and_usage") or [])).strip()
        txt = re.sub(r"^\s*\d*\.?\s*INDICATIONS?\s*(AND|&)\s*USAGE\s*", "", txt, flags=re.I)
        et = r.get("effective_time", "")
        for a in apps:
            if txt and et >= lab.get(a, ("", ""))[0]:
                lab[a] = (et, txt, [b.upper() for b in of.get("brand_name") or []],
                          [g.lower() for g in of.get("generic_name") or []])
    print(f"라벨 찾은 신청 {len(lab):,} / {len(want):,} · {time.time() - t0:.0f}초")

    rows = []
    for a, (et, txt, br, gn) in lab.items():
        h = headings(txt, br, gn)
        rows.append({"신청번호": a, "구분": kind.get(a, ""), "주성분": a2i.get(a, ""), "라벨일": et, "상표": ", ".join(br[:2]),
                     "소제목": h, "허가문장": clauses(txt), "적응증 글": txt})
    df = pd.DataFrame(rows)
    # 성분마다 쓸 라벨: 신약(NDA·BLA) 라벨이 있으면 그것, 없으면 제네릭(ANDA) 라벨 최근 3개
    use = []
    for n, g in df.groupby("주성분"):
        nd = g[g["구분"] != "ANDA"]
        use.append(nd if len(nd) else g.sort_values("라벨일", ascending=False).head(3))
    df = pd.concat(use, ignore_index=True)
    allm = set(prod["주성분"])
    via = df.groupby("주성분")["구분"].agg(lambda x: "신약" if (x != "ANDA").any() else "제네릭")
    print(f"■ 라벨이 있는 성분 {len(via):,} / {len(allm):,} ({len(via) / len(allm):.0%}) · 그중 제네릭 라벨로 보는 성분 {(via == '제네릭').sum():,}")
    print(f"■ 소제목이 있는 라벨 {(df['소제목'].map(len) > 0).mean():.0%}")
    print("■ 라벨이 없는 성분 예:", sorted(allm - set(via.index))[:25])
    G = {n: (sum(g["소제목"], []) + sum(g["허가문장"], []), list(g["적응증 글"])) for n, g in df.groupby("주성분")}
    ing_grp = ing.set_index("주성분")["질환군"]
    ev_rows = []
    summ = []
    for ko, (terms, atc, excl) in TESTS.items():
        terms = [norm(w) for w in terms]
        HC = {}
        for n, (hc, _) in G.items():
            e = hit(hc, terms, excl)
            if e:
                HC[n] = e
        T = {n for n, (_, t) in G.items() if hit(t, terms, excl)}
        A = set(ing.loc[ing["질환군"].str.contains(atc, regex=False), "주성분"]) if atc else set()
        summ.append((ko, len(HC), len(T), f"{len(A & set(HC))}/{len(A)}" if atc else "-"))
        print(f"\n◆ {ko}: {len(HC)}개 (글 전체로 찾으면 {len(T)})"
              + (f" · ATC '{atc}' 중 잡힘 {len(A & set(HC))}/{len(A)} · 못 잡음 {sorted(A - set(HC))[:12]}" if atc else ""))
        print("   잡힌 성분:", ", ".join(sorted(HC))[:1500])
        print("   글 전체엔 있는데 뺀 것:", sorted(T - set(HC))[:20])
        for n, e in HC.items():
            ev_rows.append({"질병": ko, "성분": n, "질환군": ing_grp.get(n, ""), "근거 문장": e[:400]})
    print("\n■ 요약 (질병 · 잡힌 성분 · 글 전체로 찾을 때 · ATC 질환군 중 잡힘)")
    for x in summ:
        print("  ", x)
    os.makedirs("out", exist_ok=True)
    ev = pd.DataFrame(ev_rows)
    if os.environ.get("MEDRT") == "1":
        comps = sorted({x.strip() for n in set(ev["성분"]) | set(prod["주성분"]) for x in str(n).split(" + ") if x.strip()})
        try:
            r0 = get("https://rxnav.nlm.nih.gov/REST/rxclass/class/byDrugName.json?drugName=atorvastatin&relaSource=MEDRT", 40)
            print("MED-RT 응답 예 (atorvastatin):", r0[:500])
        except Exception as e:
            print("MED-RT 접속 실패:", type(e).__name__, str(e)[:120])
        md = medrt(comps)
        first = ing.set_index("주성분")["미국 첫 허가"].astype(str)

        def med_for(n):
            return {c for x in str(n).split(" + ") for c in md.get(x.strip(), set())}

        def in_med(n, ko):
            keys, excl = MEDRT_KEY[ko]
            for c, _ in med_for(n):
                cl = c.lower()
                if any(e in cl for e in excl):
                    continue
                if any(k in cl for k in keys):
                    return c
            return ""
        has_med = {n: bool(med_for(n)) for n in set(ev["성분"])}
        print(f"\n■ FDA 결과에 나온 성분 중 MED-RT 자료가 아예 있는 성분 {sum(has_med.values())}/{len(has_med)}")
        out = []
        tot = Counter()
        for _, r in ev.iterrows():
            n, ko = r["성분"], r["질병"]
            m = in_med(n, ko)
            yr = first.get(n, "")[:4]
            if m:
                cls = "✅ 일치"
            elif not has_med.get(n):
                cls = "⚪ MED-RT에 성분 없음"
            elif yr >= "2024":
                cls = "⚪ 최근 승인(2024~)"
            elif " + " in n:
                cls = "⚪ 복합제"
            else:
                cls = "⚠️ FDA 라벨에만"
            tot[cls] += 1
            out.append({**r.to_dict(), "MED-RT 일치 병명": m, "판정": cls})
        o = pd.DataFrame(out)
        o.to_csv("out/ind_medrt.csv", index=False, encoding="utf-8-sig")
        print("■ 판정 합계:", dict(tot), f"· 일치율 {tot['✅ 일치'] / max(1, len(o)):.0%}")
        print("■ 질병별 (✅ / ⚠️ / ⚪):")
        for ko, g in o.groupby("질병", sort=False):
            c = g["판정"].value_counts()
            print(f"   {ko}: {c.get('✅ 일치', 0)} / {c.get('⚠️ FDA 라벨에만', 0)} / {len(g) - c.get('✅ 일치', 0) - c.get('⚠️ FDA 라벨에만', 0)}"
                  f" · ⚠️ {list(g.loc[g['판정'] == '⚠️ FDA 라벨에만', '성분'])[:12]}")
        print("\n■ 제가 직접 틀렸다고 본 15쌍은 어떻게 판정됐나:")
        for ko, n in KNOWN_BAD:
            x = o[(o["질병"] == ko) & (o["성분"] == n)]
            print(f"   {ko} · {n}: {x['판정'].iloc[0] if len(x) else '(이번엔 안 잡힘)'} {x['MED-RT 일치 병명'].iloc[0] if len(x) else ''}")
        # MED-RT 에만 있는 것 (FDA 라벨 방식이 놓쳤을 수 있는 것)
        print("\n■ MED-RT에는 그 병이 있는데 FDA 라벨 방식이 안 잡은 성분 (질병별 예):")
        got = set(zip(o["질병"], o["성분"]))
        for ko in TESTS:
            miss = [n for n in sorted(set(prod["주성분"])) if (ko, n) not in got and in_med(n, ko)]
            print(f"   {ko}: {len(miss)} · {miss[:15]}")
    ev.to_csv("out/ind_evidence.csv", index=False, encoding="utf-8-sig")
    os.makedirs("out", exist_ok=True)
    df.assign(소제목=df["소제목"].map(" | ".join), 허가문장=df["허가문장"].map(" | ".join)).drop(
        columns=["적응증 글"]).to_csv("out/ind_headings.csv", index=False, encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
