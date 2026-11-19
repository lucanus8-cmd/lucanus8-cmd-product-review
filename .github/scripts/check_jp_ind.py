"""일본 첨부문서(電子添文) '효능 또는 효과' 항목으로 질병 찾기가 FDA 처럼 되는지 검토한다. 사이트와 상관없다(읽기만).

1) 후생노동성 약가 목록 → 성분(일본어)·구분(내용/주사/외용)마다 제품 하나(오리지널 우선)
2) PMDA 화면에서 그 제품 첨부문서를 열어 '효능 또는 효과' 항목만 잘라낸다
   (그다음 항목 '효능 또는 효과에 관련된 주의'·'용법 및 용량' 앞에서 자름 → 주의 문구 섞임 방지)
3) 66개 질병을 일본어 병명으로 찾고, 같은 성분의 FDA 라벨 결과와 맞춰 본다
N=숫자 이면 그만큼만 받아 잘라낸 글을 보여 준다(시험). 이전에 받은 글(data 가지 review/jp_labels.csv)은 다시 받지 않는다.
"""
import html
import io
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

import pandas as pd

sys.path.insert(0, ".github/scripts")
import check_overseas as ov          # noqa: E402
import check_pmda as cp              # noqa: E402
import check_ind_headings as ih      # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (product-review feasibility check)"}
RAW = "https://raw.githubusercontent.com/lucanus8-cmd/lucanus8-cmd-product-review/data/"
OUT = "out"


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.geturl(), r.headers.get("Content-Type", "")


def page_text(b, ct):
    t = cp.dec(b, ct)
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|dt|dd|table)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = unicodedata.normalize("NFKC", t)
    return re.sub(r"[ \t　]+", " ", re.sub(r"\n\s*\n+", "\n", t))


HEAD = re.compile(r"(?:^|\n)\s*(?:\d+\.\s*)?【?\s*効能(?:又は|・)効果\s*】?\s*(?=\n|$|[^にをの等とがは])")
END = re.compile(r"(?:\d+\.\s*|[〈<(]\s*)?効能(?:又は|・)効果に関連する|【?\s*用法(?:及び|・)用量|(?:^|\n)\s*\d+\.\s*用法")


def indication(t):
    """'効能又は効果' 항목 글. 목차에 나온 제목은 글이 없으니 건너뛴다."""
    for m in HEAD.finditer(t):
        rest = t[m.end():m.end() + 6000]
        e = END.search(rest)
        body = (rest[:e.start()] if e else rest[:1500]).strip(" \n:：")
        body = re.sub(r"\s*\n\s*", " / ", body).strip(" /〈<(")
        if len(body) >= 3 and not re.match(r"^(\d+\.|【)", body):
            return body[:3000]
    return ""


def fetch_label(yj):
    b, fu, ct = get(f"https://www.pmda.go.jp/PmdaSearch/rdSearch/02/{yj}?user=1")
    t = cp.dec(b, ct)
    ln = re.findall(r'href="(/PmdaSearch/iyakuDetail/\d+_[0-9A-Z]+_\d+_\d+)"', t)
    if not ln:
        return "", "", "첨부문서 링크 없음"
    u = "https://www.pmda.go.jp" + ln[0]
    time.sleep(0.8)
    b, fu, ct = get(u)
    tx = page_text(b, ct)
    return indication(tx), u, ("" if "効能" in tx else "효능 글 없음")


def pick(jp, jmap):
    name_c = next(c for c in jp.columns if "成分名" in c)
    code_c = next(c for c in jp.columns if "コード" in c)
    item_c = next(c for c in jp.columns if "品名" in c)
    sen_c = next((c for c in jp.columns if c.strip() == "先発医薬品"), None)
    jp = jp.copy()
    jp["_sen"] = jp[sen_c].astype(str).str.contains("先発") if sen_c else False
    rows = []
    for (nm, f), g in jp.groupby([jp[name_c].astype(str).str.strip(), "_file"]):
        parts = [p.strip() for p in re.split(r"[・／/、,]", nm) if p.strip()]
        en = [jmap.get(p) for p in parts]
        if not parts or any(not x for x in en):
            continue
        g = g.sort_values("_sen", ascending=False)
        r = g.iloc[0]
        rows.append({"成分名": nm, "구분": f, "주성분": " + ".join(sorted(set(en))), "YJ": str(r[code_c]).strip(),
                     "品名": str(r[item_c]).strip(), "오리지널": bool(r["_sen"]), "제품 수": len(g)})
    return pd.DataFrame(rows)


