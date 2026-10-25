"""내 사진/영상 + 카드뉴스 문구 → 인스타 릴스(1080x1920 mp4) 만들기."""
import os
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

import ffmpeg_util

W, H = 1080, 1920
FPS = 30

# ---------- 글꼴 ----------
_FONT_CANDIDATES = {
    "bold": [
        r"C:\Windows\Fonts\malgunbd.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    ],
    "regular": [
        r"C:\Windows\Fonts\malgun.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    ],
}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    custom = os.getenv("FONT_BOLD_PATH" if kind == "bold" else "FONT_PATH", "").strip()
    paths = ([custom] if custom else []) + _FONT_CANDIDATES[kind]
    for p in paths:
        if p and os.path.exists(p):
            # Noto CJK .ttc 는 1번이 한국어(KR)
            idx = 1 if p.endswith(".ttc") and "Noto" in p else 0
            return ImageFont.truetype(p, size, index=idx)
    raise RuntimeError("한글 글꼴을 찾지 못했어요. .env 에 FONT_PATH를 지정해 주세요.")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """한글 기준 줄바꿈: 띄어쓰기 단위로 넣다가 넘치면 글자 단위로 자른다."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = (cur + " " + word).strip()
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            cur = ""
            for ch in word:  # 단어 하나가 너무 길면 글자 단위
                if draw.textlength(cur + ch, font=font) > max_w and cur:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
        lines.append(cur)
    return [l for l in lines if l is not None]


def heic_to_jpg(path: str) -> str:
    """아이폰 HEIC 사진을 JPG로 바꿔 저장하고 새 경로를 돌려준다."""
    from pillow_heif import register_heif_opener
    from PIL import ImageOps
    register_heif_opener()
    out = os.path.splitext(path)[0] + ".jpg"
    ImageOps.exif_transpose(Image.open(path)).convert("RGB").save(out, "JPEG", quality=92)
    return out


# ---------- 글자 레이어(투명 PNG) ----------
def render_overlay(title: str, body: str, index: int, total: int, out_path: str,
                   accent: str = "#FFD84D", handle: str = "") -> str:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    is_cover = index == 0

    # 아래쪽(표지는 전체) 어둡게 깔아서 글자가 잘 보이게
    shade = Image.new("L", (1, H))
    for y in range(H):
        if is_cover:
            a = 90 + int(110 * (y / H))
        else:
            start = int(H * 0.45)
            a = 0 if y < start else int(215 * ((y - start) / (H - start)) ** 0.9)
        shade.putpixel((0, y), a)
    black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    black.putalpha(shade.resize((W, H)))
    img = Image.alpha_composite(img, black)
    d = ImageDraw.Draw(img)

    margin = 90
    max_w = W - margin * 2
    tf = _font("bold", 112 if is_cover else 84)
    bf = _font("regular", 52)
    t_lines = _wrap(d, title, tf, max_w) if title else []
    b_lines = _wrap(d, body, bf, max_w) if body else []
    t_lh, b_lh = int(tf.size * 1.28), int(bf.size * 1.5)
    block_h = len(t_lines) * t_lh + (40 if b_lines else 0) + len(b_lines) * b_lh

    y = (H - block_h) // 2 if is_cover else H - 260 - block_h

    # 포인트 색 막대
    d.rounded_rectangle([margin, y - 50, margin + 90, y - 36], radius=7, fill=accent)
    for line in t_lines:
        d.text((margin, y), line, font=tf, fill="white",
               stroke_width=2, stroke_fill=(0, 0, 0, 120))
        y += t_lh
    if b_lines:
        y += 40
        for line in b_lines:
            d.text((margin, y), line, font=bf, fill=(235, 235, 235))
            y += b_lh

    # 오른쪽 위 페이지 번호
    sf = _font("bold", 40)
    label = f"{index + 1}/{total}"
    lw = d.textlength(label, font=sf)
    d.rounded_rectangle([W - margin - lw - 36, 110, W - margin, 172], radius=31,
                        fill=(0, 0, 0, 110))
    d.text((W - margin - lw - 18, 116), label, font=sf, fill="white")

    if handle:
        hf = _font("regular", 38)
        d.text((margin, H - 170), handle, font=hf, fill=(255, 255, 255, 200))

    img.save(out_path)
    return out_path


# ---------- 장면 1개 → mp4 조각 ----------
def _segment(media: str, overlay_png: str, dur: float, out: str, idx: int) -> None:
    fade = "fade=t=in:st=0.15:d=0.45:alpha=1"
    if ffmpeg_util.is_video(media):
        inputs = ["-stream_loop", "-1", "-i", media]
        base = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},fps={FPS},setsar=1[bg]")
    else:
        # 사진: 살짝 크게 만든 뒤 천천히 움직이는 효과(켄 번즈)
        bw, bh = int(W * 1.12), int(H * 1.12)
        dx, dy = bw - W, bh - H
        x_expr = f"{dx}*t/{dur}" if idx % 2 == 0 else f"{dx}-{dx}*t/{dur}"
        inputs = ["-loop", "1", "-framerate", str(FPS), "-i", media]
        base = (f"[0:v]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
                f"crop={bw}:{bh},crop={W}:{H}:x='{x_expr}':y='{dy}/2',setsar=1[bg]")
    fc = f"{base};[1:v]format=rgba,{fade}[txt];[bg][txt]overlay=0:0:format=auto,format=yuv420p[v]"
    ffmpeg_util.run([
        *inputs, "-loop", "1", "-framerate", str(FPS), "-i", overlay_png,
        "-filter_complex", fc, "-map", "[v]", "-t", f"{dur:.2f}", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS), out,
    ])


def make_reel(media_paths: list[str], slides: list[dict], out_path: str,
              seconds_per_slide: float = 3.5, bgm_path: str | None = None,
              accent: str = "#FFD84D", handle: str = "", progress=None) -> str:
    """장면들을 이어 붙여 최종 릴스 영상을 만든다."""
    total = len(media_paths)
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, (m, s) in enumerate(zip(media_paths, slides)):
            ov = render_overlay(s.get("title", ""), s.get("body", ""), i, total,
                                os.path.join(tmp, f"ov{i}.png"), accent, handle)
            # 표지와 글이 많은 장면은 조금 더 길게
            text_len = len(s.get("title", "")) + len(s.get("body", ""))
            dur = seconds_per_slide + (0.5 if i == 0 else 0) + min(1.5, max(0, text_len - 40) / 30)
            part = os.path.join(tmp, f"part{i}.mp4")
            _segment(m, ov, dur, part, i)
            parts.append(part)
            if progress:
                progress((i + 1) / (total + 1), f"장면 {i + 1}/{total} 완성")

        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in parts:
                f.write(f"file '{p}'\n")
        joined = os.path.join(tmp, "joined.mp4")
        ffmpeg_util.run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])

        # 소리: 배경음악이 있으면 넣고 끝에서 서서히 줄이기, 없으면 무음 트랙
        if bgm_path:
            audio_in = ["-stream_loop", "-1", "-i", bgm_path]
        else:
            audio_in = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        total_dur = _duration(joined)
        afilter = f"afade=t=out:st={max(0, total_dur - 1.5):.2f}:d=1.5,volume=0.8"
        ffmpeg_util.run([
            "-i", joined, *audio_in, "-map", "0:v", "-map", "1:a",
            "-af", afilter, "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-t", f"{total_dur:.2f}", "-movflags", "+faststart", out_path,
        ])
    if progress:
        progress(1.0, "영상 완성")
    return out_path


def _duration(path: str) -> float:
    import subprocess, re
    p = subprocess.run([ffmpeg_util.ffmpeg_exe(), "-i", path], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr)
    if not m:
        return 10.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


if __name__ == "__main__":
    # 간단 테스트: python video.py 사진1.jpg 사진2.jpg ...
    files = sys.argv[1:]
    demo = [{"title": f"테스트 제목 {i + 1}", "body": "본문 문구가 여기에 들어갑니다"} for i in range(len(files))]
    print(make_reel(files, demo, "test_reel.mp4"))
