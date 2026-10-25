"""Instagram API(인스타그램 로그인 방식)로 릴스를 게시하는 모듈.

흐름: 컨테이너 만들기 → 영상 업로드 → 처리 완료 기다리기 → 게시
"""
import functools
import http.server
import os
import re
import shutil
import socketserver
import subprocess
import threading
import time
from datetime import datetime, timedelta

import requests
from dotenv import set_key

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
API_VER = os.getenv("IG_API_VERSION", "v23.0")
GRAPH = f"https://graph.instagram.com/{API_VER}"


NEW_TOKEN: dict = {}  # 인터넷 서버에서 토큰이 새로 바뀌면 화면에 보여주려고 보관


class InstagramError(RuntimeError):
    pass


def _token() -> str:
    t = os.getenv("IG_ACCESS_TOKEN", "").strip()
    if not t:
        raise InstagramError(".env 파일에 IG_ACCESS_TOKEN을 넣어주세요. (설정가이드 참고)")
    return t


def _check(r: requests.Response) -> dict:
    try:
        data = r.json()
    except Exception:
        raise InstagramError(f"응답을 읽지 못했어요 (HTTP {r.status_code}): {r.text[:300]}")
    if r.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        msg = err.get("error_user_msg") or err.get("message") or str(data)
        raise InstagramError(f"인스타그램 오류: {msg}")
    return data


# ---------- 계정 / 토큰 ----------
def me() -> dict:
    """연결된 계정 정보: {'user_id':..., 'username':...}"""
    r = requests.get(f"{GRAPH}/me", params={"fields": "user_id,username",
                                            "access_token": _token()}, timeout=20)
    return _check(r)


def refresh_token_if_needed(force: bool = False) -> str | None:
    """장기 토큰(60일)은 쓰다 보면 만료되니, 7일마다 자동으로 연장한다."""
    last = os.getenv("IG_TOKEN_REFRESHED_AT", "")
    try:
        due = force or not last or datetime.now() - datetime.fromisoformat(last) > timedelta(days=7)
    except ValueError:
        due = True
    if not due:
        return None
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": _token()},
                     timeout=20)
    data = _check(r)
    new = data["access_token"]
    if new != os.getenv("IG_ACCESS_TOKEN"):
        NEW_TOKEN["token"] = new
    now = datetime.now().isoformat(timespec="seconds")
    os.environ["IG_ACCESS_TOKEN"] = new
    os.environ["IG_TOKEN_REFRESHED_AT"] = now
    if os.path.exists(ENV_PATH):
        set_key(ENV_PATH, "IG_ACCESS_TOKEN", new)
        set_key(ENV_PATH, "IG_TOKEN_REFRESHED_AT", now)
    days = int(data.get("expires_in", 0)) // 86400
    return f"토큰을 연장했어요 (앞으로 약 {days}일 유효)"


# ---------- 업로드 방식 1: 파일을 바로 올리기 ----------
def _create_resumable(user_id: str, caption: str) -> dict:
    r = requests.post(f"{GRAPH}/{user_id}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true", "access_token": _token(),
    }, timeout=30)
    return _check(r)


def _upload_bytes(container: dict, video_path: str) -> None:
    cid = container["id"]
    uri = container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{API_VER}/{cid}"
    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        r = requests.post(uri, data=f, timeout=600, headers={
            "Authorization": f"OAuth {_token()}", "offset": "0", "file_size": str(size),
        })
    data = _check(r)
    if not data.get("success", True):
        raise InstagramError(f"업로드 실패: {data}")