# ── 66개 질병: 일본어 병명 (찾을 말, 빼고 볼 말) — check_ind_headings.TESTS 와 같은 열쇠 ──
_DM_X = ["糖尿病性腎症", "糖尿病性神経障害", "糖尿病性末梢神経障害", "糖尿病網膜症", "糖尿病性網膜症", "糖尿病黄斑浮腫",
         "糖尿病性黄斑浮腫", "糖尿病性潰瘍", "糖尿病性壊疽", "を伴う2型糖尿病における", "尿崩症", "糖尿病性昏睡"]
JA = {
    "고혈압": (["高血圧"], ["肺高血圧", "肺動脈性肺高血圧", "高眼圧", "眼圧", "門脈圧亢進", "頭蓋内圧", "褐色細胞腫"]),
    "이상지질혈증": (["高コレステロール血症", "高脂血症", "脂質異常症", "高トリグリセリド血症", "高リポ蛋白血症"], []),
    "당뇨병": (["糖尿病"], _DM_X),
    "비만": (["肥満症"], []),
    "심부전": (["心不全"], []),
    "협심증": (["狭心症"], []),
    "심근경색": (["心筋梗塞"], []),
    "심방세동": (["心房細動"], []),
    "뇌졸중": (["脳卒中", "脳梗塞", "虚血性脳血管障害"], []),
    "심부정맥혈전증": (["深部静脈血栓症", "静脈血栓塞栓症"], []),
    "폐동맥고혈압": (["肺動脈性肺高血圧"], []),
    "만성신장병": (["慢性腎臓病"], []),
    "천식": (["喘息"], []),
    "COPD": (["慢性閉塞性肺疾患"], []),
    "알레르기비염": (["アレルギー性鼻炎"], []),
    "특발성폐섬유증": (["特発性肺線維症"], []),
    "위식도역류질환": (["逆流性食道炎", "胃食道逆流症"], []),
    "소화성궤양": (["胃潰瘍", "十二指腸潰瘍", "消化性潰瘍"], []),
    "크론병": (["クローン病"], []),
    "궤양성대장염": (["潰瘍性大腸炎"], []),
    "과민성대장증후군": (["過敏性腸症候群"], []),
    "변비": (["便秘"], []),
    "구역·구토": (["嘔吐"], []),
    "C형간염": (["C型慢性肝炎", "C型肝炎", "C型代償性肝硬変", "C型非代償性肝硬変"], []),
    "B형간염": (["B型慢性肝", "B型肝炎"], ["B型肝炎ウイルス感染の再活性化"]),
    "HIV": (["HIV感染症", "HIV-1感染症"], []),
    "대상포진": (["帯状疱疹"], ["帯状疱疹後神経痛"]),
    "인플루엔자": (["インフルエンザ"], ["インフルエンザ菌"]),
    "요로감염": (["膀胱炎", "腎盂腎炎", "尿路感染"], []),
    "결핵": (["結核"], []),
    "류마티스관절염": (["関節リウマチ"], []),
    "골관절염": (["変形性関節症"], []),
    "골다공증": (["骨粗鬆症"], []),
    "통풍": (["痛風", "高尿酸血症"], []),
    "강직성척추염": (["強直性脊椎炎"], []),
    "건선": (["乾癬"], []),
    "아토피피부염": (["アトピー性皮膚炎"], []),
    "여드름": (["ざ瘡"], []),
    "루푸스": (["エリテマトーデス"], []),
    "다발성경화증": (["多発性硬化症"], []),
    "우울증": (["うつ病", "うつ状態", "抑うつ", "うつ症状"], []),
    "조현병": (["統合失調症"], []),
    "양극성장애": (["双極性障害", "躁病"], []),
    "불안장애": (["不安"], []),
    "불면증": (["不眠症"], []),
    "ADHD": (["注意欠如", "注意欠陥", "AD/HD"], []),
    "알츠하이머병": (["アルツハイマー"], []),
    "파킨슨병": (["パーキンソン"], []),
    "뇌전증": (["てんかん"], []),
    "편두통": (["片頭痛"], []),
    "신경병증성통증": (["神経障害性疼痛", "帯状疱疹後神経痛", "糖尿病性神経障害に伴う疼痛"], []),
    "전립선비대증": (["前立腺肥大症"], []),
    "과민성방광": (["過活動膀胱"], []),
    "발기부전": (["勃起不全"], []),
    "녹내장": (["緑内障", "高眼圧症"], []),
    "황반변성": (["黄斑変性"], []),
    "갑상선기능저하증": (["甲状腺機能低下症"], []),
    "빈혈": (["貧血"], []),
    "혈우병": (["血友病"], []),
    "유방암": (["乳癌"], []),
    "폐암": (["肺癌"], []),
    "대장암": (["結腸・直腸癌", "結腸癌", "直腸癌", "大腸癌"], []),
    "전립선암": (["前立腺癌"], []),
    "다발골수종": (["多発性骨髄腫"], []),
    "만성림프구성백혈병": (["慢性リンパ性白血病"], []),
    "간세포암": (["肝細胞癌"], []),
}


