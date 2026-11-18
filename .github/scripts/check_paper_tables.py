"""공개(오픈 액세스) 논문의 본문 표·그림 설명을 PubMed Central 에서 읽어 적는다. 사이트와 상관없다.

메타분석 도구 검산용: 논문에 실린 임상별 숫자와 합친 값을 그대로 옮겨 적는다.

    PAPERS="PMC10685600,10.1186/s40360-020-00463-w" python .github/scripts/check_paper_tables.py
"""
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
UA = {"User-Agent": "Mozilla/5.0 (product-review)"}


def get(path, params):
    url = EU + path + "?" + urllib.parse.urlencode(params)
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return r.read()
        except Exception as e:
            if i == 2:
                raise
            print("다시 시도:", e)
            time.sleep(3)


def pmcid(x):
    x = x.strip()
    if x.upper().startswith("PMC"):
        return x[3:]
    ids = re.findall(r"<Id>(\d+)</Id>", get("esearch.fcgi", {"db": "pmc", "term": f"{x}[doi]"}).decode())
    return ids[0] if ids else ""


def text(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def show(pid):
    xml = get("efetch.fcgi", {"db": "pmc", "id": pid, "rettype": "xml"})
    root = ET.fromstring(xml)
    art = root.find(".//article") if root.tag != "article" else root
    if art is None:
        print("본문 없음")
        return
    title = art.find(".//article-meta//article-title")
    print(f"\n################ PMC{pid}: {text(title) if title is not None else ''}")
    for ab in art.findall(".//abstract"):
        print("[초록]", text(ab)[:2500])
    for sec in art.findall(".//body//sec"):
        t = sec.find("title")
        tt = text(t) if t is not None else ""
        if re.search(r"statistic|data synthesis|analysis|LDL|lipid|efficacy|result", tt, re.I):
            paras = [text(p) for p in sec.findall("p")]
            if paras:
                print(f"\n[절] {tt}\n" + "\n".join(p[:1500] for p in paras[:6]))
    for tw in art.findall(".//table-wrap"):
        lab = tw.find("label")
        cap = tw.find("caption")
        print(f"\n[표] {text(lab) if lab is not None else ''} {text(cap) if cap is not None else ''}")
        for tr in tw.findall(".//tr"):
            cells = [text(c) for c in tr if c.tag in ("td", "th")]
            print("   | " + " | ".join(cells))
    for fg in art.findall(".//fig"):
        lab = fg.find("label")
        cap = fg.find("caption")
        print(f"\n[그림] {text(lab) if lab is not None else ''} {text(cap)[:600] if cap is not None else ''}")
    for sm in art.findall(".//supplementary-material"):
        print("[부록]", text(sm)[:300], [m.get("{http://www.w3.org/1999/xlink}href") for m in sm.iter() if m.get("{http://www.w3.org/1999/xlink}href")])


def main():
    for x in (os.environ.get("PAPERS") or "PMC10685600").split(","):
        if not x.strip():
            continue
        try:
            pid = pmcid(x)
            if not pid:
                print(f"\n{x}: PMC 에서 못 찾음")
                continue
            show(pid)
        except Exception as e:
            print(f"\n{x}: 실패 {type(e).__name__}: {e}")
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