# ---------- 업로드 방식 2: 임시 공개 주소 (cloudflared) ----------
class _Tunnel:
    """내 PC의 영상 파일을 잠깐 공개 주소로 열어주는 장치. 게시가 끝나면 닫는다."""

    def __init__(self, video_path: str):
        self.dir = os.path.dirname(os.path.abspath(video_path))
        self.name = os.path.basename(video_path)
        self.httpd = None
        self.proc = None

    def __enter__(self) -> str:
        exe = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared.exe")
        if not exe and os.path.exists(local):
            exe = local
        if not exe:
            raise InstagramError(
                "영상을 바로 올리는 방식이 막혀 있어요. 설정가이드의 '대체 업로드 방식'대로 "
                "cloudflared.exe를 이 폴더에 넣어주면 자동으로 우회해서 올려요.")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=self.dir)
        self.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.proc = subprocess.Popen(
            [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace")
        deadline = time.time() + 40
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line or "")
            if m:
                time.sleep(4)  # 주소가 실제로 열릴 때까지 잠깐 대기
                return f"{m.group(0)}/{requests.utils.quote(self.name)}"
        raise InstagramError("임시 공개 주소를 만들지 못했어요. 인터넷 연결을 확인해 주세요.")

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
        if self.httpd:
            self.httpd.shutdown()


class _StaticLink:
    """인터넷 서버(Streamlit Cloud)에서는 앱 자체가 공개 주소라서,
    영상을 static 폴더에 잠깐 두고 그 주소를 인스타에 알려준다."""

    def __init__(self, video_path: str, public_url: str):
        import uuid
        self.src = video_path
        self.dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        self.name = f"{uuid.uuid4().hex}.mp4"  # 추측할 수 없는 이름
        self.base = public_url.rstrip("/")

    def __enter__(self) -> str:
        os.makedirs(self.dir, exist_ok=True)
        shutil.copyfile(self.src, os.path.join(self.dir, self.name))
        return f"{self.base}/app/static/{self.name}"

    def __exit__(self, *exc):
        try:
            os.remove(os.path.join(self.dir, self.name))
        except OSError:
            pass


def _create_from_url(user_id: str, caption: str, url: str) -> dict:
    r = requests.post(f"{GRAPH}/{user_id}/media", data={
        "media_type": "REELS", "video_url": url, "caption": caption,
        "share_to_feed": "true", "access_token": _token(),
    }, timeout=30)
    return _check(r)


# ---------- 공통: 처리 대기 → 게시 ----------
def _wait_ready(cid: str, log, timeout: int = 600) -> None:
    start = time.time()
    while time.time() - start < timeout:
        r = requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code,status",
                                                   "access_token": _token()}, timeout=20)
        data = _check(r)
        code = data.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise InstagramError(f"인스타그램이 영상을 처리하지 못했어요: {data.get('status')}")
        log(f"인스타그램에서 영상 처리 중... ({int(time.time() - start)}초)")
        time.sleep(5)
    raise InstagramError("영상 처리가 너무 오래 걸려요. 잠시 후 다시 시도해 주세요.")


def _publish(user_id: str, cid: str) -> str:
    r = requests.post(f"{GRAPH}/{user_id}/media_publish",
                      data={"creation_id": cid, "access_token": _token()}, timeout=60)
    media_id = _check(r)["id"]
    try:
        r = requests.get(f"{GRAPH}/{media_id}", params={"fields": "permalink",
                                                        "access_token": _token()}, timeout=20)
        return _check(r).get("permalink", "")
    except InstagramError:
        return ""


def post_reel(video_path: str, caption: str, log=print) -> str:
    """릴스 게시. 성공하면 게시물 링크를 돌려준다."""
    msg = refresh_token_if_needed()
    if msg:
        log(msg)
    user_id = os.getenv("IG_USER_ID", "").strip() or me()["user_id"]

    log("영상 업로드 중...")
    try:
        container = _create_resumable(user_id, caption)
        _upload_bytes(container, video_path)
        _wait_ready(container["id"], log)
        cid = container["id"]
    except InstagramError as e:
        log(f"바로 올리기가 안 돼서 대체 방식으로 다시 시도해요. ({e})")
        public = os.getenv("PUBLIC_APP_URL", "").strip()
        hoster = _StaticLink(video_path, public) if public else _Tunnel(video_path)
        with hoster as url:
            container = _create_from_url(user_id, caption, url)
            _wait_ready(container["id"], log)
        cid = container["id"]

    log("게시하는 중...")
    link = _publish(user_id, cid)
    log("게시 완료!")
    return link
