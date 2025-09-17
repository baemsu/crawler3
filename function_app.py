# 필요한 패키지:
# pip install requests beautifulsoup4 tzdata azure-functions

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone, timedelta
import json
import re
import time

# --- KST 설정 (zoneinfo 실패 환경 대비 폴백) ---
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    try:
        KST = ZoneInfo("Asia/Seoul")
    except Exception:
        KST = timezone(timedelta(hours=9))  # 폴백: UTC+9
except Exception:
    KST = timezone(timedelta(hours=9))

CATEGORY_URL = "https://techcrunch.com/category/artificial-intelligence/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r

def get_article_links(category_url=CATEGORY_URL, limit=50):
    """
    카테고리 페이지에서 기사 링크를 최대 limit개까지 수집.
    """
    html = fetch(category_url).text
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    # 1) h3 내부의 앵커들 우선
    for h3 in soup.find_all("h3"):
        a = h3.find("a", href=True)
        if a and is_article_url(a["href"]):
            links.add(normalize_link(a["href"]))

    # 2) 보강: 페이지 내 모든 a 중 연-월 패턴 포함 URL
    if len(links) < limit:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if is_article_url(href):
                links.add(normalize_link(href))
            if len(links) >= limit:
                break

    return list(links)[:limit]

def is_article_url(href: str) -> bool:
    """
    TechCrunch 기사 URL은 일반적으로 /YYYY/MM/ 형태를 가짐.
    """
    try:
        u = urlparse(href)
        path = u.path
        return ("techcrunch.com" in u.netloc or u.netloc == "") and re.search(r"/20\d{2}/\d{2}/", path)
    except Exception:
        return False

def normalize_link(href: str) -> str:
    return urljoin(CATEGORY_URL, href)

def parse_article(url: str):
    """
    기사 페이지에서 제목, 본문, 발행일시(UTC/KST 변환)를 파싱.
    발행일 우선순위:
      1) <meta property="article:published_time" content="ISO8601">
      2) JSON-LD(NewsArticle/BlogPosting) 내 datePublished
      3) <time datetime="...">
      4) 페이지 텍스트의 월/일/연도 패턴
    본문 우선순위:
      1) JSON-LD의 articleBody
      2) <article> 내 <p>들 연결
    """
    res = fetch(url)
    soup = BeautifulSoup(res.text, "html.parser")

    # 제목
    title = soup.find("h1")
    title_text = title.get_text(strip=True) if title else ""

    # 발행일
    published_dt = (
        get_meta_datetime(soup, "article:published_time")
        or get_ldjson_datetime(soup)
        or get_time_tag_datetime(soup)
        or get_text_datetime_fallback(soup)
    )

    # 본문
    body_text = get_ldjson_article_body(soup) or extract_paragraphs(soup)

    return {
        "url": url,
        "title": title_text,
        "published_utc": published_dt.astimezone(timezone.utc).isoformat() if published_dt else None,
        "published_kst": published_dt.astimezone(KST).isoformat() if published_dt else None,
        "body": (body_text or "").strip()
    }

def get_meta_datetime(soup, prop):
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    if tag and tag.get("content"):
        try:
            return datetime.fromisoformat(tag["content"].replace("Z", "+00:00"))
        except Exception:
            pass
    return None

def get_ldjson_datetime(soup):
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
            candidates = data if isinstance(data, list) else [data]
            for obj in candidates:
                if isinstance(obj, dict) and obj.get("@type") in {"NewsArticle", "Article", "BlogPosting"}:
                    dp = obj.get("datePublished") or obj.get("dateCreated")
                    if dp:
                        return datetime.fromisoformat(dp.replace("Z", "+00:00"))
        except Exception:
            continue
    return None

def get_time_tag_datetime(soup):
    t = soup.find("time")
    if t and t.get("datetime"):
        try:
            return datetime.fromisoformat(t["datetime"].replace("Z", "+00:00"))
        except Exception:
            pass
    if t and t.get_text(strip=True):
        return parse_human_datetime(t.get_text(" ", strip=True))
    return None

def get_text_datetime_fallback(soup):
    text = soup.get_text(" ", strip=True)
    return parse_human_datetime(text)

def parse_human_datetime(text: str):
    m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}", text)
    if m:
        try:
            d = datetime.strptime(m.group(0), "%B %d, %Y")
            return d.replace(tzinfo=timezone.utc)  # 시각 없으면 UTC 자정
        except Exception:
            return None
    return None

