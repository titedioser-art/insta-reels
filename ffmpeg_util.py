"""ffmpeg 실행 파일 찾기 + 공용 도우미."""
import os
import shutil
import subprocess

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def ffmpeg_exe() -> str:
    # 1) pip로 설치된 imageio-ffmpeg 안의 ffmpeg (Windows에서 따로 설치 필요 없음)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    # 2) 시스템에 설치된 ffmpeg
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    raise RuntimeError("ffmpeg를 찾지 못했어요. 설치.bat을 다시 실행해 주세요.")


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXT


def run(args: list[str]) -> None:
    proc = subprocess.run([ffmpeg_exe(), "-y", "-v", "error", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError("영상 처리 중 오류:\n" + proc.stderr[-1500:])
