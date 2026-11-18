"""FDA 허가·라벨을 '매일' 반영할 수 있는 공식 자료가 로봇에서 받아지는지, 얼마나 자주 바뀌는지 본다.
사이트와 상관없다. 공개 자료만 읽는다.

① FDA Drugs@FDA 내려받기 파일 (fda.gov) — 안의 가장 최근 허가·변경 날짜
② DailyMed (미국 국립의학도서관, FDA 라벨 공식 배포) — 일일 갱신 파일 목록, 최근 며칠 새 라벨 수
③ openFDA 기준일 (비교용)
"""
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from collections import Counter

UA = {"User-Agent": "Mozilla/5.0 (product-review feasibility check)"}


def get(url, timeout=120):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.geturl(), dict(r.headers)


def head(t):
    print(f"\n{'=' * 8} {t}")


def drugsfda_file():
    head("① FDA Drugs@FDA 내려받기 파일")
    for u in ["https://www.fda.gov/media/89850/download", "https://www.fda.gov/media/89850/download?attachment"]:
        try:
            b, url, h = get(u, timeout=180)
            print(f"{u} → {len(b) / 1e6:.1f}MB · Last-Modified {h.get('Last-Modified')} · 종류 {h.get('Content-Type')}")
            z = zipfile.ZipFile(io.BytesIO(b))
            print("   안의 파일:", z.namelist())
            for fn in z.namelist():
                if "Submissions" in fn and fn.endswith(".txt"):
                    t = z.read(fn).decode("latin-1").splitlines()
                    hdr = t[0].split("\t")
                    di = next(i for i, c in enumerate(hdr) if "StatusDate" in c)
                    dates = sorted(x.split("\t")[di][:10] for x in t[1:] if len(x.split("\t")) > di)
                    print(f"   {fn}: {len(t) - 1:,}줄 · 가장 최근 처리일 {dates[-5:]}")
                    print("   최근 7일 처리 건수:", Counter(d for d in dates if d >= dates[-1][:8] + "00")
                          .most_common(10))
            return
        except Exception as e:
            print(u, "→", type(e).__name__, str(e)[:150])


def dailymed():
    head("② DailyMed (NLM, FDA 라벨 공식 배포)")
    try:
        b, url, h = get("https://dailymed.nlm.nih.gov/dailymed/spl-resources-all-drug-labels.cfm", timeout=60)
        t = b.decode("utf-8", "ignore")
        z = sorted(set(re.findall(r'href="([^"]*dm_spl_(?:daily|weekly|monthly)_update[^"]*\.zip)"', t)))
        print(f"일일·주간·월간 갱신 파일 링크 {len(z)}개:", z[:12])
    except Exception as e:
        print("자료 화면:", type(e).__name__, str(e)[:150])
    # 웹서비스: 최근 며칠간 새로 올라온 처방약 라벨 수
    for days in range(1, 8):
        d = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
        try:
            b, _, _ = get(f"https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json?published_date={d}"
                          f"&published_date_comparison=eq&pagesize=1", timeout=60)
            j = json.loads(b)
            print(f"   {d} 올라온 라벨: {j.get('metadata', {}).get('total_elements')}개")
        except Exception as e:
            print(f"   {d}: {type(e).__name__} {str(e)[:100]}")
        time.sleep(0.5)
    try:
        b, _, _ = get("https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json?pagesize=3", timeout=60)
        print("   최근 라벨 예:", [(x.get("title", "")[:60], x.get("published_date")) for x in json.loads(b).get("data", [])])
    except Exception as e:
        print("   예시:", str(e)[:100])


def openfda():
    head("③ openFDA 기준일 (비교)")
    b, _, _ = get("https://api.fda.gov/download.json", timeout=60)
    r = json.loads(b)["results"]
    print({k: r[a][k].get("export_date") for a, k in (("drug", "drugsfda"), ("drug", "label"), ("other", "substance"))})


def main():
    for fn in (drugsfda_file, dailymed, openfda):
        try:
            fn()
        except Exception as e:
            print(f"[{fn.__name__}] 실패 {type(e).__name__}: {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
