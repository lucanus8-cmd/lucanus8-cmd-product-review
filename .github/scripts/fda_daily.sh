#!/usr/bin/env bash
# FDA 매일 갱신: FDA 공개 자료 → 국내 허가·임상과 비교 → 구글 시트 '제품검토 · FDA 비교' → 사이트용 파일(data 가지 fda_site/)
# 사이트 코드(main 가지)는 건드리지 않는다. 원본 시트가 갱신 중이거나 자료가 모자라면 오늘은 건너뛴다(어제 결과 유지).
# SAVE=0 이면 data 가지에 저장하지 않는다.
set -u
S=.github/scripts
python -m pip install --quiet pandas ijson openpyxl google-auth google-api-python-client pyarrow || exit 1

OUT_DIR=out/fda PREV_DIR=none python $S/build_fda_tables.py || { echo "FDA 표 만들기 실패"; exit 1; }

USKR_OUT=out/uskr python $S/check_us_kr.py; rc=$?
if [ $rc -eq 3 ]; then echo "오늘은 건너뜀 — 구글 시트·사이트는 어제 결과 그대로"; exit 0; fi
if [ $rc -ne 0 ]; then echo "국내 비교 실패"; exit 1; fi

OUT_DIR=out/fda USKR_OUT=out/uskr python $S/write_fda_sheet.py; rc=$?
if [ $rc -eq 3 ]; then echo "시트 쓰기 건너뜀 — 어제 결과 그대로"; exit 0; fi
if [ $rc -ne 0 ]; then echo "시트 쓰기 실패"; exit 1; fi

OUT_DIR=out/fda USKR_OUT=out/uskr SITE_OUT=out/fda_site python $S/build_fda_site.py; rc=$?
if [ $rc -eq 3 ]; then echo "사이트용 파일 건너뜀 — 어제 파일 그대로"; exit 0; fi
if [ $rc -ne 0 ]; then echo "사이트용 파일 실패"; exit 1; fi

if [ "${SAVE:-1}" != "1" ]; then echo "저장 안 함 (SAVE=0)"; exit 0; fi
git config user.name "fda-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
# 다른 로봇(DMF)이 같은 가지에 동시에 저장하면 밀릴 수 있다 → 새로 받아서 다시 시도
for i in 1 2 3 4; do
  rm -rf databr_site; git worktree prune
  if git fetch -q --depth=1 origin data && git worktree add -q databr_site FETCH_HEAD; then
    if (cd databr_site && git checkout -q -B data && mkdir -p fda_site && cp ../out/fda_site/* fda_site/ \
        && git add fda_site \
        && { git diff --cached --quiet || git commit -qm "FDA 비교(사이트용) $(date -u -d '+9 hours' +%Y-%m-%d)"; } \
        && git push -q origin data); then
      echo "data 가지 fda_site/ 에 저장함"; exit 0
    fi
  fi
  echo "저장 다시 시도 ($i)"; sleep $((20 * i))
done
echo "data 가지 저장 실패"; exit 1
