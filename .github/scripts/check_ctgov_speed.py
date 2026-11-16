"""ClinicalTrials.gov 를 가볍게 받는 방법 비교. 읽기만 하고 공개 자료의 크기·시간만 적는다.

1) API 로 임상 전체를 받을 때 vs 필요한 부분만(fields) 받을 때: 크기·시간
2) 한 번 받은 결과를 작은 표(CSV)로 줄였을 때 크기
3) AACT(CT.gov 전체를 매일 정리해 주는 공개 DB) 내려받기 파일 크기
"""
import gzip
import io
import json
import os
import re
import sys
import time

import requests

API = "https://clinicaltrials.gov/api/v2/studies"
DRUGS = [d.strip() for d in (os.environ.get("DRUGS") or "empagliflozin,dapagliflozin").split(",") if d.strip()]
UA = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
FIELDS = {
    "평가변수만": "protocolSection.identificationModule,protocolSection.designModule.phases,"
                 "resultsSection.outcomeMeasuresModule",
    "평가변수+대상자": "protocolSection.identificationModule,protocolSection.designModule,"
                     "protocolSection.conditionsModule,protocolSection.eligibilityModule,"
                     "resultsSection.outcomeMeasuresModule,resultsSection.baselineCharacteristicsModule",
    "평가변수+대상자+이상반응": "protocolSection.identificationModule,protocolSection.designModule,"
                           "protocolSection.conditionsModule,protocolSection.eligibilityModule,"
                           "resultsSection.outcomeMeasuresModule,resultsSection.baselineCharacteristicsModule,"
                           "resultsSection.adverseEventsModule",
}


def fetch(drug, fields=None):
    p = {"query.intr": drug, "query.cond": "type 2 diabetes", "aggFilters": "phase:3,results:with",
         "pageSize": 100, "format": "json"}
    if fields:
        p["fields"] = fields
    raw, wire, n, tok, t = 0, 0, 0, None, time.time()
    allj = []
    while True:
        if tok:
            p["pageToken"] = tok
        r = requests.get(API, params=p, headers=UA, timeout=90, stream=True)
        body = r.raw.read(decode_content=False)
        wire += len(body)
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        raw += len(body)
        if r.status_code != 200:
            return dict(err=f"HTTP {r.status_code} {body[:200]!r}")
        j = json.loads(body)
        allj += j.get("studies", [])
        n += len(j.get("studies", []))
        tok = j.get("nextPageToken")
        if not tok:
            break
    return dict(n=n, raw=raw, wire=wire, sec=time.time() - t, studies=allj,
                gz=r.headers.get("Content-Encoding") or "없음")


def compact(studies):
    """평가변수 하나 = 군 하나 = 한 줄 로 줄인 CSV 크기."""
    out = io.StringIO()
    out.write("nct,outcome,type,timeframe,unit,param,disp,group,n,value,spread,lo,hi\n")
    rows = 0
    for s in studies:
        nct = s["protocolSection"]["identificationModule"]["nctId"]
        for o in s.get("resultsSection", {}).get("outcomeMeasuresModule", {}).get("outcomeMeasures", []):
            g = {x["id"]: x["title"] for x in o.get("groups", [])}
            try:
                den = {c["groupId"]: c["value"] for c in o["denoms"][0]["counts"]}
            except Exception:
                den = {}
            for c in o.get("classes", []):
                for cat in c.get("categories", []):
                    for m in cat.get("measurements", []):
                        vals = [nct, o.get("title", ""), o.get("type", ""), o.get("timeFrame", ""),
                                o.get("unitOfMeasure", ""), o.get("paramType", ""), o.get("dispersionType", ""),
                                g.get(m.get("groupId"), ""), den.get(m.get("groupId"), ""),
                                m.get("value", ""), m.get("spread", ""), m.get("lowerLimit", ""), m.get("upperLimit", "")]
                        out.write(",".join('"' + str(v).replace('"', "'") + '"' for v in vals) + "\n")
                        rows += 1
    b = out.getvalue().encode()
    return rows, len(b), len(gzip.compress(b))


def kb(x):
    return f"{x / 1024:,.0f}KB" if x < 1024 * 1024 else f"{x / 1024 / 1024:,.1f}MB"


def aact():
    print("\n===== AACT (CT.gov 전체를 매일 정리한 공개 자료)")
    for u in ("https://aact.ctti-clinicaltrials.org/downloads", "https://aact.ctti-clinicaltrials.org/download",
              "https://aact.ctti-clinicaltrials.org/pipe_files", "https://aact.ctti-clinicaltrials.org/snapshots"):
        try:
            r = requests.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            links = sorted(set(re.findall(r'href="([^"]+\.(?:zip|dmp|gz)[^"]*)"', r.text)))
            sizes = re.findall(r"(\d[\d.,]*\s*(?:MB|GB|KB))", r.text)
            print(f"   {u} → HTTP {r.status_code} · 파일 링크 {len(links)}개 · 크기 표시 {sizes[:8]}")
            for l in links[:6]:
                full = l if l.startswith("http") else "https://aact.ctti-clinicaltrials.org" + l
                try:
                    h = requests.head(full, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, allow_redirects=True)
                    print(f"      {full[:110]} · {kb(int(h.headers.get('Content-Length', 0)))}")
                except Exception as e:
                    print(f"      {full[:110]} · 크기 못 봄 {str(e)[:60]}")
        except Exception as e:
            print(f"   {u} → 실패 {str(e)[:100]}")


def main():
    for d in DRUGS:
        print(f"\n===== {d} (2형 당뇨 3상, 결과 있음)")
        full = fetch(d)
        if "err" in full:
            print("   전체 받기 실패:", full["err"])
            continue
        print(f"   전체 받기: 임상 {full['n']}개 · 압축해서 {kb(full['wire'])} 오감 · 풀면 {kb(full['raw'])} · "
              f"{full['sec']:.1f}초 · 압축전송 {full['gz']}")
        for name, f in FIELDS.items():
            r = fetch(d, f)
            if "err" in r:
                print(f"   {name}: 실패 {r['err']}")
                continue
            print(f"   {name}: 압축 {kb(r['wire'])} · 풀면 {kb(r['raw'])} · {r['sec']:.1f}초")
        rows, b, gz = compact(full["studies"])
        print(f"   작은 표(CSV)로 줄이면: {rows:,}줄 · {kb(b)} · 압축 {kb(gz)}")
    # 결과 있는 3상 약물 임상 전체 수(미리 받아 둘 때 규모)
    for q in ({"aggFilters": "phase:3,results:with"}, {"aggFilters": "phase:3,results:with", "query.cond": "type 2 diabetes"}):
        r = requests.get(API, params=dict(q, pageSize=1, countTotal="true", format="json"), headers=UA, timeout=60)
        print(f"\n건수 {q}: {r.json().get('totalCount') if r.ok else r.status_code}")
    aact()
    return 0


if __name__ == "__main__":
    sys.exit(main())