def ja_hit(text, terms, excl):
    t = unicodedata.normalize("NFKC", str(text))
    for e in excl:
        t = t.replace(unicodedata.normalize("NFKC", e), " ")
    for w in terms:
        w = unicodedata.normalize("NFKC", w)
        i = t.find(w)
        if i >= 0:
            return t[max(0, i - 40):i + 80]
    return ""


def evaluate(lab):
    cp.head("질병별 일본 첨부문서 결과 · FDA 라벨 결과와 맞춰 보기")
    try:
        b, _, _ = get(RAW + "fda_site/indx.parquet", timeout=120)
        ix = pd.read_parquet(io.BytesIO(b))
    except Exception as e:
        print("FDA 문장 파일 못 읽음:", str(e)[:100])
        ix = pd.DataFrame(columns=["주성분", "문장"])
    fda_txt = ix.groupby("주성분")["문장"].apply(list).to_dict()
    lab = lab[lab["효능"].astype(str).str.len() > 0]
    jp_txt = lab.groupby("주성분")["효능"].apply(list).to_dict()
    both = set(jp_txt) & set(fda_txt)
    print(f"효능 글 있는 일본 성분 {len(jp_txt):,} · FDA 문장 있는 성분 {len(fda_txt):,} · 둘 다 {len(both):,}")
    rows, tot = [], {"jp": 0, "agree": 0, "jp_only": 0, "fda_only": 0}
    for k, (terms, excl) in JA.items():
        ft, _, fx = ih.TESTS.get(k, ([], "", []))
        jp_hit = {m: s for m, ts in jp_txt.items() for s in [next((ja_hit(x, terms, excl) for x in ts if ja_hit(x, terms, excl)), "")] if s}
        fda_hit = {m: ih.hit(fda_txt[m], ft, fx) for m in both} if ft else {}
        fda_hit = {m: s for m, s in fda_hit.items() if s}
        a = {m for m in jp_hit if m in both and m in fda_hit}
        jo = {m for m in jp_hit if m in both and m not in fda_hit}
        fo = {m for m in fda_hit if m not in jp_hit}
        tot["jp"] += len(jp_hit)
        tot["agree"] += len(a)
        tot["jp_only"] += len(jo)
        tot["fda_only"] += len(fo)
        print(f"{k:10s} 일본 {len(jp_hit):3d} · 둘 다 있는 성분 중 일치 {len(a):3d} · 일본만 {len(jo):2d} {sorted(jo)[:6]}"
              f" · FDA만 {len(fo):2d} {sorted(fo)[:6]}")
        for m, s in jp_hit.items():
            rows.append({"질병": k, "주성분": m, "일본 근거": s, "FDA": "같음" if m in a else ("FDA 없음" if m in jo else ("미국 라벨 없음" if m not in both else "")),
                         "FDA 문장": (fda_hit.get(m, "") or "")[:200]})
        for m in fo:
            rows.append({"질병": k, "주성분": m, "일본 근거": "", "FDA": "FDA만",
                         "FDA 문장": fda_hit[m][:200], "일본 효능": " | ".join(jp_txt.get(m, []))[:300]})
    print(f"\n합계: 일본 찾은 쌍 {tot['jp']:,} · 미국에도 있는 성분에서 일치 {tot['agree']:,} · 일본만 {tot['jp_only']:,} · FDA만 {tot['fda_only']:,}")
    d = pd.DataFrame(rows)
    d.to_csv(f"{OUT}/jp_ind_pairs.csv", index=False, encoding="utf-8-sig")
    # 검토 표본 (일본만 · FDA만 · 미국 라벨 없음)
    for lab_, n in (("FDA 없음", 25), ("FDA만", 25), ("미국 라벨 없음", 25)):
        x = d[d["FDA"] == lab_]
        x = x.sample(min(n, len(x)), random_state=3) if len(x) else x
        print(f"\n▶ 표본 [{lab_}] {len(d[d['FDA'] == lab_])}쌍 중 {len(x)}")
        for _, r in x.iterrows():
            print(f"   {r['질병']} | {r['주성분'][:30]} | JP: {str(r['일본 근거'])[:110]} | "
                  f"{'US: ' + str(r['FDA 문장'])[:110] if r['FDA 문장'] else ('JP효능: ' + str(r.get('일본 효능', ''))[:150])}")


