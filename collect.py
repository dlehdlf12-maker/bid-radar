"""
GitHub Actions에서 실행되는 수집 스크립트
- 나라장터에서 공고를 받아 docs/data.json 으로 저장 → GitHub Pages 화면이 이 파일을 읽음
- 인증키는 GitHub 저장소 Settings → Secrets 의 SERVICE_KEY 에서 받아옴 (코드에 키 없음)
"""
import json
import sys
from pathlib import Path

import app

HERE = Path(__file__).parent
OUT = HERE / "docs" / "data.json"

app.DATA_FILE = OUT  # 저장 위치를 docs/ 로 변경

data = app.collect()
# 직전 결과와 비교용 로그
print(f"공고 {len(data.get('items', []))}건 / 오류 {len(data.get('errors', []))}건 → {OUT}")
for e in data.get("errors", []):
    print("  API 오류:", e)

# 수집 자체가 완전히 실패하면(전체 0건 + 오류) 이전 data.json 을 덮어쓰지 않도록 실패 처리
if data.get("total_notices", 0) == 0 and data.get("errors"):
    print("수집 실패 — 이전 데이터를 유지합니다.")
    sys.exit(1)
