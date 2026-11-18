"""미국(FDA)·일본(후생노동성 약가 목록) 허가 의약품과 국내 허가를 '주성분(염 무시) + 제형'으로
맞출 수 있는지 재 본다. 사이트와 상관없다. 공개 자료만 읽고, 건수와 예시만 적는다(매출 없음).

순서: A 국내 허가 → B 미국 → C 일본 약가 목록 → D 일본 성분명 영문 대조 → E 세 나라 맞추기
"""
import csv
import io
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict

import pandas as pd

UA = {"User-Agent": "Mozilla/5.0 (product-review feasibility check)"}
OUT = {}


def get(url, timeout=120, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def head(t):
    print(f"\n{'=' * 8} {t}")


# ── 염·수화물 떼기 (에스터는 남긴다) ─────────────────────────────────
EN_SALT = ["hydrochloride", "dihydrochloride", "trihydrochloride", "hydrobromide", "hydroiodide", "hcl",
           "mesylate", "mesilate", "dimesylate", "besylate", "besilate", "tosylate", "tosilate", "edisylate",
           "esylate", "napsylate", "maleate", "dimaleate", "fumarate", "hemifumarate", "succinate", "hemisuccinate",
           "tartrate", "bitartrate", "hemitartrate", "citrate", "dihydrogen citrate", "sulfate", "bisulfate",
           "hemisulfate", "sulphate", "phosphate", "diphosphate", "dihydrogen phosphate", "nitrate", "lactate",
           "gluconate", "lactobionate", "malate", "oxalate", "benzoate", "salicylate", "camsylate", "isethionate",
           "pamoate", "embonate", "xinafoate", "stearate", "orotate", "aspartate", "glutamate", "carbonate",
           "bicarbonate", "chloride", "bromide", "iodide", "sodium", "disodium", "trisodium", "monosodium",
           "potassium", "dipotassium", "calcium", "magnesium", "zinc", "aluminum", "lysine", "arginine",
           "meglumine", "tromethamine", "trometamol", "olamine", "diolamine", "erbumine", "choline",
           "benzathine", "procaine", "hydrate", "monohydrate", "dihydrate", "trihydrate", "tetrahydrate",
           "pentahydrate", "hemihydrate", "sesquihydrate", "anhydrous", "hemipentahydrate", "solvate",
           "ethanolate", "acid"]
EN_SALT_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, EN_SALT), key=len, reverse=True)) + r")\b", re.I)
EN_SYN = {"acetaminophen": "paracetamol", "albuterol": "salbutamol", "epinephrine": "adrenaline",
          "norepinephrine": "noradrenaline", "glyburide": "glibenclamide", "meperidine": "pethidine",
          "cyclosporine": "ciclosporin", "furosemide": "furosemide", "levalbuterol": "levosalbutamol",
          "acyclovir": "aciclovir", "valacyclovir": "valaciclovir", "rifampin": "rifampicin",
          "cephalexin": "cefalexin", "isoproterenol": "isoprenaline", "lidocaine": "lidocaine",
          "dimenhydrinate": "dimenhydrinate", "estradiol": "estradiol", "phenobarbital": "phenobarbital",
          "sulfamethoxazole": "sulfamethoxazole", "amphetamine": "amfetamine", "dextroamphetamine": "dexamfetamine",
          "methamphetamine": "metamfetamine", "thiamine": "thiamine", "colestipol": "colestipol",
          "glycopyrrolate": "glycopyrronium", "ipratropium": "ipratropium", "tiotropium": "tiotropium",
          "mesalamine": "mesalazine", "ethacrynic": "etacrynic", "beclomethasone": "beclometasone",
          "salmeterol": "salmeterol", "nitroglycerin": "glyceryl trinitrate", "torsemide": "torasemide",
          "sumatriptan": "sumatriptan", "levothyroxine": "levothyroxine", "liothyronine": "liothyronine",
          "dicyclomine": "dicycloverine", "trimeprazine": "alimemazine", "chlorpheniramine": "chlorphenamine",
          "pheniramine": "pheniramine", "bupivacaine": "bupivacaine", "cromolyn": "cromoglicate",
          "niacin": "nicotinic", "vitamin": "vitamin", "penicillin g": "benzylpenicillin",
          "penicillin v": "phenoxymethylpenicillin", "oxymetazoline": "oxymetazoline"}


