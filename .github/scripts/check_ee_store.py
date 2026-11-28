"""효능·효과 원문 보관(같은 글은 번호로) + 키워드 검색 시험.
공개 자료(식약처 효능·효과 PDF)만 쓴다. 사이트에 쓰지 않는다. 매출은 쓰지 않는다.
원문은 고치지 않는다 — PDF 에서 뽑을 때 생기는 빈 글자(\\x00)를 띄어쓰기로 바꾸고 연속 공백만 하나로 맞춘다."""
import base64, io, json, os, re, sys, time, random, warnings, logging, threading
from concurrent.futures import ThreadPoolExecutor
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

N_BIG, N_RAND, CAP = 30, 30, 60          # 품목 많은 성분 30 + 아무 성분 30, 성분당 최대 60품목
SEARCH = [("당뇨", ""), ("고혈압", ""), ("고혈압", "폐동맥"), ("간질", ""), ("과민성 방광", ""),
          ("위식도역류", ""), ("고지혈증,이상지질혈증", ""), ("골다공증", ""), ("천식", "")]


def clean(t):
    return re.sub(r"\s+", " ", t.replace("\x00", " ")).strip()


def text_of(data):
    if data[:4] == b"%PDF":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    return re.sub(r"<[^>]+>", " ", data.decode("utf-8", "ignore"))


def score(text, kw):
    """정렬 점수(작을수록 위): 키워드가 그 번호 항목(1. 2. …)의 시작에서 얼마나 가까운지.
    시작에서 12자 안이면 '첫머리'(1000 미만)."""
    c = text.replace(" ", "")
    k = kw.replace(" ", "")
    best = 9e9
    for m in re.finditer(re.escape(k), c):
        starts = [x.end() for x in re.finditer(r"효능효과|(?<![0-9])[0-9]{1,2}\.(?![0-9])|\||(?<=[.:;])|-(?=[가-힣])", c[:m.start()])]
        d = m.start() - (starts[-1] if starts else 0)
        best = min(best, d if d <= 12 else 1000 + d)
    return best


def snippet(text, kw, w=35):
    k = kw.replace(" ", "")
    # 띄어쓰기를 무시하고 찾되, 원문 그대로 앞뒤를 보여준다
    pat = r"\s*".join(map(re.escape, k))
    m = re.search(pat, text)
    if not m:
        return ""
    a, b = max(0, m.start() - w), min(len(text), m.end() + w)
    return ("…" if a else "") + text[a:m.start()] + "【" + text[m.start():m.end()] + "】" + text[m.end():b] + ("…" if b < len(text) else "")


