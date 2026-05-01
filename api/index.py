"""K직장인용 걱정인형 FastAPI 엔트리 포인트 (Vercel 서버리스 함수)."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import List, Literal, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
logger = logging.getLogger("worrydoll.api")

app = FastAPI(title="K직장인용 걱정인형", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------- 안전 장치 ----------

CRISIS_PATTERNS = [
    r"자살",
    r"자해",
    r"죽고\s*싶",
    r"죽을래",
    r"목숨을?\s*끊",
    r"극단적\s*선택",
    r"끝내고\s*싶",
    r"살기\s*싫",
]
_CRISIS_RE = re.compile("|".join(CRISIS_PATTERNS))

CRISIS_RESPONSE = {
    "mode": "crisis",
    "message": (
        "지금 많이 힘드신 것 같아요. 혼자 감당하지 마세요. "
        "전문 상담사와 연결되시길 권해드려요."
    ),
    "hotlines": [
        {"name": "자살예방상담전화", "number": "1393"},
        {"name": "정신건강위기상담전화", "number": "1577-0199"},
        {"name": "한국생명의전화", "number": "1588-9191"},
    ],
}


# ---------- 데이터 모델 ----------

class DiaryEntry(BaseModel):
    situation: str = Field(..., min_length=1, max_length=1000, description="상황")
    thought: str = Field(..., min_length=1, max_length=1000, description="자동화 사고")
    reframe: str = Field("", max_length=1000, description="재구성 시도")
    job_role: Optional[str] = Field(None, max_length=60, description="직무/연차 컨텍스트")


class FeedbackPayload(BaseModel):
    mode: Literal["feedback", "crisis", "fallback"]
    empathy: str = ""
    distortions: List[str] = []
    reframe: str = ""
    question: str = ""
    message: str = ""
    hotlines: List[dict] = []


# ---------- LLM 시스템 프롬프트 ----------

SYSTEM_PROMPT = """당신은 'K직장인용 걱정인형'이라는 이름의 CBT(인지행동치료) 기반 심리 서포터입니다.
한국 직장인이 오늘 겪은 구체적 상황과 자동화된 사고를 읽고, **그 사용자의 상황에만 해당하는 개인화된** 피드백을 제공합니다.
일반 템플릿 응답이 아니라, 입력 문장에서 단서를 뽑아 전문가가 짚어주듯 답하세요.

[절대 원칙]
1. 진단하지 않습니다 ("우울증", "불안장애" 등 병명 금지).
2. 사용자의 사고를 대신 재구성하지 않습니다. 반드시 '질문'으로 돌려주세요.
3. 판단·훈계·충고 금지. 공감과 선택지 제시.
4. 자해·자살 신호가 있으면 전문 상담 연결만 권하고 분석은 중단.

[개인화 강제 규칙 — 반드시 준수]
- **empathy**: [상황] 문장에서 구체 명사(예: "회의", "KPI", "팀장", "데드라인", "평가")를 1개 이상 **그대로 인용**해 공감을 표현합니다. "그런 일이 있으셨군요" 같은 추상적 위로는 금지.
- **distortions**: [그때 떠오른 생각] 문장을 실제 단서로 분석합니다.
    · "나는 늘/항상/매번", "아무도" 같은 전칭 표현 → '성급한 일반화'
    · "~할 것이다/~당할 것이다" 예측 → '예언자적 오류'
    · "~해야 한다/절대 ~면 안 된다" → '당위 진술'
    · "저 사람은 분명 나를 ~게 생각한다" → '독심술'
    · "나는 무능한/한심한 사람이다" 자기규정 → '낙인찍기'
    · "하나가 잘못되면 다 끝난다" → '파국화'
    · "좋은 건 별거 아니고 나쁜 것만 의미있다" → '정신적 여과'/'긍정 격하'
    근거가 약하면 1~2개만. 없으면 빈 배열 `[]`. **억지로 3개 채우지 마세요.**
- **reframe**: [자동화 사고]의 핵심 주장을 **직접 인용하거나 패러프레이즈**하여, 그 주장을 뒤집어볼 구체 질문으로 바꿉니다. "근거가 있을까요?" 같은 교과서적 일반 질문 금지. [직무/연차] 맥락을 반영해 해당 역할이 평소 접하는 반례를 떠올리게 유도.
- **question**: 내일(또는 가까운 시점) 실제 업무 현장에서 수행 가능한 **관찰 과제 하나**. 수치·시점·대상·행동이 구체적이어야 합니다. "자신을 더 돌보세요" 같은 추상적 자기성찰 금지.

