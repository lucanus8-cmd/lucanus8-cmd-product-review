"""PubMed(E-utilities, 무료 공개) 접속 시험.

1) 성분 + 학술지(NEJM 등)로 논문 찾기: 건수와 제목 몇 개
2) ClinicalTrials.gov 임상번호(NCT)로 그 임상의 논문 찾기 — PubMed 기록에 임상번호가 붙어 있다
읽기만 한다. 공개 자료만 적는다.
"""
import os
import sys
import time

import requests

E = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
DRUGS = [d.strip() for d in (os.environ.get("DRUGS") or "bempedoic acid,ezetimibe").split(",") if d.strip()]
JOURNAL = os.environ.get("JOURNAL") or "N Engl J Med"
NCTS = [x.strip() for x in (os.environ.get("NCTS") or "NCT02666664,NCT03337308,NCT02993406,NCT02991118").split(",") if x.strip()]
P = {"tool": "product-review-check", "retmode": "json"}


def search(term, n=5):
    t = time.time()
    r = requests.get(E + "esearch.fcgi", params=dict(P, db="pubmed", term=term, retmax=n, sort="pub_date"), timeout=60)
    r.raise_for_status()
    j = r.json()["esearchresult"]
    return int(j.get("count", 0)), j.get("idlist", []), time.time() - t


def summary(ids):
    if not ids:
        return []
    r = requests.get(E + "esummary.fcgi", params=dict(P, db="pubmed", id=",".join(ids)), timeout=60)
    r.raise_for_status()
    res = r.json()["result"]
    out = []
    for i in ids:
        d = res.get(i, {})
        doi = next((a["value"] for a in d.get("articleids", []) if a.get("idtype") == "doi"), "")
        pmc = next((a["value"] for a in d.get("articleids", []) if a.get("idtype") == "pmc"), "")
        out.append(f"{d.get('pubdate', '')[:10]} · {d.get('source', '')} · {d.get('title', '')[:90]}"
                   f"\n         PMID {i} · doi {doi or '-'} · 무료 전문(PMC) {pmc or '없음'}")
    return out


def main():
    for d in DRUGS:
        for term, label in ((f'"{d}"[tiab] AND "{JOURNAL}"[ta]', f"{d} · {JOURNAL}"),
                            (f'"{d}"[tiab] AND randomized controlled trial[pt]', f"{d} · RCT 전체 학술지")):
            n, ids, sec = search(term)
            print(f"\n===== {label}: {n}건 ({sec:.1f}초)")
            for l in summary(ids):
                print("   " + l)
            time.sleep(0.4)
    print("\n===== 임상번호로 논문 찾기")
    for nct in NCTS:
        n, ids, sec = search(f"{nct}[si]", 5)
        print(f"   {nct}: {n}건 ({sec:.1f}초)")
        for l in summary(ids):
            print("      " + l)
        time.sleep(0.4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
