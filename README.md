# 입찰 레이더 (GitHub 자동 수집 버전)

나라장터 실내건축 입찰공고를 매일 자동 수집해서 웹페이지로 보여줍니다.
PC를 꺼도 동작하고, 링크만 있으면 누구나 볼 수 있습니다 (보기 전용).

- 자동 수집: 매일 한국시간 07:50 / 11:50 / 16:50
- 인증키: 저장소 Secrets에만 보관 (코드·화면에 노출 안 됨)
- 키워드 수정: `keywords.txt` 를 GitHub 화면에서 바로 편집 → 다음 수집부터 반영

## 처음 설정 (한 번만)

1. **GitHub 가입** → https://github.com
2. **저장소 만들기**: 오른쪽 위 `+` → New repository
   - Repository name: `bid-radar`
   - **Public** 선택 (무료 웹페이지는 Public만 가능 / 인증키는 공개되지 않음)
   - Create repository
3. **파일 올리기**: 저장소 화면의 `uploading an existing file` 클릭
   → 압축 푼 `bid-radar` 폴더 **안의 내용 전체**를 끌어다 놓기 → Commit changes
   - `.github` 폴더가 안 올라갔으면: Add file → Create new file →
     이름 칸에 `.github/workflows/collect.yml` 입력 → 같은 파일 내용 붙여넣기 → Commit
4. **인증키 등록**: Settings → Secrets and variables → Actions → New repository secret
   - Name: `SERVICE_KEY`
   - Secret: 공공데이터포털 일반 인증키 (`.env`에 넣었던 값 그대로)
5. **웹페이지 켜기**: Settings → Pages
   - Source: Deploy from a branch
   - Branch: `main` / 폴더: `/docs` → Save
6. **첫 수집 실행**: Actions 탭 → (워크플로 사용 안내가 나오면 Enable) →
   `나라장터 공고 수집` → Run workflow
   - 초록 ✓ 이면 성공, 빨간 ✕ 면 클릭해서 `공고 수집` 단계 로그 확인
7. 1~2분 뒤 `https://<내 아이디>.github.io/bid-radar/` 접속 → 이 링크를 공유

## 자주 하는 작업
- 지금 바로 수집: Actions → 나라장터 공고 수집 → Run workflow
- 수집 시간 변경: `.github/workflows/collect.yml` 의 cron (UTC 기준, 한국시간 -9시간)
- 내 PC에서 실행(기존 방식): `.env` 만들고 `python app.py`
