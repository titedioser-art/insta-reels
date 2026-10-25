"""카드뉴스 문구 / 캡션 / 해시태그를 Claude API로 생성하는 모듈."""
import base64
import io
import json
import os
import re
import subprocess

from PIL import Image

import ffmpeg_util

SYSTEM_PROMPT = """너는 한국어 인스타그램 정보성 카드뉴스(릴스) 전문 에디터야.
사용자가 준 주제와 사진/영상 장면을 보고, 장면 수와 똑같은 개수의 슬라이드 문구를 만든다.

규칙:
- 첫 슬라이드는 스크롤을 멈추게 하는 '후킹' 제목(질문형/숫자형 추천).
- 중간 슬라이드는 핵심 정보 하나씩. 제목은 15자 이내, 본문은 2줄 이내(한 줄 18자 정도).
- 마지막 슬라이드는 저장/팔로우를 유도하는 마무리.
- 장면(사진) 내용과 어울리는 순서와 문구로 배치한다.
- 캡션은 3~6문장, 줄바꿈과 이모지를 적당히 사용. 정보 요약 + 행동 유도.
- 해시태그는 10~15개, 너무 일반적인 것과 구체적인 것을 섞는다.
- 사실이 불확실한 수치나 주장은 지어내지 않는다.

반드시 아래 JSON 형식으로만 답한다. 다른 말은 하지 않는다.
{"slides":[{"title":"...","body":"..."}],"caption":"...","hashtags":["#...","#..."]}"""


def _thumb_b64(path: str, max_side: int = 768) -> str | None:
    """사진이면 그대로, 영상이면 첫 장면을 뽑아서 작은 JPEG(base64)로 만든다."""
    try:
        if ffmpeg_util.is_video(path):
            raw = subprocess.run(
                [ffmpeg_util.ffmpeg_exe(), "-v", "error", "-ss", "0.5", "-i", path,
                 "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                capture_output=True, check=True,
            ).stdout
            img = Image.open(io.BytesIO(raw))
        else:
            img = Image.open(path)
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("AI 응답에서 JSON을 찾지 못했어요:\n" + text[:500])
    return json.loads(m.group(0))


def generate(topic: str, media_paths: list[str], tone: str = "친근하고 깔끔하게",
             extra: str = "") -> dict:
    """주제 + 장면 목록 → {"slides":[...], "caption":str, "hashtags":[...]}"""
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env 파일에 ANTHROPIC_API_KEY를 넣어주세요.")
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5").strip()
    client = anthropic.Anthropic(api_key=api_key)

    n = len(media_paths)
    content: list[dict] = []
    for i, p in enumerate(media_paths, 1):
        content.append({"type": "text", "text": f"[장면 {i}]"})
        b64 = _thumb_b64(p)
        if b64:
            content.append({"type": "image",
                            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": (
        f"주제: {topic}\n말투: {tone}\n장면 수: {n}개 (슬라이드도 정확히 {n}개)\n"
        + (f"추가 요청: {extra}\n" if extra else "")
    )})

    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = _extract_json(text)

    slides = data.get("slides", [])[:n]
    while len(slides) < n:  # 개수가 모자라면 빈 슬라이드로 채움
        slides.append({"title": "", "body": ""})
    tags = [t if t.startswith("#") else "#" + t for t in data.get("hashtags", [])]
    return {"slides": slides, "caption": data.get("caption", ""), "hashtags": tags}