def main():
    info = gsheet._info()
    os.makedirs(".streamlit", exist_ok=True)
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
    import pandas as pd, requests
    import bootstrap, scout_sources as ss
    from googleapiclient.discovery import build
    sheets = build("sheets", "v4", credentials=bootstrap._creds(), cache_discovery=False)
    ap = bootstrap._sheet_df(sheets, bootstrap.SHEETS["approval"])
    ap["_key"] = ss.ing_key_series(ap["주성분(영문)"].fillna("")).str.lower()
    ok = ~ap["상태"].astype(str).str.contains("취하|취소|만료", na=False)
    single = ss.is_single_series(ap["주성분"].fillna(""), ap["주성분(영문)"].fillna(""))
    base = ap[ok & single & (ap["_key"].str.len() > 0)].copy()
    print(f"정상 품목 전체 {int(ok.sum()):,}개 (단일제 {len(base):,}개)")
    cnt = base["_key"].value_counts()
    random.seed(11)
    big = list(cnt.index[:N_BIG])
    rest = [k for k in cnt.index[N_BIG:]]
    random.shuffle(rest)
    pick = big + rest[:N_RAND]
    items = (base[base["_key"].isin(pick)].sort_values("허가일자", ascending=False)
             .groupby("_key").head(CAP))
    print(f"시험: 성분 {len(pick)}개 (품목 많은 {N_BIG} + 아무거나 {N_RAND}) · 품목 {len(items):,}개")

    ses = requests.Session()
    ses.headers["User-Agent"] = "Mozilla/5.0 (product-review research robot)"
    lock = threading.Lock()
    out, fails = {}, [0]

    def get(seq):
        for attempt in range(2):
            try:
                r = ses.get(f"https://nedrug.mfds.go.kr/pbp/cmn/pdfDownload/{seq}/EE", timeout=30)
                t = clean(text_of(r.content))
                with lock:
                    out[seq] = t
                time.sleep(0.2)
                return
            except Exception:
                time.sleep(1)
        with lock:
            fails[0] += 1

    t0 = time.time()
    seqs = items["품목기준코드"].astype(str).str.strip().tolist()
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(get, seqs))
    el = time.time() - t0
    print(f"PDF {len(out):,}개 받음 · 실패 {fails[0]} · {el:.0f}초 (동시 4개, 품목당 {el/max(len(out),1):.2f}초)")

    # ── 보관: 같은 글은 번호 하나 ──
    texts = pd.Series(out)
    uniq = pd.Series(texts.unique())
    tid = {t: i for i, t in enumerate(uniq)}
    item_map = pd.DataFrame({"품목기준코드": texts.index, "효능번호": [tid[t] for t in texts.values]})
    store = pd.DataFrame({"효능번호": range(len(uniq)), "효능효과": uniq.values})
    raw_mb = texts.memory_usage(deep=True) / 1e6
    st_mb = (store.memory_usage(deep=True).sum() + item_map.memory_usage(deep=True).sum()) / 1e6
    store.to_parquet("/tmp/ee_text.parquet", compression="zstd", index=False)
    item_map.to_parquet("/tmp/ee_item.parquet", compression="zstd", index=False)
    fsz = (os.path.getsize("/tmp/ee_text.parquet") + os.path.getsize("/tmp/ee_item.parquet")) / 1e6
    print(f"\n── 보관 크기 ──")
    print(f"품목 {len(texts):,}개 → 서로 다른 효능 글 {len(uniq):,}개 (같은 글 비율 {100*(1-len(uniq)/max(len(texts),1)):.0f}%)")
    print(f"평균 글자 수 {texts.str.len().mean():.0f}자 · 최대 {texts.str.len().max():,}자")
    print(f"메모리: 품목마다 원문 {raw_mb:.2f} MB → 번호 방식 {st_mb:.2f} MB · 파일(zstd) {fsz:.2f} MB")
    # 품목 많은 성분 안에서 같은 글 비율
    k_of = items.set_index(items["품목기준코드"].astype(str).str.strip())["_key"]
    per = pd.DataFrame({"key": k_of.reindex(texts.index).values, "t": texts.values}).groupby("key")["t"].agg(["size", "nunique"])
    print("품목 많은 성분 예 (품목 수 → 서로 다른 글 수): " +
          ", ".join(f"{k} {r['size']}→{r['nunique']}" for k, r in per.loc[[k for k in big if k in per.index][:8]].iterrows()))
    # 전체 어림
    tot_items = int(ok.sum())
    ratio = len(uniq) / max(len(texts), 1)
    print(f"\n전체 어림 (정상 품목 {tot_items:,}개, 복합제 포함): 서로 다른 글 약 {tot_items*ratio:,.0f}개 · "
          f"메모리 약 {st_mb/len(texts)*tot_items:.0f} MB · 파일 약 {fsz/len(texts)*tot_items:.0f} MB")
    print(f"처음 전체 받기 어림: {tot_items:,}개 × {el/max(len(out),1):.2f}초 ≈ {tot_items*el/max(len(out),1)/60:.0f}분 (동시 4개)")

    # ── 성분별 검색 색인(성분마다 서로 다른 글을 합침) ──
    ing = (pd.DataFrame({"key": k_of.reindex(texts.index).values, "t": texts.values})
           .drop_duplicates().groupby("key")["t"].agg(lambda x: " | ".join(x)))
    core = base.drop_duplicates("_key").set_index("_key")["주성분"].astype(str)
    print(f"성분별 검색 색인 {len(ing)}개 · 메모리 {ing.memory_usage(deep=True)/1e6:.2f} MB")

    print("\n── 키워드 검색 (포함하면 전부, 걸린 문구 표시, 첫머리에 있는 것 먼저) ──")
    for kws, excl in SEARCH:
        words = [w.strip() for w in kws.split(",") if w.strip()]
        t = time.time()
        hits = []
        for k, tx in ing.items():
            c = tx.replace(" ", "")
            if not any(w.replace(" ", "") in c for w in words):
                continue
            if excl and excl.replace(" ", "") in c:
                # 빼는 단어: 그 단어가 들어간 문장으로만 걸렸으면 뺀다
                parts = [p for p in re.split(r"[|.;]", c) if any(w.replace(" ", "") in p for w in words)]
                if all(excl.replace(" ", "") in p for p in parts):
                    continue
            sc = min(score(tx, w) for w in words)
            w0 = min(words, key=lambda w: score(tx, w))
            hits.append((sc, k, snippet(tx, w0)))
        hits.sort()
        dt = (time.time() - t) * 1000
        print(f"\n[{kws}{' · 제외: ' + excl if excl else ''}] {len(hits)}개 · {dt:.0f}ms")
        show = hits[:6] + ([("", "", "…")] if len(hits) > 9 else []) + (hits[-3:] if len(hits) > 9 else hits[6:])
        for sc, k, sn in show:
            if k == "":
                print("   ⋮"); continue
            nm = core.get(k, k)[:30]
            print(f"   {'첫머리' if sc < 1000 else '본문  '} {nm}: {sn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
