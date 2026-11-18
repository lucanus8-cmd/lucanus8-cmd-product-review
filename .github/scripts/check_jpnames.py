"""일본 성분명(일본어) ↔ 영문을 공신력 있는 공개 자료로 얻을 수 있는지 본다. 사이트와 상관없다.

① 일본 공식 일반명 DB(JAN, 국립의약품식품위생연구소) ② PMDA 의약품 검색·첨부문서
③ 일본약국방 영문판 ④ 미국 FDA 성분 사전(GSRS, openFDA substance) — 동의어(INN·USAN·JAN)
공개 화면만 읽고 구조·건수·예시만 적는다.
"""
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (product-review feasibility check)"}


def get(url, data=None, timeout=60, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.geturl(), r.headers.get("Content-Type", "")


def text(b, ct=""):
    m = re.search(r"charset=([\w\-]+)", ct or "") or re.search(rb'charset=["\']?([\w\-]+)', b[:3000])
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    try:
        return b.decode(enc, "ignore")
    except LookupError:
        return b.decode("utf-8", "ignore")


def plain(t):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()


def forms(t):
    out = []
    for f in re.finditer(r"<form[^>]*>(.*?)</form>", t, re.S | re.I):
        tag = re.match(r"<form[^>]*>", f.group(0), re.I).group(0)
        ins = re.findall(r'<(?:input|select|textarea)[^>]*name="([^"]+)"', f.group(1), re.I)
        out.append((tag[:200], ins[:25]))
    return out


def head(t):
    print(f"\n{'=' * 8} {t}")


def jan():
    head("① 일본 공식 일반명 DB (JAN)")
    b, url, ct = get("https://jpdb.nihs.go.jp/jan/")
    t = text(b, ct)
    print("주소:", url, "·", len(t), "자")
    print("글:", plain(t)[:1200])
    print("폼:", forms(t))
    links = sorted(set(re.findall(r'href="([^"#]+)"', t)))
    print("링크:", links[:60])
    for kw in ("アムロジピン", "amlodipine"):
        for cand in [f"https://jpdb.nihs.go.jp/jan/Search?keyword={urllib.parse.quote(kw)}",
                     f"https://jpdb.nihs.go.jp/jan/search?q={urllib.parse.quote(kw)}"]:
            try:
                b2, u2, ct2 = get(cand, timeout=30)
                print(f"   시도 {cand} → {u2} · {plain(text(b2, ct2))[:300]}")
            except Exception as e:
                print(f"   시도 {cand} → {str(e)[:80]}")
            time.sleep(1)


def pmda():
    head("② PMDA 의약품 검색·첨부문서")
    for u in ["https://www.pmda.go.jp/PmdaSearch/iyakuSearch/",
              "https://www.pmda.go.jp/safety/info-services/drugs/0001.html"]:
        try:
            b, url, ct = get(u)
            t = text(b, ct)
            print(f"{url} · {len(t)}자 · 폼 {forms(t)[:3]}")
            p = plain(t)
            for kw in ("一括", "ダウンロード", "XML", "SGML", "英"):
                i = p.find(kw)
                if i >= 0:
                    print(f"   '{kw}' 근처: {p[max(0, i - 120):i + 160]}")
        except Exception as e:
            print(u, "못 읽음", str(e)[:120])


def jp_pharmacopoeia():
    head("③ 일본약국방 영문판")
    for u in ["https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/0000066597.html",
              "https://www.pmda.go.jp/english/rs-sb-std/standards-development/jp/0029.html",
              "https://www.pmda.go.jp/english/rs-sb-std/standards-development/jp/0019.html"]:
        try:
            b, url, ct = get(u)
            t = text(b, ct)
            ls = re.findall(r'href="([^"]+\.pdf)"[^>]*>(.*?)</a>', t, re.S | re.I)
            print(f"{url} · {len(t)}자 · 제목 {plain(re.search(r'<title>(.*?)</title>', t, re.S).group(1))[:80] if '<title>' in t else ''}")
            print("   PDF:", [(h.split('/')[-1], plain(x)[:40]) for h, x in ls][:15])
        except Exception as e:
            print(u, "못 읽음", str(e)[:120])


def gsrs():
    head("④ 미국 FDA 성분 사전 (GSRS · openFDA substance)")
    try:
        idx = json.loads(get("https://api.fda.gov/download.json")[0])
        sub = idx["results"]["other"]["substance"]
        print("통째 파일:", len(sub["partitions"]), "개 ·", sum(float(p.get("size_mb") or 0) for p in sub["partitions"]), "MB · 기준일", sub.get("export_date"))
    except Exception as e:
        print("download.json:", str(e)[:120])
    for nm in ("INDOMETHACIN", "LEUPROLIDE", "SUCCINYLCHOLINE", "URSODIOL", "AMLODIPINE BESYLATE"):
        try:
            q = urllib.parse.quote(f'names.name:"{nm}"')
            j = json.loads(get(f"https://api.fda.gov/other/substance.json?search={q}&limit=1")[0])
            r = j["results"][0]
            names = [n.get("name") for n in r.get("names", [])]
            tagged = [n for n in names if re.search(r"\[(INN|USAN|JAN|BAN|WHO-DD|MI|USP)", n or "")]
            print(f"   {nm}: UNII {r.get('unii')} · 이름 {len(names)}개 · 표준 이름 예 {tagged[:8]}")
        except Exception as e:
            print(f"   {nm}: {str(e)[:100]}")
        time.sleep(0.5)


def main():
    for fn in (jan, pmda, jp_pharmacopoeia, gsrs):
        try:
            fn()
        except Exception as e:
            print(f"[{fn.__name__}] 실패 {type(e).__name__}: {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