def main():
    os.makedirs(OUT, exist_ok=True)
    N = int(os.environ.get("N") or 0)
    jn = cp.jan()
    jmap = {}
    full = {cp.ja_norm(a): b for a, b in zip(jn["ja"], jn["en"])}
    moi = {}
    for a, b in zip(jn["ja"], jn["en"]):
        moi.setdefault(ov.ja_moiety(cp.ja_norm(a)), set()).add(ov.en_moiety(b))
    jp = ov.japan()
    name_c = next(c for c in jp.columns if "成分名" in c)
    for s in jp[name_c].astype(str):
        for p in re.split(r"[・／/、,]", s):
            p = p.strip()
            k = cp.ja_norm(p)
            if p and p not in jmap:
                if k in full:
                    jmap[p] = ov.en_moiety(full[k])
                elif len(moi.get(ov.ja_moiety(k), ())) == 1:
                    jmap[p] = next(iter(moi[ov.ja_moiety(k)]))
    sel = pick(jp, jmap)
    print(f"\n받을 첨부문서: 성분·구분 {len(sel):,}개 (오리지널 {sel['오리지널'].sum():,}) · 영문 성분 {sel['주성분'].nunique():,}")
    try:
        b, _, _ = get(RAW + "review/jp_labels.csv", timeout=60)
        old = pd.read_csv(io.BytesIO(b), dtype=str, keep_default_na=False)
        old = old[old["효능"] != ""]
        print(f"이전에 받은 글 {len(old):,}개 다시 씀")
    except Exception:
        old = pd.DataFrame(columns=["YJ", "효능", "주소", "메모"])
    done = old.set_index("YJ")[["효능", "주소", "메모"]].to_dict("index")
    todo = sel[~sel["YJ"].isin(done)]
    if N:
        todo = todo.sample(min(N, len(todo)), random_state=11)
    print(f"새로 받을 것 {len(todo):,}개 (약 {len(todo) * 2.3 / 60:.0f}분)")
    t0, err = time.time(), 0
    for i, (_, r) in enumerate(todo.iterrows()):
        try:
            txt, u, memo = fetch_label(r["YJ"])
        except Exception as e:
            txt, u, memo = "", "", f"{type(e).__name__}"
            err += 1
        done[r["YJ"]] = {"효능": txt, "주소": u, "메모": memo}
        if N and i < 40:
            print(f"   [{r['구분']}] {r['成分名'][:22]} ({r['주성분'][:25]}) {r['品名'][:18]} → {txt[:220] or memo}")
        if (i + 1) % 200 == 0:
            print(f"   … {i + 1:,}/{len(todo):,} · {time.time() - t0:.0f}초 · 오류 {err}", flush=True)
        time.sleep(1.0)
    sel["효능"] = sel["YJ"].map(lambda y: done.get(y, {}).get("효능", ""))
    sel["주소"] = sel["YJ"].map(lambda y: done.get(y, {}).get("주소", ""))
    sel["메모"] = sel["YJ"].map(lambda y: done.get(y, {}).get("메모", ""))
    got = sel[sel["효능"] != ""]
    print(f"\n효능 글 잘라냄 {len(got):,}/{len(sel):,} ({len(got) / max(1, len(sel)) * 100:.1f}%) · 받은 시도 중 오류 {err}")
    print("   못 자른 이유:", sel.loc[sel["효능"] == "", "메모"].value_counts().head(6).to_dict())
    print(f"   효능 글 길이 중앙값 {got['효능'].str.len().median():.0f}자 · 200자 넘는 비율 {(got['효능'].str.len() > 200).mean() * 100:.0f}%")
    sel.to_csv(f"{OUT}/jp_labels.csv", index=False, encoding="utf-8-sig")
    if not N:
        evaluate(sel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
