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
                     r"should not|warning|precaution|monitor)\b", re.I)


def clauses(t):
    """소제목이 없는 라벨: '사용 제한(Limitations of Use)' 앞까지에서, 주의 문장을 뺀 '허가 문장'만"""
    t = re.split(r"\bLimitations? of Use\b|\bImportant Limitations?\b", t, flags=re.I)[0]
    parts = [p.strip() for p in re.split(r"(?<=[.])\s+(?=[A-Z])|•", t) if p.strip()]
    return [p for p in parts if not CAUTION.search(p)]


TESTS = {   # 한글: (라벨에서 찾을 말들, ATC 질환군 글자)
    "이상지질혈증": (["hyperlipidemia", "hypercholesterolemia", "dyslipidemia", "hypertriglyceridemia", "lipid",
                 "ldl", "chylomicronemia", "lipoprotein", "sitosterolemia"], "이상지질혈증"),
    "고혈압": (["hypertension"], "고혈압"),
    "폐동맥고혈압": (["pulmonary arterial hypertension", "pah"], ""),
    "통풍": (["gout", "hyperuricemia"], "통풍"),
    "당뇨병": (["diabetes", "glycemic control"], "당뇨병"),
    "편두통": (["migraine"], ""),
    "류마티스관절염": (["rheumatoid arthritis"], ""),
    "건선": (["psoriasis", "psoriatic"], ""),
    "천식": (["asthma"], "천식"),
    "골다공증": (["osteoporosis"], "골다공증"),
    "조현병": (["schizophrenia"], ""),
    "심방세동": (["atrial fibrillation"], ""),
    "뇌전증": (["epilep", "seizure"], "뇌전증"),
    "파킨슨병": (["parkinson"], "파킨슨"),
}


def hit(texts, terms):
    return any(any(w in norm(x) for w in terms) for x in texts)


def main():
    prod = pd.read_parquet(io.BytesIO(get(RAW + "prod.parquet")))
    ing = pd.read_parquet(io.BytesIO(get(RAW + "ing.parquet")))
    a2i = prod.drop_duplicates("신청번호").set_index("신청번호")["주성분"]
    want = set(prod.loc[prod["구분"] != "ANDA", "신청번호"])
    print(f"미국 판매 중 신약 신청(NDA·BLA) {len(want):,}건 · 성분 {prod.loc[prod['구분'] != 'ANDA', '주성분'].nunique():,}개")
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
        h, rf = headings(txt, br, gn), refs(txt)
        c = [] if h else clauses(txt)
        rows.append({"신청번호": a, "주성분": a2i.get(a, ""), "상표": ", ".join(br[:2]), "소제목": " | ".join(h),
                     "허가문장": " | ".join(c),
                     "요약구절": " | ".join(rf), "적응증 글": txt})
    df = pd.DataFrame(rows)
    has_h, has_r = df["소제목"] != "", df["요약구절"] != ""
    by_ing = df.groupby("주성분").agg(h=("소제목", lambda x: any(x != "")), r=("요약구절", lambda x: any(x != "")))
    print(f"\n■ 소제목이 있는 라벨: 신청 {has_h.mean():.0%} ({has_h.sum():,}) · 성분 기준 {by_ing['h'].mean():.0%}")
    print(f"■ 요약구절('…( 1.x )')만 있는 라벨: {(~has_h & has_r).mean():.0%} · 둘 다 없는 라벨: {(~has_h & ~has_r).mean():.0%}"
          f" · 성분 기준 둘 다 없음 {(~by_ing['h'] & ~by_ing['r']).mean():.0%}")
    random.seed(3)
    print("\n■ 소제목 예 (무작위 40):")
    for x in random.sample([y for s in df.loc[has_h, "소제목"] for y in s.split(" | ")], 40):
        print("   ·", x)
    print("\n■ 소제목 없는 라벨 예 (무작위 8, 앞 220자):")
    for _, x in df[~has_h].sample(8, random_state=4).iterrows():
        print(f"   · {x['주성분']} [{x['상표']}] {x['적응증 글'][:220]}")
    hc = Counter(norm(y) for s in df.loc[has_h, "소제목"] for y in s.split(" | "))
    print("\n■ 자주 나오는 소제목 30:", hc.most_common(30))

    ing_grp = ing.set_index("주성분")["질환군"]
    for ko, (terms, atc) in TESTS.items():
        g = df.groupby("주성분")
        H = {n for n, x in g if hit(x["소제목"].str.split(" \\| ").sum(), terms)}
        R = {n for n, x in g if hit(x["요약구절"].str.split(" \\| ").sum(), terms)}
        T = {n for n, x in g if hit(x["적응증 글"], terms)}
        A = set(ing.loc[ing["질환군"].str.contains(atc, regex=False), "주성분"]) if atc else set()
        C = {n for n, x in g if hit(x["허가문장"].str.split(" \\| ").sum(), terms)}
        HC = H | C
        print(f"   ▶ 소제목+허가문장(소제목 없을 때만) {len(HC)}" + (f" · ATC 성분 중 잡힘 {len(A & HC)}/{len(A)}" if atc else "")
              + f" · olanzapine {'포함' if 'olanzapine' in HC else '없음'}")
        if atc:
            print("     ATC인데 못 잡음:", sorted(A - HC)[:20])
            print("     잡혔는데 ATC 다름:", [(n, str(ing_grp.get(n, ''))[:20]) for n in sorted(HC - A)[:25]])
        else:
            print("     글 전체엔 있는데 못 잡음:", sorted(T - HC)[:20], "· 새로 잡힌 것:", sorted(HC - T)[:10])
        print(f"\n◆ {ko}: 소제목 {len(H)} · 소제목+요약구절 {len(H | R)} · 글 전체 {len(T)}"
              + (f" · ATC '{atc}' 성분 {len(A)} (그중 소제목으로 잡힘 {len(A & H)}, +요약구절 {len(A & (H | R))})" if atc else ""))
        print("   글 전체로만 잡히는 것(소제목·요약구절엔 없음):", sorted(T - H - R)[:25])
        if atc:
            miss = sorted(A - H - R)
            print("   ATC 질환군인데 소제목·요약구절로 못 잡음:", miss[:25])
        odd = sorted(n for n in (H | R) if atc and n not in A)
        print("   소제목·요약구절로 잡혔는데 ATC 질환군 다름:", [(n, str(ing_grp.get(n, ""))[:22]) for n in odd[:20]])
        print("   olanzapine 포함?", "olanzapine" in (H | R), "(글 전체:", "olanzapine" in T, ")")
    os.makedirs("out", exist_ok=True)
    df.drop(columns=["적응증 글"]).to_csv("out/ind_headings.csv", index=False, encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