def en_moiety(name):
    """영문 성분 하나 → 모체 이름 (소문자). 염·수화물 단어를 떼고, 다 떼어지면 원래 이름."""
    n = unicodedata.normalize("NFKC", name or "").lower()
    n = re.sub(r"\([^)]*\)", " ", n)
    n = re.sub(r"[^a-z0-9 \-]", " ", n)
    full = re.sub(r"\s+", " ", n).strip()
    m = re.sub(r"\s+", " ", EN_SALT_RE.sub(" ", full)).strip(" -")
    if not m or m in ("oxide", "hydroxide", "peroxide", "trisilicate", "subsalicylate"):
        m = full
    for a, b in EN_SYN.items():
        if m == a or m.startswith(a + " "):
            m = b + m[len(a):]
            break
    return m


def en_set(names):
    return tuple(sorted({en_moiety(x) for x in names if (x or "").strip()}))


# 제형 묶음 (공통 이름, 정규식) — 위에서부터 먼저 맞는 것
FORM_EN = [
    ("서방 주사", r"inject.*(extended|prolonged)|(extended|prolonged).*inject|depot|microsphere"),
    ("주사", r"inject|infusion|intravenous|subcutaneous|intramuscular|for solution;? ?(iv|im|sc)|powder.*inject"),
    ("구강붕해정", r"orally disintegrating|\bodt\b"),
    ("츄어블", r"chewable"),
    ("발포정", r"effervescent"),
    ("서방정", r"tablet.*(extended|controlled|sustained|prolonged|modified)|(extended|controlled|sustained).*tablet"),
    ("장용정", r"tablet.*delayed|delayed.*tablet|enteric.*tablet|tablet.*enteric"),
    ("서방캡슐", r"capsule.*(extended|sustained|controlled|delayed|prolonged)"),
    ("캡슐", r"capsule"),
    ("정제", r"tablet,? film coated|film coated tablet"),
    ("구강필름", r"\bfilm\b(?!,? coated).*(buccal|sublingual|oral)|(buccal|sublingual).*\bfilm\b|oral film"),
    ("설하정", r"sublingual|buccal"),
    ("정제", r"tablet"),
    ("흡입", r"inhal|aerosol.*metered|powder.*inhal|nebuli"),
    ("비강", r"nasal"),
    ("점안", r"ophthalmic"),
    ("점이", r"otic"),
    ("경피 패치", r"transdermal|patch"),
    ("좌제", r"suppositor|rectal"),
    ("질정", r"vaginal"),
    ("외용(크림·연고·겔 등)", r"cream|ointment|gel|lotion|topical|foam|shampoo|cutaneous"),
    ("경구 현탁·건조시럽", r"suspension|for suspension"),
    ("경구 액·시럽", r"solution|syrup|elixir|oral.*liquid|drops|concentrate"),
    ("산·과립", r"powder|granule|pellet|sachet"),
]
FORM_JA = [
    ("서방 주사", r"持続性.*注|注.*徐放|デポ"),
    ("주사", r"注|点滴|輸液"),
    ("구강붕해정", r"OD錠|口腔内崩壊|ＯＤ錠"),
    ("츄어블", r"チュアブル"),
    ("발포정", r"発泡"),
    ("서방정", r"徐放錠|ＬＡ錠|LA錠|ＣＲ錠|CR錠|SR錠|ＳＲ錠|XR錠|ＸＲ錠"),
    ("장용정", r"腸溶錠"),
    ("서방캡슐", r"徐放.*カプセル|腸溶.*カプセル|ＳＲカプセル|SRカプセル"),
    ("캡슐", r"カプセル|Ｃ\b"),
    ("구강필름", r"フィルム"),
    ("설하정", r"舌下|バッカル"),
    ("정제", r"錠"),
    ("흡입", r"吸入|エアゾール|ディスカス|タービュヘイラー|エリプタ|ブリーズヘラー|レスピマット"),
    ("비강", r"点鼻|鼻"),
    ("점안", r"点眼|眼"),
    ("점이", r"点耳|耳"),
    ("경피 패치", r"テープ|パップ|貼付|パッチ|プラスター"),
    ("좌제", r"坐剤|坐薬|直腸|注腸"),
    ("질정", r"腟"),
    ("외용(크림·연고·겔 등)", r"軟膏|クリーム|ゲル|ローション|外用|スプレー|液剤|フォーム|シャンプー"),
    ("경구 현탁·건조시럽", r"ドライシロップ|懸濁"),
    ("경구 액·시럽", r"シロップ|内用液|液|エリキシル"),
    ("산·과립", r"散|顆粒|細粒|末"),
]
FORM_KO = [
    ("서방 주사", r"서방.*주|지속.*주|데포"),
    ("주사", r"주사|주\d|주$|주\(|주사액|프리필드|펜주|키트주|바이알"),
    ("구강붕해정", r"구강붕해|오디정|ODT"),
    ("츄어블", r"츄어블|저작"),
    ("발포정", r"발포"),
    ("서방정", r"서방정|이알정|씨알정|엑스알정|XR정|SR정|CR정|ER정|서방성.*정|지속정"),
    ("장용정", r"장용정|장용코팅정"),
    ("서방캡슐", r"서방.*캡슐|장용.*캡슐|서방성.*캡슐"),
    ("캡슐", r"캡슐|캅셀"),
    ("구강필름", r"필름(?!코팅)|구강용해필름"),
    ("설하정", r"설하"),
    ("정제", r"정"),
    ("흡입", r"흡입|에어로솔|디스커스|엘립타|레스피맷|흡입기"),
    ("비강", r"비강|점비|나잘"),
    ("점안", r"점안|안약|안연고|아이드롭"),
    ("점이", r"점이"),
    ("경피 패치", r"패취|패치|첩부|플라스타|카타플라스마"),
    ("좌제", r"좌제|좌약|관장"),
    ("질정", r"질정|질좌|질용"),
    ("외용(크림·연고·겔 등)", r"크림|연고|겔|로션|외용|스프레이|폼|샴푸|액$"),
    ("경구 현탁·건조시럽", r"건조시럽|현탁"),
    ("경구 액·시럽", r"시럽|내용액|액|엘릭서|드롭"),
    ("산·과립", r"산|과립|세립"),
]