def get_ldjson_article_body(soup):
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
            candidates = data if isinstance(data, list) else [data]
            for obj in candidates:
                if isinstance(obj, dict) and obj.get("@type") in {"NewsArticle", "Article", "BlogPosting"}:
                    body = obj.get("articleBody")
                    if body:
                        return body
        except Exception:
            continue
    return None

def extract_paragraphs(soup):
    article = soup.find("article") or soup
    paragraphs = []
    for p in article.find_all("p"):
        bad = p.find_parent(["aside", "figcaption", "nav", "footer"])
        if bad:
            continue
        txt = p.get_text(" ", strip=True)
        if len(txt) >= 2:
            paragraphs.append(txt)
    return "\n\n".join(paragraphs)

def is_today_kst(dt: datetime, today_kst: datetime):
    if not dt:
        return False
    return dt.astimezone(KST).date() == today_kst.date()

def crawl_today(category_url=CATEGORY_URL, today_kst=None, limit=40, sleep_sec=1.0):
    if today_kst is None:
        today_kst = datetime.now(KST)
    links = get_article_links(category_url, limit=limit)
    results = []
    for url in links:
        try:
            art = parse_article(url)
            if art["published_kst"] and is_today_kst(datetime.fromisoformat(art["published_kst"]), today_kst):
                results.append(art)
        except Exception:
            pass
        time.sleep(sleep_sec)  # 예의상 천천히
    return results

# --- 로컬 실행용 진입점(유지) ---
if __name__ == "__main__":
    today_kst = datetime.now(KST)
    items = crawl_today(today_kst=today_kst, limit=50, sleep_sec=0.7)
    print(json.dumps({
        "date_kst": today_kst.strftime("%Y-%m-%d"),
        "count": len(items),
        "items": items
    }, ensure_ascii=False, indent=2))

# --- Azure Functions HTTP 트리거 추가 ---
try:
    import azure.functions as func
    app = func.FunctionApp()

    # ✅ 라우트 이름을 'ai-today' 로 통일 (호출은 /api/ai-today)
    @app.route(route="ai-today", methods=["GET", "POST"], auth_level=func.AuthLevel.ANONYMOUS)
    def ai_today(req: func.HttpRequest) -> func.HttpResponse:
        try:
            qs = req.params
            date_str = qs.get("date")
            limit_str = qs.get("limit")
            sleep_str = qs.get("sleep")
            category_url = qs.get("category_url") or CATEGORY_URL

            if req.method == "POST":
                try:
                    body = req.get_json()
                except ValueError:
                    body = {}
                date_str = body.get("date", date_str)
                if "limit" in body and body.get("limit") is not None:
                    limit_str = str(body.get("limit"))
                if "sleep" in body and body.get("sleep") is not None:
                    sleep_str = str(body.get("sleep"))
                category_url = body.get("category_url", category_url)

            # date 파싱
            today_kst = None
            if date_str:
                try:
                    yyyy, mm, dd = map(int, date_str.split("-"))
                    today_kst = datetime(yyyy, mm, dd, tzinfo=KST)
                except Exception:
                    return func.HttpResponse(
                        json.dumps({"error": "invalid date format, use YYYY-MM-DD"}),
                        status_code=400,
                        mimetype="application/json",
                    )

            # limit/sleep 기본값 및 범위
            limit = 40
            if limit_str:
                try:
                    limit = max(1, min(int(limit_str), 80))
                except Exception:
                    pass

            sleep_sec = 0.7
            if sleep_str:
                try:
                    sleep_sec = float(sleep_str)
                    if sleep_sec < 0:
                        sleep_sec = 0.0
                    if sleep_sec > 2:
                        sleep_sec = 2.0
                except Exception:
                    pass

            items = crawl_today(
                category_url=category_url,
                today_kst=today_kst,
                limit=limit,
                sleep_sec=sleep_sec,
            )

            out = {
                "date_kst": (today_kst or datetime.now(KST)).strftime("%Y-%m-%d"),
                "count": len(items),
                "items": items,
            }
            return func.HttpResponse(json.dumps(out, ensure_ascii=False), status_code=200, mimetype="application/json")

        except Exception as e:
            return func.HttpResponse(
                json.dumps({"error": "internal_error", "detail": str(e)}),
                status_code=500,
                mimetype="application/json",
            )

    # (선택) 라우팅만 빠르게 확인하는 핑 엔드포인트
    @app.route(route="ping", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
    def ping(req: func.HttpRequest) -> func.HttpResponse:
        return func.HttpResponse("ok", status_code=200)

except ImportError:
    # 로컬에서 azure.functions 미설치 시에도 __main__ 동작하도록 무시
    pass
