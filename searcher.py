import cloudscraper
import time

# 初始化绕过防火墙的 scraper
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

years = range(2006, 2026)  # 2017 到 2025
categories = [
    "open-forums", "open-forum-proposals",
    "lightning-talks", "lightning-talk-proposals",
    "main-sessions", "pre-events",
    "launches-awards", "networking-sessions"
]

print("🔍 开始自动探测有效 URL...\n")



valid_urls = []

for year in years:
    print(f"--- 正在探测 {year} 年 ---")
    for cat in categories:
        # 猜测的两种主要 URL 格式
        url_format_1 = f"https://www.intgovforum.org/en/{cat}-{year}"
        url_format_2 = f"https://www.intgovforum.org/en/content/igf-{year}-{cat}"

        for url in [url_format_1, url_format_2]:
            try:
                # 只获取网页头部信息，不下载整个网页，探测速度极快
                response = scraper.head(url, timeout=10)
                if response.status_code == 200:
                    print(f"✅ 找到有效链接: {url}")
                    valid_urls.append(url)
            except Exception as e:
                pass

            time.sleep(1)  # 礼貌延迟，防止被封

print("\n🎉 探测完毕！请将以下有效链接复制到你的爬虫配置中：")
for valid_url in valid_urls:
    print(valid_url)