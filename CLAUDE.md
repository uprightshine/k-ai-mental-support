# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

**K직장인용 걱정인형 (Worry Doll)** — CBT(인지행동치료) 기반 직장인 심리 케어 웹앱. 세줄일기 스타일 문답 UI로 "상황/자동화 사고/재구성" 3줄을 기록하면 Claude LLM이 CBT 왜곡 유형을 짚고 되묻기 형식 피드백을 제공.

## 기술 스택

- **백엔드**: FastAPI (Vercel @vercel/python 서버리스 함수)
- **프론트엔드**: Jinja2 템플릿 + Vanilla JS (번들러 없음)
- **LLM**: MiniMax Token Plan API (기본 `MiniMax-M2.7` — Token Plan이 `MiniMax-Text-01`/`M1`은 권한 없음으로 2061 반환함. `chatcompletion_v2` OpenAI 호환)
- **저장소**: 브라우저 `localStorage` (MVP) — 서버는 stateless
- **STT**: Web Speech API (브라우저 네이티브)
- **배포**: Vercel

## 자주 쓰는 커맨드

```bash
# 로컬 서버
source .venv/bin/activate
uvicorn api.index:app --reload --port 3000

# 의존성
pip install -r requirements.txt

# 배포
vercel              # 프리뷰
vercel --prod       # 프로덕션
```

## Vercel 운영 가이드

- **정본 프로젝트는 하나만 유지**: Vercel 프로젝트명은 `k-ai-mental-support`, 대표 주소는 `https://k-ai-mental-support.vercel.app`
- 비슷한 이름의 중복 프로젝트를 새로 만들지 말 것. 배포 전에 현재 링크를 먼저 확인:

```bash
cat .vercel/project.json
```

- 로컬 링크가 꼬였거나 Vercel 프로젝트를 삭제/재생성한 뒤에는 반드시 다시 동기화:

```bash
vercel pull --yes --environment production
vercel pull --yes --environment preview
```

- 배포 후 현재 프로덕션 alias 확인:

```bash
vercel inspect k-ai-mental-support.vercel.app
```

- `vercel build`가 로컬에 `pyproject.toml`, `uv.lock`를 임시 생성할 수 있음. 이 저장소의 기준 의존성 파일은 `requirements.txt`이며, 임시 파일은 커밋하지 말 것.
- `.vercel/` 디렉터리는 로컬 링크/환경변수 캐시용이다. Git에는 올리지 않는다.

LLM 피드백 활성화에는 `MINIMAX_API_KEY` 환경변수 필요 (옵션: `MINIMAX_MODEL`, `MINIMAX_BASE_URL`). 미설정 시 `api/index.py`의 `_fallback_feedback()`이 템플릿 응답을 반환 — UI 흐름은 막히지 않음.

## 아키텍처

### 요청 흐름

`브라우저 → POST /api/analyze → 크라이시스 키워드 검사 → (통과 시) MiniMax /v1/text/chatcompletion_v2 → JSON 파싱 → FeedbackPayload 반환 → localStorage 저장`

### 핵심 파일

- `api/index.py` — 단일 FastAPI 앱. 템플릿·정적·API 모두 이 함수 하나가 처리 (Vercel 라우트는 `vercel.json`에서 모두 `/api/index.py`로 흘려보냄).
- `static/app.js` — 탭 전환, 폼 제출, STT, localStorage CRUD, 피드백 카드 렌더링.
- `templates/index.html` — 단일 페이지. 쓰기·기록·설정 3탭.

### 안전 장치 (수정 시 주의)

1. **크라이시스 키워드 필터** (`CRISIS_PATTERNS` in `api/index.py`): 자살·자해 등 키워드 감지 시 LLM 호출을 건너뛰고 1393/1577-0199/1588-9191 핫라인을 즉시 반환. **이 검사를 우회하는 코드를 추가하지 말 것.**
2. **LLM 시스템 프롬프트** (`SYSTEM_PROMPT`): "진단 금지 / 사고를 대신 재구성하지 않고 질문으로 돌려주기 / 판단·훈계 금지"를 명시. 프롬프트 변경 시 이 3원칙을 유지해야 함 (README Pre-mortem 참조).
3. **JSON 스키마 강제 출력**: LLM 응답이 코드펜스나 부가 텍스트를 포함해도 `_extract_json()`이 `{...}` 블록만 추출. 파싱 실패 시 `_fallback_feedback()`으로 자동 폴백.

## CBT 설계 원칙 (README 발췌)

- 사고 분석·재구성 단계에는 AI 개입 금지 — 사용자 자력 훈련이 치료 효과의 핵심.
- AI는 **피드백 단계에만** 개입: 왜곡 유형 감지 + 되묻기 형식 질문.
- 왜곡 유형 목록 13개, 교정 방법론 5~7가지를 향후 RAG화 예정.

## 개발 단계

- **1차 (현재)**: 세줄 기록 UI + LLM 피드백 + localStorage
- **2차**: Vercel Postgres 전환, 사용자 프로필 기반 카테고리 매칭
- **3차**: 고민 유형별 익명 커뮤니티, 인사담당자 상담사 연결 (수익화)

## Python 버전 주의

Pydantic v2가 런타임에 타입 어노테이션을 eval함. Python 3.9 로컬 환경 호환을 위해 `str | None` 대신 `Optional[str]`, `list[str]` 대신 `List[str]` 사용 (`from __future__ import annotations`만으로는 불충분).
