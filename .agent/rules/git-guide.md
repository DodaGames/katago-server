---
trigger: model_decision
description: when committing changes, pushing code, or managing git branches
---

# Git 가이드 (Git Guide)

- git commit 시 commit lint 규칙을 준수하세요.
- commit 메시지는 한국어로 작성하세요.
- commit 메세지에 `Co-authored-by`를 사용하지 마세요.
- 별도의 명시적 지시가 없으면 새 브랜치를 생성하지 마세요.
- git commit 시 husky에 의해 'pnpm check:all' 테스트가 자동으로 실행됩니다. 굳이 별도로 실행하지 마세요.