def form_of(text, table):
    t = text or ""
    for lab, rx in table:
        if re.search(rx, t, re.I):
            return lab
    return None


def same_form_group(f):
    return f


# ═══════════════ A. 국내 허가 ═══════════════
def korea():
    head("A. 국내 허가 자료 (식약처)")
    sid = "1x0lPMvsH0G-9cOdwi1kZy5yTAhAaz9N5BluK36iS8-Q"
    try:
        raw = get(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv", timeout=300)
        if raw[:15].lstrip().lower().startswith(b"<!doctype") or raw[:6].lower() == b"<html":
            raise RuntimeError("시트가 공개 링크로 열리지 않음 (사이트는 서비스 계정으로 읽음)")
        df = pd.read_csv(io.BytesIO(raw), dtype=str, low_memory=False).fillna("")
        df.columns = [str(c).split("\n")[0].strip() for c in df.columns]
        print(f"허가 시트 공개 링크로 읽힘: {len(df):,}행 · 칸 {list(df.columns)[:40]}")
        return df
    except Exception as e:
        print("허가 시트 공개 링크:", str(e)[:200])
    # 공공 경로 탐색: 의약품안전나라 공공데이터 화면
    for url in ["https://nedrug.mfds.go.kr/pbp/CCBGA01/getItem?totalPages=1",
                "https://nedrug.mfds.go.kr/cntnts/20"]:
        try:
            t = get(url, timeout=60).decode("utf-8", "ignore")
            links = sorted(set(re.findall(r'href="([^"]*(?:down|excel|xls|csv|file)[^"]*)"', t, re.I)))[:15]
            print(f"{url} → {len(t):,}자, 내려받기 같은 링크: {links}")
        except Exception as e:
            print(url, "못 읽음", str(e)[:120])
    return None


# ═══════════════ B. 미국 ═══════════════
def usa():
    head("B. 미국 Drugs@FDA (openFDA 전체 파일)")
    idx = json.loads(get("https://api.fda.gov/download.json", timeout=60))
    parts = idx["results"]["drug"]["drugsfda"]["partitions"]
    print("파일:", [(p["file"].split("/")[-1], p.get("size_mb")) for p in parts], "기준일", idx["results"]["drug"]["drugsfda"].get("export_date"))
    apps = []
    for p in parts:
        z = zipfile.ZipFile(io.BytesIO(get(p["file"], timeout=300)))
        for n in z.namelist():
            apps += json.loads(z.read(n))["results"]
    rows = []
    for a in apps:
        no = a.get("application_number", "")
        kind = "BLA" if no.startswith("BLA") else ("ANDA" if no.startswith("ANDA") else "NDA")
        orig = [s.get("submission_status_date", "") for s in a.get("submissions") or []
                if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP"]
        for pr in a.get("products") or []:
            ings = [x.get("name", "") for x in pr.get("active_ingredients") or []]
            rows.append({"app": no, "kind": kind, "brand": pr.get("brand_name", ""),
                         "ings": ings, "form": pr.get("dosage_form", ""), "route": pr.get("route", ""),
                         "status": pr.get("marketing_status", ""), "date": min(orig) if orig else ""})
    us = pd.DataFrame(rows)
    print(f"신청 {len(apps):,}건 · 제품 {len(us):,}개 · 판매 상태 {Counter(us['status']).most_common(6)}")
    us = us[us["status"].isin(["Prescription", "Over-the-counter"])].copy()
    us["moi"] = us["ings"].map(en_set)
    us["fg"] = [form_of(f"{f} {r}", FORM_EN) for f, r in zip(us["form"], us["route"])]
    print(f"판매 중 제품 {len(us):,}개 · 주성분 묶음 {us['moi'].nunique():,}가지 (단일 {sum(len(m) == 1 for m in us['moi'].unique()):,})"
          f" · 제형 묶음 판독 {us['fg'].notna().mean() * 100:.1f}%")
    print("   못 읽은 제형 예:", Counter(f"{f} | {r}" for f, r, g in zip(us['form'], us['route'], us['fg']) if not g).most_common(12))
    print("   염 떼기 예:", [(i, en_moiety(i)) for i in ["AMLODIPINE BESYLATE", "METFORMIN HYDROCHLORIDE",
                                                     "ESOMEPRAZOLE MAGNESIUM", "POTASSIUM CHLORIDE",
                                                     "ESTRADIOL VALERATE", "DABIGATRAN ETEXILATE MESYLATE",
                                                     "TENOFOVIR DISOPROXIL FUMARATE", "OCTREOTIDE ACETATE",
                                                     "LEVOTHYROXINE SODIUM", "ALBUTEROL SULFATE"]])
    try:
        b = get("https://www.fda.gov/media/76860/download", timeout=120)
        print(f"Orange Book 압축 파일 받음 {len(b) / 1e6:.1f}MB · 안의 파일 {zipfile.ZipFile(io.BytesIO(b)).namelist()}")
    except Exception as e:
        print("Orange Book 못 받음:", str(e)[:150])
    return us


# ═══════════════ C. 일본 약가 목록 ═══════════════
JA_SALT = ["メタンスルホン酸塩", "ベンゼンスルホン酸塩", "パラトルエンスルホン酸塩", "エタンスルホン酸塩",
           "エタンジスルホン酸塩", "臭化物", "塩化物", "ヨウ化物", "塩酸塩", "臭化水素酸塩", "メシル酸塩", "ベシル酸塩", "トシル酸塩", "マレイン酸塩", "フマル酸塩",
           "コハク酸塩", "酒石酸塩", "クエン酸塩", "硫酸塩", "リン酸塩", "硝酸塩", "乳酸塩", "グルコン酸塩",
           "酢酸塩", "安息香酸塩", "シュウ酸塩", "リンゴ酸塩", "カンシル酸塩", "イセチオン酸塩", "パモ酸塩",
           "キシナホ酸塩", "ナトリウム", "カリウム", "カルシウム", "マグネシウム", "亜鉛", "メグルミン",
           "水和物", "一水和物", "二水和物", "三水和物", "半水和物", "無水物", "エタノール付加物", "水和物塩",
           "塩", "水和物"]
JA_SALT_RE = re.compile("(" + "|".join(sorted(map(re.escape, JA_SALT), key=len, reverse=True)) + r")+$")


def ja_moiety(n):
    n = unicodedata.normalize("NFKC", str(n or "")).strip()
    n = re.sub(r"[（(][^）)]*[）)]", "", n).replace(" ", "").replace("　", "")
    m = JA_SALT_RE.sub("", n)
    if len(m) < 2 or m.endswith("化") or (m.endswith("酸") and len(m) <= 3):   # 塩化カリウム·炭酸カルシウム 같은 무기물
        m = n
    # 앞에 붙는 염 (예: 'L-' 는 남긴다)
    return m


def japan():
    head("C. 일본 후생노동성 약가 등재 목록")
    page = "https://www.mhlw.go.jp/topics/2025/04/tp20250401-01.html"
    t = get(page, timeout=60).decode("utf-8", "ignore")
    title = re.search(r"<title>(.*?)</title>", t, re.S)
    print("화면:", (title.group(1).strip() if title else "")[:120])
    links = re.findall(r'href="([^"]+\.(?:xlsx|xls))"[^>]*>(.*?)</a>', t, re.S)
    links = [(urllib.parse.urljoin(page, h), re.sub(r"<[^>]+>|\s+", " ", x).strip()) for h, x in links]
    print(f"엑셀 링크 {len(links)}개:", [x[1][:30] for x in links][:12])
    frames = []
    for url, txt in links:
        if not re.search(r"内用|注射|外用|歯科|薬価", txt + url):
            continue
        try:
            b = get(url, timeout=180)
            x = pd.read_excel(io.BytesIO(b), dtype=str, header=None)
            # 머리줄 찾기
            hi = next(i for i in range(min(15, len(x))) if any("成分名" in str(v) for v in x.iloc[i]))
            x.columns = [str(c).strip() for c in x.iloc[hi]]
            x = x.iloc[hi + 1:].copy()
            x["_file"] = txt[:20]
            frames.append(x)
            print(f"   {txt[:30]}: {len(x):,}행 · 칸 {list(x.columns)[:12]}")
        except Exception as e:
            print(f"   {txt[:30]} 못 읽음: {str(e)[:120]}")
    if not frames:
        return None
    jp = pd.concat(frames, ignore_index=True)
    cols = {c: c for c in jp.columns}
    name_col = next(c for c in jp.columns if "成分名" in c)
    item_col = next((c for c in jp.columns if "品名" in c), None)
    spec_col = next((c for c in jp.columns if "規格" in c), None)
    print(f"합계 {len(jp):,}행 · 성분명 칸 '{name_col}' · 품명 칸 '{item_col}' · 규격 칸 '{spec_col}'")
    jp["moi_ja"] = jp[name_col].map(lambda s: tuple(sorted({ja_moiety(p) for p in re.split(r"[・／/、,]", str(s)) if p.strip()})))
    jp["fg"] = [form_of(f"{a} {b} {f}", FORM_JA) for a, b, f in zip(jp[item_col].astype(str) if item_col else "",
                                                                      jp[spec_col].astype(str) if spec_col else "",
                                                                      jp["_file"])]
    print(f"성분 묶음 {jp['moi_ja'].nunique():,}가지 · 제형 판독 {jp['fg'].notna().mean() * 100:.1f}%")
    print("   못 읽은 제형 예:", Counter(str(a)[:25] for a, g in zip(jp[item_col], jp["fg"]) if not g).most_common(10))
    print("   염 떼기 예:", [(s, ja_moiety(s)) for s in ["アムロジピンベシル酸塩", "メトホルミン塩酸塩", "エソメプラゾールマグネシウム水和物",
                                                   "塩化カリウム", "エストラジオール吉草酸エステル", "ダビガトランエテキシラートメタンスルホン酸塩"]])
    return jp


# ═══════════════ D. 일본 성분명 → 영문 ═══════════════
def ja_en_map():
    head("D. 일본 성분명 영문 대조")
    q = """SELECT ?item ?ja ?en WHERE {
      { ?item wdt:P267 ?atc } UNION { ?item wdt:P2275 ?inn } UNION { ?item wdt:P3345 ?rx }
      ?item rdfs:label ?en FILTER(lang(?en)="en")
      { ?item rdfs:label ?ja FILTER(lang(?ja)="ja") } UNION { ?item skos:altLabel ?ja FILTER(lang(?ja)="ja") }
    }"""
    m = {}
    try:
        b = get("https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q), timeout=180,
                headers={"Accept": "application/sparql-results+json"})
        res = json.loads(b)["results"]["bindings"]
        for r in res:
            ja = ja_moiety(r["ja"]["value"])
            en = en_moiety(r["en"]["value"])
            if ja and en:
                m.setdefault(ja, en)
        print(f"위키데이터: 약 이름 일본어↔영문 {len(res):,}쌍 → 일본 모체 {len(m):,}개")
    except Exception as e:
        print("위키데이터 못 읽음:", str(e)[:200])
    for url in ["https://jpdb.nihs.go.jp/jan/", "https://www.pmda.go.jp/english/review-services/reviews/approved-information/drugs/0001.html"]:
        try:
            t = get(url, timeout=60).decode("utf-8", "ignore")
            ls = sorted(set(re.findall(r'href="([^"]+\.(?:pdf|csv|xlsx|xls|zip))"', t, re.I)))
            print(f"{url} → {len(t):,}자 · 파일 링크 {len(ls)}개 예 {ls[:8]}")
            if "pmda" in url and ls:
                big = [urllib.parse.urljoin(url, x) for x in ls][:3]
                try:
                    from pypdf import PdfReader
                    pdf = PdfReader(io.BytesIO(get(big[0], timeout=180)))
                    txt = "\n".join((pdf.pages[i].extract_text() or "") for i in range(min(2, len(pdf.pages))))
                    print(f"   PMDA 첫 PDF {len(pdf.pages)}쪽 · 앞부분: {txt[:600]!r}")
                except Exception as e:
                    print("   PMDA PDF 못 읽음:", str(e)[:150])
        except Exception as e:
            print(url, "못 읽음:", str(e)[:150])
    return m


# ═══════════════ E. 맞추기 ═══════════════
def match(us, jp, m, kr):
    head("E. 맞추기")
    if jp is not None:
        jp_names = Counter(x for t in jp["moi_ja"] for x in t)
        hit = {n: m[n] for n in jp_names if n in m}
        print(f"일본 모체 이름 {len(jp_names):,}개 중 영문으로 바뀐 것 {len(hit):,}개 ({100 * len(hit) / max(1, len(jp_names)):.1f}%)"
              f" · 행 기준 {100 * sum(c for n, c in jp_names.items() if n in m) / max(1, sum(jp_names.values())):.1f}%")
        miss = [n for n, _ in jp_names.most_common() if n not in m]
        print("   못 바꾼 이름 (행 많은 순):", miss[:40])
        jp["moi_en"] = jp["moi_ja"].map(lambda t: tuple(sorted(m[x] for x in t)) if all(x in m for x in t) else None)
    if us is None or jp is None:
        return
    us_set = {(mo, f) for mo, f in zip(us["moi"], us["fg"]) if f}
    jp_set = {(mo, f) for mo, f in zip(jp["moi_en"], jp["fg"]) if mo and f}
    us_moi = {mo for mo, _ in us_set}
    jp_moi = {mo for mo, _ in jp_set}
    both = us_set & jp_set
    print(f"미국 (주성분+제형) {len(us_set):,} · 일본 {len(jp_set):,} · 둘 다 {len(both):,}"
          f" · 주성분만 보면 미국 {len(us_moi):,} · 일본 {len(jp_moi):,} · 둘 다 {len(us_moi & jp_moi):,}")
    if kr is None:
        print("국내 자료가 없어 국내 비교는 못 함")
        return
    kcols = list(kr.columns)
    name_c = next((c for c in kcols if c == "품목명"), None)
    eng_c = next((c for c in kcols if c == "주성분(영문)"), None)
    gb_c = next((c for c in kcols if "전문" in c), None)
    st_c = next((c for c in kcols if c == "상태"), None)
    print("국내 전문/일반:", Counter(kr[gb_c]).most_common(5) if gb_c else "-", "· 상태:", Counter(kr[st_c]).most_common(5) if st_c else "-")
    k = kr[kr[st_c].eq("정상")] if st_c else kr
    k = k[k[gb_c].isin(["전문", "일반"])] if gb_c else k
    k = k.copy()
    k["moi"] = k[eng_c].map(lambda s: en_set(re.split(r"/|;|\+|,(?=\s*[A-Za-z])", str(s))) if str(s).strip() else None)
    k["fg"] = k[name_c].map(lambda s: form_of(re.sub(
        r"\(.*?\)|\[.*?\]|\d+(\.\d+)?\s*(밀리그램|마이크로그램|그램|밀리리터|리터|mg|μg|㎍|㎎|g|mL|㎖|%|퍼센트|단위|IU|국제단위).*$",
        "", str(s)), FORM_KO))
    print(f"국내 정상 전문+일반 {len(k):,}품목 · 영문 성분명 있음 {k['moi'].notna().mean() * 100:.1f}% · 제형 판독 {k['fg'].notna().mean() * 100:.1f}%")
    print("   못 읽은 제형 예:", Counter(str(n)[-12:] for n, g in zip(k[name_c], k["fg"]) if not g).most_common(12))
    kr_set = {(mo, f) for mo, f in zip(k["moi"], k["fg"]) if mo and f}
    kr_moi = {mo for mo in k["moi"] if mo}
    gap_moi = sorted({mo for mo, f in both if mo not in kr_moi})
    gap_form = sorted({(mo, f) for mo, f in both if mo in kr_moi and (mo, f) not in kr_set})
    print(f"\n▶ 미국·일본 둘 다 있고 국내에 성분이 없음: {len(gap_moi):,}가지")
    print("   예:", [" + ".join(x) for x in gap_moi[:40]])
    print(f"▶ 국내에 성분은 있고 그 제형이 없음: {len(gap_form):,}가지")
    print("   예:", [f"{' + '.join(mo)} [{f}]" for mo, f in gap_form[:40]])


def main():
    kr = us = jp = None
    m = {}
    for name, fn in (("국내", korea), ("미국", usa), ("일본", japan)):
        try:
            v = fn()
            if name == "국내":
                kr = v
            elif name == "미국":
                us = v
            else:
                jp = v
        except Exception as e:
            print(f"[{name}] 실패: {type(e).__name__} {str(e)[:200]}")
    try:
        m = ja_en_map()
    except Exception as e:
        print("[대조] 실패:", str(e)[:200])
    try:
        match(us, jp, m, kr)
    except Exception as e:
        import traceback
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
