import json, sys
a, b = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
print("\n── 결과 비교 (지금 사이트 vs v2) ──")
for k in a:
    if k.startswith("_"):
        continue
    x, y = a.get(k, {}), b.get(k, {})
    if "실패" in x or "실패" in y:
        print(f"{k}: 비교 못 함 (지금 {x.get('실패','ok')} / v2 {y.get('실패','ok')})"); continue
    if "후보" in x:
        print(f"{k}: 후보 수 {'같음' if x['후보']==y.get('후보') else '다름'} (지금 {x['후보']} / v2 {y.get('후보')}) · 오류 {x['오류']}/{y.get('오류')}")
        continue
    same_pub = x["공개"] == y["공개"]
    same_sales = x["매출지문"] == y["매출지문"]
    print(f"{k}: 허가·특허·임상·약가·DMF 건수 {'같음' if same_pub else '다름'} · 매출 지표 {'같음' if same_sales else '다름'} (칸 {x['매출칸수']}/{y['매출칸수']}) · 오류 {x['오류']}/{y['오류']}")
    if not same_pub:
        print("    지금만:", [t for t in x["공개"] if t not in y["공개"]][:6])
        print("    v2만  :", [t for t in y["공개"] if t not in x["공개"]][:6])
print(f"\n메모리 — 지금 사이트 최고 {a['_mem']['최고']} MB · v2 최고 {b['_mem']['최고']} MB")