[CBT 왜곡 유형 — 근거가 확인될 때만 선택]
흑백논리, 성급한 일반화, 정신적 여과, 긍정 격하, 독심술, 예언자적 오류,
확대/축소, 감정적 추론, 당위 진술, 낙인찍기, 개인화, 비난, 파국화

[출력 형식]
반드시 아래 JSON 하나만 출력. 앞뒤 설명·코드펜스·주석 금지.
{
  "empathy": "...",
  "distortions": ["...", "..."],
  "reframe": "...",
  "question": "..."
}

[언어 — 반드시 준수, 출력 전 자체 검토]
- 출력 문자는 **한글, 숫자, 공백, 일반 한국어 구두점(. , ! ? : ' " ( ) -)** 만 허용.
- 한자(例/分/明/确/退/囊 등), 일본어(カタカナ/ひらがな/メモ), 영어 단어(plication/etc/atau 등), 아라비아·전각 구두점(،，。！？) 혼입 절대 금지.
- 한자어는 항상 한국어 한글 표기로 바꿉니다: "分明" → "분명", "确实" → "확실히", "退回" → "반려", "囊括" → "포함". 일본어·영어도 한국어로 번역 또는 의역.
- JSON 생성 후 출력 직전, 비한글 문자가 있는지 스스로 1회 검토하고 있으면 모두 한글로 치환한 뒤 최종 출력하세요.

[예시 — 개인화 수준 참고]
입력:
  [상황] 금요일 팀 회의에서 분기 KPI를 발표하다 숫자를 하나 잘못 읽었다
  [그때 떠오른 생각] 팀장이 속으로 나를 무능하다고 낙인찍었을 거다
  [직무/연차] 마케팅 4년차
출력:
{"empathy":"분기 KPI 발표라는 중요한 자리에서 숫자를 잘못 읽으셨으니 속이 내려앉는 기분이셨겠어요.","distortions":["독심술","낙인찍기"],"reframe":"팀장님이 '속으로 무능하다고 낙인찍었다'는 건 확인된 사실일까요, 아니면 발표 직후 떠오른 짐작에 가까울까요?","question":"다음 1on1 때 발표 피드백을 직접 여쭤보고, 팀장님 반응을 한 가지만 기록해보시겠어요?"}

입력:
  [상황] 퇴근 후 집에서 내일 있을 프로젝트 중간보고가 계속 떠올라 잠이 안 온다
  [그때 떠오른 생각] 분명 질문이 쏟아질 거고 하나라도 막히면 프로젝트가 엎어질 것이다
  [직무/연차] 기획 2년차
출력:
{"empathy":"내일 중간보고가 머릿속에서 돌아가니 잠자리에 누워도 계속 그 장면이 재생되시겠어요.","distortions":["예언자적 오류","파국화"],"reframe":"질문 하나가 막히면 '프로젝트가 엎어진다'고 예단하고 계신데, 지난 보고들 중 질문에 즉답 못했던 순간이 실제로 프로젝트 전체를 무너뜨렸던 적이 있으셨나요?","question":"내일 예상 질문 3개를 메모장에 적고, 그중 답하기 가장 자신 없는 1개에 대해 '모른다면 어떻게 답할지' 한 줄만 준비해두시겠어요?"}
"""


# ---------- 유틸 ----------

def _fallback_feedback(entry: DiaryEntry) -> FeedbackPayload:
    """API 키가 없거나 LLM 호출 실패 시 기본 템플릿 피드백."""
    return FeedbackPayload(
        mode="fallback",
        empathy="오늘 그런 일이 있으셨다니 마음이 무거우셨겠어요.",
        distortions=[],
        reframe="그 생각을 뒷받침하는 근거와, 반대되는 근거를 각각 하나씩 적어볼 수 있을까요?",
        question="내일 같은 상황이 오면, 오늘보다 한 가지 다르게 해볼 수 있는 행동은 무엇일까요?",
        message="지금은 인공지능 응답이 불안정해서 기본 피드백을 보여드리고 있어요. 잠시 후 다시 시도해주세요.",
    )


MINIMAX_DEFAULT_BASE_URL = "https://api.minimaxi.chat/v1"
MINIMAX_DEFAULT_MODEL = "MiniMax-M2.7"

NVIDIA_DEFAULT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_DEFAULT_MODEL = "moonshotai/kimi-k2.5"

# 금지 문자: CJK 한자(확장A 포함), 히라가나, 가타카나, 반각 가타카나.
# 한 글자라도 있으면 재시도/스크러빙 트리거.
_FORBIDDEN_RE = re.compile(
    r"["
    r"\u3400-\u4DBF"   # CJK 확장 A
    r"\u4E00-\u9FFF"   # CJK 통합 한자
    r"\u3040-\u309F"   # 히라가나
    r"\u30A0-\u30FF"   # 가타카나
    r"\uFF66-\uFF9F"   # 반각 가타카나
    r"]"
)

_WS_COLLAPSE_RE = re.compile(r"\s+")
_WS_BEFORE_PUNCT_RE = re.compile(r"\s+([,.!?;:])")


def _has_forbidden(payload: FeedbackPayload) -> bool:
    combined = " ".join([payload.empathy, payload.reframe, payload.question, *payload.distortions])
    return bool(_FORBIDDEN_RE.search(combined))


def _scrub_forbidden(s: str) -> str:
    """최후 방어선: 남은 금지 문자를 공백으로 대체하고 공백·구두점 정리."""
    if not isinstance(s, str):
        return s
    if not _FORBIDDEN_RE.search(s):
        return s
    s = _FORBIDDEN_RE.sub(" ", s)
    s = _WS_COLLAPSE_RE.sub(" ", s).strip()
    s = _WS_BEFORE_PUNCT_RE.sub(r"\1", s)
    return s


def _scrub_payload(p: FeedbackPayload) -> FeedbackPayload:
    return FeedbackPayload(
        mode=p.mode,
        empathy=_scrub_forbidden(p.empathy),
        distortions=[_scrub_forbidden(d) for d in p.distortions],
        reframe=_scrub_forbidden(p.reframe),
        question=_scrub_forbidden(p.question),
        message=p.message,
        hotlines=p.hotlines,
    )


def _build_user_block(entry: DiaryEntry) -> str:
    return (
        f"[상황]\n{entry.situation}\n\n"
        f"[그때 떠오른 생각]\n{entry.thought}\n\n"
        f"[스스로 시도한 재구성]\n{entry.reframe or '(작성하지 않음)'}\n\n"
        f"[직무/연차]\n{entry.job_role or '(미입력)'}"
    )


def _parse_feedback_json(content: str) -> FeedbackPayload:
    data = json.loads(_extract_json(content))
    return FeedbackPayload(
        mode="feedback",
        empathy=_normalize_text(data.get("empathy", "")),
        distortions=[_normalize_text(d) for d in (data.get("distortions") or [])],
        reframe=_normalize_text(data.get("reframe", "")),
        question=_normalize_text(data.get("question", "")),
    )


async def _call_minimax(entry: DiaryEntry) -> Optional[FeedbackPayload]:
    """MiniMax 1차 시도. 성공 시 FeedbackPayload, 실패 시 None(→ NVIDIA로 폴백)."""
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        logger.warning("MiniMax skip: MINIMAX_API_KEY is not configured")
        return None

    base_url = os.environ.get("MINIMAX_BASE_URL", MINIMAX_DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("MINIMAX_MODEL", MINIMAX_DEFAULT_MODEL)

    user_block = _build_user_block(entry)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_block},
        ],
        "max_tokens": 4096,
        "temperature": 0.3,
        "top_p": 0.9,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async def _one_shot(body_payload: dict) -> Optional[FeedbackPayload]:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base_url}/text/chatcompletion_v2",
                headers=headers,
                json=body_payload,
            )
        response.raise_for_status()
        body = response.json()
        base_resp = body.get("base_resp") or {}
        if base_resp.get("status_code", 0) not in (0, None):
            logger.warning("MiniMax fallback: base_resp not ok: %s", base_resp)
            return None
        choices = body.get("choices") or []
        if not choices:
            logger.warning("MiniMax fallback: empty choices in response")
            return None
        raw_content = choices[0].get("message", {}).get("content", "")
        if isinstance(raw_content, list):
            content = "\n".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in raw_content
            )
        else:
            content = str(raw_content or "")
        return _parse_feedback_json(content)

    try:
        result = await _one_shot(payload)
        if result is None:
            return None

        # 가드레일 1·2: 금지 문자가 있으면 이전 응답을 assistant 메시지로 인용하고
        # 온도를 낮추며 최대 2회까지 재시도.
        for attempt in range(1, 3):
            if not _has_forbidden(result):
                break
            prev_bad = json.dumps(
                {
                    "empathy": result.empathy,
                    "distortions": result.distortions,
                    "reframe": result.reframe,
                    "question": result.question,
                },
                ensure_ascii=False,
            )
            retry_payload = dict(payload)
            retry_payload["temperature"] = max(0.05, 0.25 - 0.1 * attempt)
            retry_payload["messages"] = list(payload["messages"]) + [
                {"role": "assistant", "content": prev_bad},
                {
                    "role": "user",
                    "content": (
                        f"[재시도 {attempt}] 직전 JSON 응답에 한자·일본어 문자가 섞여 있습니다. "
                        "같은 조언을 **순수 한글만** 사용하여 동일 JSON 스키마로 다시 출력하세요. "
                        "금지: 한자·히라가나·가타카나·전각 구두점. "
                        "반드시 한국어 한글로 치환 예) 不行→안 된다, 过程→과정, "
                        "开始→시작, 职场→직장, 踏入→들어서다, 这个人→이 사람."
                    ),
                },
            ]
            retried = await _one_shot(retry_payload)
            if retried is not None:
                result = retried

        # 재시도 후에도 금지 문자가 남으면 MiniMax 결과 버리고 NVIDIA로 폴백.
        if _has_forbidden(result):
            logger.warning("MiniMax response still had forbidden characters after retries; falling back to NVIDIA")
            return None
        return result
    except Exception:
        logger.exception(
            "MiniMax call failed for situation=%r thought=%r job_role=%r",
            entry.situation[:120],
            entry.thought[:120],
            (entry.job_role or "")[:60],
        )
        return None


async def _call_nvidia(entry: DiaryEntry) -> Optional[FeedbackPayload]:
    """NVIDIA(moonshotai/kimi-k2.5) 2차 폴백. OpenAI 호환 엔드포인트."""
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        logger.warning("NVIDIA skip: NVIDIA_API_KEY is not configured")
        return None

    url = os.environ.get("NVIDIA_BASE_URL", NVIDIA_DEFAULT_URL).strip()
    model = os.environ.get("NVIDIA_BASE_MODEL", NVIDIA_DEFAULT_MODEL)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_block(entry)},
        ],
        "max_tokens": 4096,
        "temperature": 0.3,
        "top_p": 0.9,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            logger.warning("NVIDIA fallback: empty choices in response")
            return None
        raw_content = choices[0].get("message", {}).get("content", "")
        content = str(raw_content or "")
        result = _parse_feedback_json(content)
        # NVIDIA 응답에도 한자·가나 가드레일 적용. 잔류 문자는 서버에서 직접 제거.
        if _has_forbidden(result):
            logger.info("NVIDIA response contained forbidden characters; scrubbing")
            result = _scrub_payload(result)
        return result
    except Exception:
        logger.exception(
            "NVIDIA call failed for situation=%r thought=%r job_role=%r",
            entry.situation[:120],
            entry.thought[:120],
            (entry.job_role or "")[:60],
        )
        return None


def _extract_json(text: str) -> str:
    """LLM이 <think>…</think>, 코드펜스, 주변 텍스트를 붙여도 JSON 블록을 추출."""
    text = text.strip()
    # M2.7 같은 reasoning 모델이 남기는 <think>…</think> 블록 제거
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


_PUNCT_NORMALIZE = {
    "،": ",",   # 아라비아 쉼표
    "，": ",",   # 전각 쉼표
    "。": ".",   # 전각 마침표
    "！": "!",   # 전각 느낌표
    "？": "?",   # 전각 물음표
    "：": ":",   # 전각 콜론
    "；": ";",   # 전각 세미콜론
    "「": "'",   # 일본 괄호
    "」": "'",
    "『": "\"",
    "』": "\"",
}


def _normalize_text(s: str) -> str:
    """LLM 응답에서 특수 구두점만 한국어 표준 구두점으로 정규화.
    한자·가타카나 치환은 의미 손상 위험이 있어 프롬프트로만 통제."""
    if not isinstance(s, str):
        return s
    for old, new in _PUNCT_NORMALIZE.items():
        s = s.replace(old, new)
    return s


def _contains_crisis(entry: DiaryEntry) -> bool:
    blob = f"{entry.situation}\n{entry.thought}\n{entry.reframe}"
    return bool(_CRISIS_RE.search(blob))


# ---------- 라우트 ----------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "primary": {
            "configured": bool(os.environ.get("MINIMAX_API_KEY")),
            "model": os.environ.get("MINIMAX_MODEL", MINIMAX_DEFAULT_MODEL),
        },
        "fallback": {
            "configured": bool(os.environ.get("NVIDIA_API_KEY")),
            "model": os.environ.get("NVIDIA_BASE_MODEL", NVIDIA_DEFAULT_MODEL),
        },
    }


@app.post("/api/analyze")
async def analyze(entry: DiaryEntry) -> JSONResponse:
    if _contains_crisis(entry):
        return JSONResponse(CRISIS_RESPONSE)

    # 1차: MiniMax → 2차: NVIDIA kimi → 최후: 템플릿
    result = await _call_minimax(entry)
    if result is None:
        logger.info("Primary provider failed; trying NVIDIA fallback")
        result = await _call_nvidia(entry)
    if result is None:
        logger.info("All providers failed; returning template fallback")
        result = _fallback_feedback(entry)
    return JSONResponse(result.model_dump())
