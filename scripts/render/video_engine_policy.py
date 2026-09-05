#!/usr/bin/env python3
"""영상(i2v/t2v) 생성 엔진 정책 — 프로젝트 단일 관문.

★불변 규칙 (2026-09-06 사용자 지시 — 협상·폴백·자동전환 대상이 아니다)

    영상은 PJN(`PJN_API_KEY`, 로컬 5090 MiniMax H3)으로만 만든다.
    **`GEMINI_API_KEY`로는 어떤 경우에도 영상을 만들지 않는다.**

왜 코드에 박는가: 엔진 선택이 settings.json · CLI 플래그 · 함수 직접 호출 세 군데로
갈라져 있어서 문서에만 적어 두면 어느 한 경로로 새어 나간다. 그래서 세 겹으로 막는다.

    1겹 — 엔진 이름:  assert_video_engine()      "gemini"라는 값 자체를 거부
    2겹 — 함수 진입:  assert_no_gemini_video()   veo_gemini() 첫 줄에서 즉시 사망
    3겹 — 키 모양:    assert_video_key()         Google 키(AIza…)가 영상 호출에 닿으면 사망

3겹이 핵심이다. 엔진 이름을 우회하거나 새 코드에서 함수를 직접 불러도,
Google API 키는 접두사로 정체가 드러나므로 영상 생성 직전에 반드시 걸린다.

★적용 대상이 아닌 것 — **이미지** 생성(`scripts/image/generate_image.py`).
  Gemini 키를 이미지에 쓰는 것은 정상이고 계속 쓴다. 이 모듈은 영상만 본다.
"""

# 영상 생성에 허용된 엔진. 새 엔진을 늘릴 때만 손댄다.
ALLOWED_VIDEO_ENGINES = ("pjn", "flow")

# 영구 차단 엔진 → 차단 사유. 여기서 빼는 것은 위 불변 규칙을 깨는 일이다.
BANNED_VIDEO_ENGINES = {
    "gemini": "Gemini API(GEMINI_API_KEY)로 영상 생성 — 이 프로젝트에서 영구 금지",
}

DEFAULT_VIDEO_ENGINE = "pjn"

# Google API 키 접두사. Gemini/Google Cloud 키는 전부 이것으로 시작한다.
_GOOGLE_KEY_PREFIX = "AIza"

_HOWTO = (
    "영상은 PJN으로만 만듭니다.\n"
    "  · .env 에 PJN_API_KEY=... 를 두고\n"
    "  · settings.json image.veo.engine = \"pjn\" (또는 --engine pjn)\n"
    "GEMINI_API_KEY 는 이미지 생성(scripts/image/generate_image.py) 전용입니다."
)


class VideoEnginePolicyError(RuntimeError):
    """영상 생성이 금지된 엔진/키로 시도됐다. 잡아서 폴백하지 말 것 — 그러면 규칙이 무의미해진다."""


def assert_video_engine(engine, where="video"):
    """1겹. 엔진 이름을 검사한다. 통과하면 정규화된 엔진 이름을 돌려준다."""
    name = (engine or DEFAULT_VIDEO_ENGINE).strip().lower()
    if name in BANNED_VIDEO_ENGINES:
        raise VideoEnginePolicyError(
            f"[{where}] 영상 엔진 '{name}' 는 이 프로젝트에서 금지되어 있습니다.\n"
            f"  사유: {BANNED_VIDEO_ENGINES[name]}\n{_HOWTO}"
        )
    if name not in ALLOWED_VIDEO_ENGINES:
        raise VideoEnginePolicyError(
            f"[{where}] 알 수 없는 영상 엔진 '{name}'. "
            f"허용: {', '.join(ALLOWED_VIDEO_ENGINES)}\n{_HOWTO}"
        )
    return name


def assert_no_gemini_video(where="video"):
    """2겹. Gemini 영상 백엔드 함수 진입점에서 무조건 부른다. 정상 반환하지 않는다."""
    raise VideoEnginePolicyError(
        f"[{where}] Gemini API 영상 생성은 이 프로젝트에서 영구 차단되어 있습니다.\n"
        f"  이 코드 경로는 호출되면 안 됩니다 — 호출한 쪽을 pjn 으로 고치세요.\n{_HOWTO}"
    )


def assert_video_key(key, engine, where="video"):
    """3겹. 영상 생성 직전, 넘어온 API 키가 Google 키가 아닌지 본다.

    엔진 이름 검사를 어떤 식으로 우회했든 여기서 걸린다 — 영상 백엔드에 Google 키가
    닿는 것 자체가 규칙 위반이기 때문이다."""
    if key and str(key).startswith(_GOOGLE_KEY_PREFIX):
        raise VideoEnginePolicyError(
            f"[{where}] Google API 키(`{_GOOGLE_KEY_PREFIX}…`)가 영상 생성 경로(engine={engine})에 "
            f"전달됐습니다. 영상에 Gemini 키를 쓰는 것은 영구 금지입니다.\n{_HOWTO}"
        )
    return key


def video_engine_from_settings(veo_cfg, cli_engine=None, where="video"):
    """settings.json image.veo 딕셔너리 + CLI 플래그에서 엔진을 정한다(정책 적용 포함).

    settings 에 gemini 가 박혀 있어도 조용히 넘어가지 않는다 — 그 설정 자체가 오류다."""
    return assert_video_engine(
        cli_engine or (veo_cfg or {}).get("engine") or DEFAULT_VIDEO_ENGINE, where
    )
