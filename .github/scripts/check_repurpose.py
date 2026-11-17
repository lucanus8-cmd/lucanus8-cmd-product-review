"""사이트에 넣을 repurpose.py 를 실제 Open Targets 로 돌려 본다 (약 지도·질환 지도).

공개 자료(약·단백질·질환·임상 단계)만 적는다. 키 필요 없음. 읽기만 한다.

    WORDS="sildenafil;cough" python .github/scripts/check_repurpose.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
import repurpose as rp  # noqa: E402

W = ((os.environ.get("WORDS") or "").strip() or "sildenafil;cough").split(";")


def drug(q):
    t = time.time()
    c = rp.find_drug(q)
    print(f"\n######## 약 '{q}' → 후보 {c[:4]} ({time.time() - t:.1f}초)")
    if not c:
        return
    t = time.time()
    raw = rp.fetch_drug(c[0][0])
    print(f"받기 {time.time() - t:.1f}초 · 크기 {len(json.dumps(raw)) // 1024}KB · 표적 {len(raw['targets'])} · 적응증 {len(raw['ind'])}")
    for x in raw["targets"]:
        print(f"   표적 {x['symbol']} ({x['name']}) · {x['cls']} · {x['action']} → {rp.action_ko(x['action'])}")
    g = rp.drug_graph(raw, 5, 8, 0.2)
    for d in g["right"]:
        print(f"   {d['score']:.2f} {d['name'][:40]:40s} {d['label']:20s} 임상 {d['n_trials']} · 진행 중 {len(d['ongoing'])}"
              f" · 근거 {', '.join(f'{rp.DATATYPE_KO.get(k, k)} {v:.2f}' for k, v in d['dts'].items())}")
    print("   선:", len(g["edges"]), "· 가운데:", [(m["symbol"], round(m["y"], 1)) for m in g["mid"]])
    g2 = rp.drug_graph(raw, 5, 8, 0.2, hide_approved=True, only_ongoing=True)
    print("   (허가 숨김 + 진행 중만):", [(d["name"], len(d["ongoing"])) for d in g2["right"]])


def disease(q):
    t = time.time()
    c = rp.find_disease([q])
    print(f"\n######## 질환 '{q}' → 후보 {c[:6]} ({time.time() - t:.1f}초)")
    if not c:
        return
    t = time.time()
    raw = rp.fetch_disease(c[0][0])
    print(f"받기 {time.time() - t:.1f}초 · 크기 {len(json.dumps(raw)) // 1024}KB · 표적 {len(raw['targets'])} · 이미 쓰는 약 {len(raw['known'])}")
    for x in raw["targets"][:8]:
        print(f"   표적 {x['symbol']} ({x['name'][:50]}) · {x['cls']} · {x['score']:.2f} · 약 {len(raw['by_target'].get(x['id'], []))}")
    g = rp.disease_graph(raw, 6, 3)
    for d in g["right"]:
        print(f"   {d['name'][:30]:30s} 전체 {rp.stage_ko(d['stage']):6s} · 이 질환 {rp.stage_ko(d['dz_stage']) or '-':6s}"
              f" · 임상 {d['n_trials']} · 진행 중 {len(d['ongoing'])} · 표적 {[t for t, _ in d['targets']][:3]}")
    print("   선:", len(g["edges"]))


def main():
    a, b = (W + ["", ""])[:2]
    for q in [x for x in a.split(",") if x.strip()]:
        drug(q.strip())
    for q in [x for x in b.split(",") if x.strip()]:
        disease(q.strip())
    print("\n없는 이름:", rp.find_drug("zzqqxx"), rp.find_disease(["zzqqxx"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
