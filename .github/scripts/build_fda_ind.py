"""성분별 'FDA 허가 적응증 문장'을 만든다 → 사이트 'FDA 비교 › ② 적응증으로 찾기'가 이 문장에서만 찾는다.

FDA 라벨(openFDA 라벨 전체 파일)의 INDICATIONS AND USAGE 항목에서
  · 적응증 소제목(1.1 고혈압 …)
  · 허가 문장 — '사용 제한(Limitations of Use)' 앞까지, 주의·위험·효과 없음·공통 권고 문장은 뺀 것
만 남긴다. 신약(NDA·BLA) 라벨이 있는 성분은 그 라벨, 제네릭만 남은 성분은 제네릭(ANDA) 라벨 최근 3개.
2026-11 검토: 60개 질병 무작위 표본 약 94% 정확 (check_ind_headings.py).

읽는 것: out/uskr/site_products.csv (판매 중 제품 · 주성분 · 신청번호)
만드는 것: out/uskr/site_indx.parquet (주성분 · 문장 · 출처 · _n=찾기용 정규화 글)
"""
import io
import json
import os
import re
import sys
import time
import urllib.request
import zipfile

import pandas as pd

USKR = os.environ.get("USKR_OUT", "out/uskr")


def get(url, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "product-review-robot"})
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception:
            if i == 3:
                raise
            time.sleep(15 * (i + 1))


def stream(parts):
    import ijson
    for p in parts:
        z = zipfile.ZipFile(io.BytesIO(get(p["file"], timeout=900)))
        for fn in z.namelist():
            with z.open(fn) as fp:
                yield from ijson.items(fp, "results.item")


# ── 사이트 fda_view.py 와 똑같이 맞춰야 하는 부분 (찾기용 정규화) ──
EG = re.compile(r"\((?:e\.?g\.?|for example|such as|including|i\.e\.)[^)]*\)|\b(?:e\.g\.|for example|such as)[^.;:]*", re.I)


def norm(s):
    s = str(s).lower().replace("’", "'")
    s = re.sub(r"'s\b", "", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


STOP = re.compile(r"\b(?:is|are)\s+(?:also\s+)?indicated\b|\bindicated\b|\bLimitations? of Use\b", re.I)
CAUTION = re.compile(
    r"\b(not indicated|consider|potential for|not been established|not recommended|not approved|"
    r"should not|should be part of|should be made|risk management|as appropriate|caution|contraindicat\w*|"
    r"absolute benefit|independent of their|expected to benefit|for example, patients with|"
    r"warning|precaution|monitor|not effective|not of value|not been shown|not been demonstrated|no substantial evidence|"
    r"because of the risks?|substantial risk|primarily strokes and myocardial|"
    r"has been a reduction in the risk of stroke|permit the administration of lower|ncep|other risk factors)\b", re.I)


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
        h = re.sub(r"(\s+(The|This|A|An|In|For|Use|Oral|Intramuscular|Intravenous|Injection|Injectable|Tablets?|Capsules?))+$",
                   "", h)
        if 3 <= len(h) <= 160 and not CAUTION.search(h):
            out.append(h)
    return out


def clauses(t):
    t = re.split(r"\bLimitations? of Use\b|\bImportant Limitations?\b", t, flags=re.I)[0]
    parts = [p.strip() for p in re.split(r"(?<=[.])\s+(?=[A-Z])|•", t) if p.strip()]
    return [p for p in parts if not CAUTION.search(p)]


def main():
    prod = pd.read_csv(f"{USKR}/site_products.csv", dtype=str, keep_default_na=False)
    a2i = prod.drop_duplicates("신청번호").set_index("신청번호")["주성분"]
    kind = prod.drop_duplicates("신청번호").set_index("신청번호")["구분"]
    want = set(prod["신청번호"])
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
            if txt and et >= lab.get(a, ("",))[0]:
                lab[a] = (et, txt, [b.upper() for b in of.get("brand_name") or []],
                          [g.lower() for g in of.get("generic_name") or []], (of.get("brand_name") or [""])[0])
    print(f"라벨 찾은 신청 {len(lab):,} / {len(want):,} · {time.time() - t0:.0f}초")
    by = {}
    for a, v in lab.items():
        by.setdefault(a2i.get(a, ""), []).append((a, kind.get(a, ""), *v))
    rows = []
    for n, ls in by.items():
        if not n:
            continue
        nd = [x for x in ls if x[1] != "ANDA"]
        use = nd if nd else sorted(ls, key=lambda x: x[2], reverse=True)[:3]
        seen = set()
        for a, kd, et, txt, br, gn, brand in use:
            for s in headings(txt, br, gn) + clauses(txt):
                s = s.strip()[:400]
                k = norm(s)
                if len(k) < 4 or k in seen:
                    continue
                seen.add(k)
                rows.append({"주성분": n, "문장": s, "출처": f"{brand} ({a})" if brand else a,
                             "라벨": "제네릭" if kd == "ANDA" else "신약", "_n": norm(EG.sub(" ", s))})
    df = pd.DataFrame(rows)
    nm = df["주성분"].nunique() if len(df) else 0
    print(f"적응증 문장 {len(df):,}줄 · 성분 {nm:,}개 (판매 중 성분 {prod['주성분'].nunique():,}개)"
          f" · 제네릭 라벨로 본 성분 {df.loc[df['라벨'] == '제네릭', '주성분'].nunique() if len(df) else 0:,}")
    if len(df) < 8000 or nm < 1500:
        print("너무 적음 — 만들지 않음 (사이트는 어제 파일 또는 예전 방식)")
        return 3
    df.to_parquet(f"{USKR}/site_indx.parquet", index=False)
    print(f"저장: {USKR}/site_indx.parquet · {os.path.getsize(f'{USKR}/site_indx.parquet') / 1e6:.1f}MB"
          f" · 메모리 {df.memory_usage(deep=True).sum() / 1e6:.0f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
