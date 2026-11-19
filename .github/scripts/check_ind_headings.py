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
                     r"warning|precaution|monitor)\b", re.I)


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
    "폐동맥고혈압": (["pulmonary arterial hypertension", "pah"], "", []),
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
    "HIV": (["hiv"], "", []),
    "대상포진": (["herpes zoster", "shingles"], "", []),
    "인플루엔자": (["influenza"], "", []),
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
    "우울증": (["depressive disorder", "depression"], "", []),
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
    "빈혈": (["anemia"], "", []),
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
        if any(w in n for w in terms):
            return str(x)
    return ""


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
    pd.DataFrame(ev_rows).to_csv("out/ind_evidence.csv", index=False, encoding="utf-8-sig")
    os.makedirs("out", exist_ok=True)
    df.assign(소제목=df["소제목"].map(" | ".join), 허가문장=df["허가문장"].map(" | ".join)).drop(
        columns=["적응증 글"]).to_csv("out/ind_headings.csv", index=False, encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
