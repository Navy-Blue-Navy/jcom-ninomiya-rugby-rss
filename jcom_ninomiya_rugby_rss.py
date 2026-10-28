import os
import sys
import time
import requests
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from urllib.parse import urljoin


LIST_URL = "https://www2.myjcom.jp/special/tv/sports/rugby/column/index.shtml"
OUTPUT_FILE = "jcom_ninomiya_rugby.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/156.0.0.0 Safari/537.36"
    )
}


def fetch_page(url):
    last_error = None

    for attempt in range(3):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )

            if response.status_code == 200:
                response.encoding = response.apparent_encoding
                return response

            last_error = RuntimeError(
                f"HTTP {response.status_code}: {url}"
            )

            # 403などは無理に繰り返さない
            if response.status_code == 403:
                break

            # 429 / 5xx は少し待って再試行
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue

            break

        except requests.RequestException as e:
            last_error = e
            if attempt < 2:
                time.sleep(5 * (attempt + 1))

    raise RuntimeError(f"ページ取得失敗: {last_error}")


def parse_date(text):
    text = text.strip()

    for fmt in (
        "%Y年%m月%d日(%a)",
        "%Y年%m月%d日",
        "%Y/%m/%d",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=JST)
        except ValueError:
            pass

    # 「2026年10月22日(木)」の曜日部分を除去
    if "日" in text:
        base = text.split("日", 1)[0] + "日"
        try:
            return datetime.strptime(
                base, "%Y年%m月%d日"
            ).replace(tzinfo=JST)
        except ValueError:
            pass

    return None


def load_existing_items():
    items = {}

    if not os.path.exists(OUTPUT_FILE):
        return items

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        for item in root.findall("./channel/item"):
            link = item.findtext("link")

            if link:
                items[link] = {
                    "title": item.findtext("title") or "",
                    "link": link,
                    "description": item.findtext("description") or "",
                    "pubDate": item.findtext("pubDate") or "",
                }

    except Exception as e:
        print(f"既存RSS読み込みエラー: {e}", file=sys.stderr)
        raise

    return items


def scrape_articles():
    response = fetch_page(LIST_URL)
    soup = BeautifulSoup(response.text, "html.parser")

    articles = {}

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")

        # 個別記事URLだけを対象
        if "/column/detail/" not in href:
            continue

        url = urljoin(LIST_URL, href)

        title = a.get_text(" ", strip=True)

        if not title:
            img = a.find("img")
            if img:
                title = img.get("alt", "").strip()

        # リンク内部だけではタイトルが取れない場合、
        # 親要素からテキストを取得
        parent = a.parent
        parent_text = ""

        if parent:
            parent_text = parent.get_text(" ", strip=True)

        if not title or len(title) < 5:
            title = parent_text

        # 日付を親要素周辺から探す
        date_text = ""

        node = a
        for _ in range(5):
            if node is None:
                break

            text = node.get_text(" ", strip=True)

            import re
            match = re.search(
                r"20\d{2}年\d{1,2}月\d{1,2}日(?:\([^)]+\))?",
                text,
            )

            if match:
                date_text = match.group(0)
                break

            node = node.parent

        pub_dt = parse_date(date_text) if date_text else None

        # URLの日付も利用可能
        if pub_dt is None:
            import re
            match = re.search(r"/detail/(20\d{6})\.shtml", url)

            if match:
                try:
                    pub_dt = datetime.strptime(
                        match.group(1), "%Y%m%d"
                    ).replace(tzinfo=JST)
                except ValueError:
                    pass

        if not title:
            continue

        articles[url] = {
            "title": title.strip(),
            "link": url,
            "description": title.strip(),
            "pubDate": (
                format_datetime(pub_dt)
                if pub_dt
                else ""
            ),
        }

    if not articles:
        raise RuntimeError(
            "記事を1件も取得できませんでした。RSSは更新しません。"
        )

    return articles


def build_rss(items):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
        },
    )

    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = (
        "二宮清純コラム ノーサイドラウンジ"
    )
    ET.SubElement(channel, "link").text = LIST_URL
    ET.SubElement(channel, "description").text = (
        "J:COM 二宮清純コラム ノーサイドラウンジの更新情報"
    )
    ET.SubElement(channel, "language").text = "ja"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(JST)
    )

    def sort_key(item):
        pub = item.get("pubDate", "")

        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(pub)
        except Exception:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)

    sorted_items = sorted(
        items.values(),
        key=sort_key,
        reverse=True,
    )

    for data in sorted_items:
        item = ET.SubElement(channel, "item")

        ET.SubElement(item, "title").text = data["title"]
        ET.SubElement(item, "link").text = data["link"]
        ET.SubElement(item, "guid").text = data["link"]

        ET.SubElement(
            item,
            "description",
        ).text = data["description"]

        if data.get("pubDate"):
            ET.SubElement(
                item,
                "pubDate",
            ).text = data["pubDate"]

    tree = ET.ElementTree(rss)

    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )


def main():
    print("J:COM 二宮清純コラムを取得します")

    # まずWeb取得・解析を完了させる。
    # ここで失敗した場合は既存XMLには触らない。
    new_items = scrape_articles()

    print(f"Webから取得: {len(new_items)}件")

    existing_items = load_existing_items()

    print(f"既存RSS: {len(existing_items)}件")

    # URLをキーに累積
    merged = existing_items.copy()
    merged.update(new_items)

    print(f"統合後: {len(merged)}件")

    build_rss(merged)

    print(f"RSSを保存しました: {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)