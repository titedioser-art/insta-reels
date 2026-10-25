"""인스타 카드뉴스 릴스 자동 게시 - 확인 화면 (Streamlit)

실행: 실행.bat 더블클릭 (또는 streamlit run app.py)
"""
import json
import os
from datetime import datetime

from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))

import hmac  # noqa: E402

import streamlit as st  # noqa: E402

# 인터넷 서버(Streamlit Cloud)에서는 키를 Secrets에 넣는다 → 환경변수로 옮겨 쓰기
ON_CLOUD = False
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float)):
            os.environ.setdefault(_k, str(_v))
            ON_CLOUD = True
except Exception:
    pass

import content  # noqa: E402
import instagram  # noqa: E402
import video  # noqa: E402

WORK = os.path.join(BASE, "작업중")
OUT = os.path.join(BASE, "게시기록")
os.makedirs(WORK, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

st.set_page_config(page_title="인스타 릴스 자동 게시", page_icon="📮", layout="wide")
ss = st.session_state

# ---------------- 비밀번호 잠금 ----------------
_pw = os.getenv("APP_PASSWORD", "")
if ON_CLOUD and not _pw:
    st.error("Secrets에 APP_PASSWORD를 꼭 넣어주세요. 비밀번호 없이는 아무나 내 계정에 글을 올릴 수 있어요.")
    st.stop()
if _pw and not ss.get("authed"):
    st.title("📮 인스타 릴스 자동 게시")
    with st.form("login"):
        typed = st.text_input("비밀번호", type="password")
        if st.form_submit_button("들어가기", type="primary"):
            if hmac.compare_digest(typed, _pw):
                ss.authed = True
                st.rerun()
            else:
                st.error("비밀번호가 틀렸어요.")
    st.stop()

ss.setdefault("media", [])       # 저장된 파일 경로 목록 (순서대로)
ss.setdefault("draft", None)     # {"slides", "caption", "hashtags"}
ss.setdefault("video", None)     # 만들어진 영상 경로
ss.setdefault("posted", None)    # 게시 결과 링크

# ---------------- 사이드바: 설정 ----------------
with st.sidebar:
    st.header("⚙️ 설정")
    if st.button("인스타 계정 연결 확인", width="stretch"):
        try:
            info = instagram.me()
            st.success(f"연결됨: @{info.get('username')}")
        except Exception as e:
            st.error(str(e))
    if ON_CLOUD and instagram.NEW_TOKEN.get("token"):
        with st.expander("🔑 새 인스타 토큰이 발급됐어요"):
            st.caption("Streamlit 앱 설정 → Secrets 의 IG_ACCESS_TOKEN 값을 아래 것으로 바꿔두면 "
                       "두 달 뒤에도 끊기지 않아요.")
            st.code(instagram.NEW_TOKEN["token"], wrap_lines=True)
    handle = st.text_input("영상에 넣을 계정명 (선택)", os.getenv("HANDLE", ""),
                           placeholder="@my_account")
    secs = st.slider("장면당 기본 시간(초)", 2.0, 6.0, 3.5, 0.5)
    accent = st.color_picker("포인트 색", "#FFD84D")
    bgm = st.file_uploader("배경음악 (선택, mp3)", type=["mp3", "m4a", "wav"])
    st.caption("배경음악은 저작권 걱정 없는 음원만 써주세요.")
    st.divider()
    if st.button("🔄 처음부터 새로 만들기", width="stretch"):
        for k in ("media", "draft", "video", "posted"):
            ss[k] = [] if k == "media" else None
        st.rerun()

st.title("📮 인스타 카드뉴스 릴스 만들기")

# ---------------- 1단계: 재료 ----------------
st.subheader("1. 주제와 사진·영상")
c1, c2 = st.columns([2, 1])
with c1:
    topic = st.text_input("주제", placeholder="예: 겨울철 전기요금 아끼는 5가지 방법")
    extra = st.text_input("추가 요청 (선택)", placeholder="예: 20~30대 자취생 대상, 마지막에 저장 유도")
with c2:
    tone = st.selectbox("말투", ["친근하고 깔끔하게", "전문적이고 신뢰감 있게",
                                "짧고 임팩트 있게", "유쾌하고 가볍게"])

files = st.file_uploader("사진·영상 (올린 순서대로 장면이 돼요, 3~10개 추천)",
                         type=["jpg", "jpeg", "png", "webp", "heic", "heif", "mp4", "mov", "m4v"],
                         accept_multiple_files=True)

if files:
    # 같은 파일 묶음이면 다시 저장하지 않음
    sig = [f.name + str(f.size) for f in files]
    if ss.get("media_sig") != sig:
        stamp = datetime.now().strftime("%H%M%S")
        paths = []
        for i, f in enumerate(files):
            p = os.path.join(WORK, f"{stamp}_{i:02d}_{f.name}")
            with open(p, "wb") as out:
                out.write(f.getbuffer())
            if p.lower().endswith((".heic", ".heif")):  # 아이폰 사진 → JPG로 변환
                p = video.heic_to_jpg(p)
            paths.append(p)
        ss.media, ss.media_sig, ss.draft, ss.video, ss.posted = paths, sig, None, None, None

if ss.media:
    st.caption("장면 순서를 바꾸려면 아래 번호를 고쳐주세요.")
    cols = st.columns(min(len(ss.media), 6))
    orders = []
    for i, p in enumerate(ss.media):
        with cols[i % len(cols)]:
            if video.ffmpeg_util.is_video(p):
                st.video(p)
            else:
                st.image(p, width="stretch")
            orders.append(st.number_input("순서", 1, len(ss.media), i + 1, key=f"ord{i}_{p}"))
    if st.button("순서 적용"):
        ss.media = [p for _, p in sorted(zip(orders, ss.media), key=lambda x: x[0])]
        ss.draft, ss.video = None, None
        st.rerun()

    if st.button("✨ AI로 문구·캡션 만들기", type="primary", disabled=not topic):
        with st.spinner("Claude가 카드뉴스 문구를 쓰는 중..."):
            try:
                ss.draft = content.generate(topic, ss.media, tone, extra)
                ss.video, ss.posted = None, None
            except Exception as e:
                st.error(str(e))
    if not topic:
        st.caption("주제를 입력하면 버튼이 켜져요.")

# ---------------- 2단계: 문구 확인·수정 ----------------
if ss.draft:
    st.divider()
    st.subheader("2. 문구 확인·수정")
    for i, p in enumerate(ss.media):
        s = ss.draft["slides"][i]
        a, b = st.columns([1, 4])
        with a:
            if video.ffmpeg_util.is_video(p):
                st.caption(f"장면 {i + 1} (영상)")
            else:
                st.image(p, width=120)
        with b:
            s["title"] = st.text_input(f"장면 {i + 1} 제목", s["title"], key=f"t{i}")
            s["body"] = st.text_area(f"장면 {i + 1} 본문", s["body"], key=f"b{i}", height=70)
    ss.draft["caption"] = st.text_area("캡션", ss.draft["caption"], height=160)
    tags = st.text_area("해시태그", " ".join(ss.draft["hashtags"]), height=70)
    ss.draft["hashtags"] = [t for t in tags.split() if t.startswith("#")]

    if st.button("🎬 영상 만들기", type="primary"):
        bgm_path = None
        if bgm:
            bgm_path = os.path.join(WORK, "bgm_" + bgm.name)
            with open(bgm_path, "wb") as f:
                f.write(bgm.getbuffer())
        out = os.path.join(WORK, f"reel_{datetime.now():%Y%m%d_%H%M%S}.mp4")
        bar = st.progress(0.0, "영상 만드는 중...")
        try:
            video.make_reel(ss.media, ss.draft["slides"], out, secs, bgm_path, accent,
                            handle, progress=lambda v, m: bar.progress(v, m))
            ss.video, ss.posted = out, None
        except Exception as e:
            st.error(str(e))

# ---------------- 3단계: 미리보기 → 확정 → 게시 ----------------
if ss.video and os.path.exists(ss.video):
    st.divider()
    st.subheader("3. 미리보기 후 확정")
    v, info = st.columns([1, 1])
    with v:
        st.video(ss.video)
    full_caption = ss.draft["caption"].strip() + "\n\n" + " ".join(ss.draft["hashtags"])
    with info:
        st.text_area("올라갈 캡션", full_caption, height=260, disabled=True)
        with open(ss.video, "rb") as f:
            st.download_button("영상 파일 저장", f, file_name=os.path.basename(ss.video))
        st.caption("문구를 고쳤다면 위에서 '영상 만들기'를 다시 눌러주세요.")

        if ss.posted is not None:
            st.success("인스타그램에 게시됐어요! 🎉")
            if ss.posted:
                st.link_button("게시물 보러 가기", ss.posted)
        else:
            ok = st.checkbox("영상과 캡션을 확인했어요")
            if st.button("✅ 확정하고 인스타에 올리기", type="primary", disabled=not ok):
                status = st.status("게시 진행 중...", expanded=True)
                try:
                    link = instagram.post_reel(ss.video, full_caption, log=status.write)
                    status.update(label="게시 완료", state="complete")
                    ss.posted = link
                    # 기록 남기기
                    folder = os.path.join(OUT, datetime.now().strftime("%Y%m%d_%H%M%S"))
                    os.makedirs(folder, exist_ok=True)
                    os.replace(ss.video, os.path.join(folder, "reel.mp4"))
                    ss.video = os.path.join(folder, "reel.mp4")
                    with open(os.path.join(folder, "내용.json"), "w", encoding="utf-8") as f:
                        json.dump({"topic": topic, "link": link, **ss.draft}, f,
                                  ensure_ascii=False, indent=2)
                    st.rerun()
                except Exception as e:
                    status.update(label="게시 실패", state="error")
                    st.error(str(e))
