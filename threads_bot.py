"""
포스텔러 트위터 탭 글을 스레드에 자동 발행하는 스크립트

  python threads_bot.py post     -> 내일(또는 오늘) 날짜 글을 스레드에 발행
  python threads_bot.py refresh  -> 액세스 토큰 60일 연장

필요한 값(GitHub Secrets에 저장):
  THREADS_TOKEN      : 60일짜리 액세스 토큰
  THREADS_USER_ID    : 스레드 id 숫자
  SLACK_WEBHOOK_URL  : 슬랙 알림 주소 (없으면 알림만 생략)
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.environ["THREADS_TOKEN"]
USER_ID = os.environ["THREADS_USER_ID"]

SOURCE_URL = "https://external-api.forceteller.com/api/interactive/outsource/twitter?month=current"
API = "https://graph.threads.net/v1.0"

# 스레드용 원링크 (글 안의 원링크를 모두 이 링크로 바꿔서 발행)
THREADS_LINK = "https://forcetellerkr.onelink.me/Jfkr/2r7wxf79"

# 스레드 주제(토픽) 태그
TOPIC_TAG = "사주"


def call(url, params=None, method="GET"):
    """주소에 요청을 보내고 결과를 받아오는 도우미"""
    data = None
    if params:
        encoded = urllib.parse.urlencode(params)
        if method == "GET":
            url = f"{url}?{encoded}"
        else:
            data = encoded.encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        print("요청 실패:", e.code, e.read().decode())
        raise


def set_result(msg):
    """슬랙 알림에 보낼 문구를 남겨두기"""
    print(msg)
    path = os.environ.get("GITHUB_ENV")
    if path:
        with open(path, "a") as f:
            f.write(f"RESULT_MSG<<EOF_MSG\n{msg}\nEOF_MSG\n")


def fail(msg):
    set_result(f"스레드 오늘의 운세 업로드 실패\n사유: {msg}")
    sys.exit(msg)


def already_posted(date_text):
    """최근 스레드 글 중에 같은 날짜 운세가 이미 있는지 확인 (중복 발행 방지)"""
    res = call(f"{API}/{USER_ID}/threads", {"fields": "text", "limit": "10", "access_token": TOKEN})
    for item in res.get("data", []):
        first = (item.get("text") or "").split("\n")[0]
        if date_text in first:
            return True
    return False


def post():
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    # 며칠 뒤 운세를 올릴지 (1 = 내일 운세, 0 = 오늘 운세)
    days_ahead = int(os.environ.get("DAYS_AHEAD", "1"))
    target = now + timedelta(days=days_ahead)
    expected = f"{target.year}년 {target.month}월 {target.day}일"

    # 0) 이미 같은 날짜 글이 올라가 있으면 건너뛰기
    if already_posted(expected):
        set_result(f"스레드 오늘의 운세 업로드 건너뜀 ({target.month}월 {target.day}일 운세가 이미 올라가 있음)")
        return

    # 1) 포스텔러 데이터에서 해당 월 글 목록 가져오기
    #    (말일 밤처럼 대상 날짜가 다음 달이면 '다음 달' 목록을 사용)
    month = "current" if target.month == now.month else "next"
    posts = call(SOURCE_URL.replace("month=current", f"month={month}"))["data"]
    if target.day > len(posts):
        fail(f"{target.month}월 {target.day}일 글이 아직 페이지에 없어요. (목록에 {len(posts)}개뿐)")
    text = posts[target.day - 1]

    # 2) 안전장치: 글 첫 줄 날짜가 올릴 날짜와 맞는지 확인
    first_line = text.splitlines()[0]
    if expected not in first_line:
        fail(f"날짜가 안 맞아서 발행을 멈췄어요. 대상: {expected} / 글: {first_line}")

    # 전날 밤에 올리니까 첫 줄의 '오늘의 '를 빼기 -> "☀️ 2026년 10월 15일 띠별 운세"
    new_first_line = first_line.replace("오늘의 ", "")
    text = text.replace(first_line, new_first_line, 1)
    first_line = new_first_line

    # 트위터용 원링크를 스레드용 원링크로 교체
    text = re.sub(r"https://forcetellerkr\.onelink\.me/\S+", THREADS_LINK, text)

    # 3) 스레드에 '발행 준비' 요청 -> 접수번호(id) 받기
    created = call(
        f"{API}/{USER_ID}/threads",
        {"media_type": "TEXT", "text": text, "topic_tag": TOPIC_TAG, "access_token": TOKEN},
        "POST",
    )
    time.sleep(5)

    # 4) 접수번호로 실제 발행
    result = call(
        f"{API}/{USER_ID}/threads_publish",
        {"creation_id": created["id"], "access_token": TOKEN},
        "POST",
    )

    # 5) 게시물 링크 가져오기 (실패해도 발행은 된 것이니 넘어감)
    link = ""
    try:
        link = call(f"{API}/{result['id']}", {"fields": "permalink", "access_token": TOKEN}).get("permalink", "")
    except Exception:
        pass
    print(f"발행 완료: {first_line}")
    set_result(f"스레드 오늘의 운세 업로드 성공\n{link}".strip())


def refresh():
    res = call(
        "https://graph.threads.net/refresh_access_token",
        {"grant_type": "th_refresh_token", "access_token": TOKEN},
    )
    days = res.get("expires_in", 0) // 86400
    set_result(f"스레드 토큰 연장 성공 (약 {days}일 유효)")

    new_token = res["access_token"]
    if new_token != TOKEN:
        # 새 토큰이 나온 경우: 로그에 안 보이게 가리고, 다음 단계에서 Secrets에 저장
        print(f"::add-mask::{new_token}")
        with open(os.environ["GITHUB_ENV"], "a") as f:
            f.write(f"NEW_TOKEN={new_token}\n")
        print("새 토큰이 발급돼서 Secrets에 저장합니다.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"
    if mode == "post":
        post()
    elif mode == "refresh":
        refresh()
    else:
        sys.exit("post 또는 refresh 중 하나를 적어주세요.")
