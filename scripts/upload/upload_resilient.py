"""
약한 WiFi/불안정 회선용 튼튼한 유튜브 업로드 (재시도 강화 + 작은 청크 + 긴 타임아웃).

기존 upload.py가 회선 불안정으로 10회 재시도 후 포기하는 문제 대응:
- 청크 4MB (약한 신호에서 한 청크가 타임아웃 전에 넘어갈 확률↑)
- httplib2 타임아웃 300초 (느린 응답도 기다림)
- 연속 실패 허용 최대 200회 + 백오프 (청크 하나라도 성공하면 카운터 리셋 → 밤새 블립 견딤)
- 제목/설명/태그/썸네일은 meta.txt에서 자동 (upload.py와 동일 규약)

사용:
  PYTHONUTF8=1 PYTHONPATH=scripts/_pytools python3 scripts/upload/upload_resilient.py \
    --channel yadam --project 260715_2057_삼월이 \
    --video "D:\\6.Youtube부업\\1.야담\\Final\\260715_2057_삼월이_veohook_v2.mp4" \
    --privacy private
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httplib2
import google.auth.transport.requests
import google_auth_httplib2
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_resolver import resolve_project_dir

CHUNK = 4 * 1024 * 1024        # 4MB — 약한 신호 친화
HTTP_TIMEOUT = 300             # 초 — 느린 응답 대기
MAX_CONSEC_FAIL = 200         # 연속 실패 허용 (성공 시 리셋)


def load_credentials(channel: str) -> Credentials:
    tok = ROOT / "channels" / channel / "config" / "youtube-api" / "token.json"
    if not tok.exists():
        print(f"[ERROR] 토큰 없음: {tok}  → 먼저 auth.py 실행")
        sys.exit(1)
    d = json.loads(tok.read_text(encoding="utf-8"))
    creds = Credentials(
        token=d["token"], refresh_token=d["refresh_token"], token_uri=d["token_uri"],
        client_id=d["client_id"], client_secret=d["client_secret"], scopes=d.get("scopes"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(google.auth.transport.requests.Request())
        d["token"] = creds.token
        tok.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        print("[INFO] 토큰 갱신")
    return creds


def meta_from_txt(project_dir: Path) -> dict:
    p = project_dir / "meta.txt"
    m = {"title": "", "description": "", "tags": []}
    if not p.exists():
        return m
    content = p.read_text(encoding="utf-8")
    sections = {}
    for g in re.finditer(r"\[([^\]]+)\]\n(.*?)(?=\n\[|\Z)", content, re.DOTALL):
        sections[g.group(1).strip()] = g.group(2).strip()
    if sections.get("제목"):
        m["title"] = sections["제목"].splitlines()[0].strip()
    desc = sections.get("설명문", "")
    m["description"] = desc
    m["tags"] = re.findall(r"#([\w가-힣]+)", desc)
    return m


def build_youtube(creds):
    # 기본 http(googleapiclient 내부 — resumable 리다이렉트 처리 정상)를 쓰되,
    # 소켓 기본 타임아웃만 늘려 약한 회선에서 조기 read timeout을 방지한다.
    # (커스텀 httplib2.Http를 넘기면 resumable 세션 초기화에서 RedirectMissingLocation 발생)
    import socket
    socket.setdefaulttimeout(HTTP_TIMEOUT)
    return build("youtube", "v3", credentials=creds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--privacy", default="private", choices=("private", "unlisted", "public"))
    ap.add_argument("--thumbnail", default="")
    args = ap.parse_args()

    try:
        project_dir = resolve_project_dir(args.project, args.channel)
    except Exception as e:
        print(f"[ERROR] 프로젝트 경로 확인 실패: {e}")
        sys.exit(1)
    if not project_dir.exists():
        print(f"[ERROR] 프로젝트 없음: {project_dir}")
        sys.exit(1)

    video = Path(args.video)
    if not video.exists():
        print(f"[ERROR] 영상 파일 없음: {video}")
        sys.exit(1)

    meta = meta_from_txt(project_dir)
    if not meta["title"]:
        print("[ERROR] meta.txt [제목] 없음")
        sys.exit(1)

    # 썸네일 자동 탐색
    thumb = Path(args.thumbnail) if args.thumbnail else None
    if not thumb:
        td = project_dir / "output" / "thumbnails"
        if td.exists():
            cands = sorted(td.glob("*.png")) + sorted(td.glob("*.jpg"))
            if cands:
                thumb = cands[0]

    creds = load_credentials(args.channel)
    youtube = build_youtube(creds)

    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": "22",
            "defaultLanguage": "ko",
            "defaultAudioLanguage": "ko",
        },
        "status": {
            "privacyStatus": args.privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": False,
        },
    }

    size_mb = video.stat().st_size / 1024 / 1024
    print(f"[INFO] 영상: {video.name} ({size_mb:.0f} MB)")
    print(f"[INFO] 제목: {meta['title']}")
    print(f"[INFO] 태그 {len(meta['tags'])}개 · 공개상태 {args.privacy}")
    print(f"[INFO] 청크 {CHUNK//1024//1024}MB · 타임아웃 {HTTP_TIMEOUT}s · 연속실패허용 {MAX_CONSEC_FAIL}회")
    print()

    media = MediaFileUpload(str(video), mimetype="video/mp4", resumable=True, chunksize=CHUNK)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    consec_fail = 0
    last_pct = -1
    while response is None:
        try:
            status, response = request.next_chunk(num_retries=3)
            consec_fail = 0
            if status:
                pct = int(status.progress() * 100)
                if pct != last_pct:
                    print(f"  업로드 중... {pct}%")
                    last_pct = pct
        except Exception as e:
            consec_fail += 1
            if consec_fail > MAX_CONSEC_FAIL:
                print(f"[FATAL] 연속 {MAX_CONSEC_FAIL}회 실패 — 회선이 완전히 끊긴 듯. 나중에 다시 실행하세요.")
                raise
            wait = min(2 ** min(consec_fail, 6) * 2, 60)
            print(f"  [WARN] {type(e).__name__}: {str(e)[:80]}  → {wait}s 후 재시도 ({consec_fail}/{MAX_CONSEC_FAIL})")
            time.sleep(wait)

    vid = response["id"]
    print()
    print(f"[OK] ✅ 업로드 완료! Video ID: {vid}")
    print(f"[OK] 스튜디오: https://studio.youtube.com/video/{vid}/edit")

    # 썸네일 (실패해도 영상은 유지 — 인증/전파 문제면 나중에 재시도)
    if thumb and thumb.exists():
        for attempt in range(5):
            try:
                youtube.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb))).execute()
                print(f"[OK] ✅ 썸네일 설정: {thumb.name}")
                break
            except Exception as e:
                print(f"  [WARN] 썸네일 실패({attempt+1}/5): {str(e)[:90]}")
                time.sleep(5)
        else:
            print("[WARN] 썸네일 자동 설정 실패 — 스튜디오에서 수동으로 넣으세요(채널 전화인증 필요).")

    result = {
        "video_id": vid,
        "title": meta["title"],
        "url": f"https://youtu.be/{vid}",
        "studio_url": f"https://studio.youtube.com/video/{vid}/edit",
        "privacy": args.privacy,
    }
    (project_dir / "output" / "upload_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False)
    )
    print(f"[OK] 결과 저장: output/upload_result.json")


if __name__ == "__main__":
    main()
