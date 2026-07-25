import os
import time
import random
from bs4 import BeautifulSoup
import re
import cloudscraper
from datetime import datetime
from urllib.parse import urlparse

# ================= 唯一配置区 =================
# 👇 将你要爬取的总列表页链接粘贴在下面引号里面 👇
TARGET_LIST_URL = "https://www.intgovforum.org/en/networking-sessions-2020"
# ==============================================

BASE_URL = "https://www.intgovforum.org"

# 创建一个 scraper 实例，模拟真实的 Windows Chrome 浏览器指纹绕过防火墙
scraper = cloudscraper.create_scraper(browser={
    'browser': 'chrome',
    'platform': 'windows',
    'desktop': True
})


def sanitize_filename(title):
    """清理标题中的特殊字符，使其能作为合法的文件名保存"""
    clean_title = re.sub(r'[\\/*?:"<>|]', "", title)
    clean_title = clean_title.replace('\n', ' ').replace('\r', '')
    return clean_title[:100].strip()


def extract_year_from_url(url):
    """从目标 URL 中智能提取年份"""
    match = re.search(r'(20\d{2})', url)
    return match.group(1) if match else None


def get_all_target_links(list_url):
    """智能抓取列表页中所有相关的详情页链接"""
    print(f"🔍 正在请求目标页面: {list_url}")
    try:
        response = scraper.get(list_url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # 智能探测逻辑：找出 URL 中的年份
        target_year = extract_year_from_url(list_url)

        links = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href'].lower()
            is_target = False

            # 核心过滤规则：
            # 如果成功提取到年份，则抓取所有带有 'igf-{年份}-' 的详情页 (通杀ws/of/lt等)
            if target_year and f'igf-{target_year}-' in href:
                is_target = True
            # 如果没提取到年份，启用备用通用关键词过滤
            elif any(kw in href for kw in ['workshop', 'open-forum', 'lightning', 'session']):
                is_target = True

            if is_target:
                full_link = BASE_URL + a_tag['href'] if a_tag['href'].startswith('/') else a_tag['href']
                if full_link not in links:
                    links.append(full_link)

        print(f"✅ 智能扫描完毕，共发现 {len(links)} 个数据详情页链接。")
        return links
    except Exception as e:
        print(f"❌ 获取列表页失败，请检查网络或 URL 是否正确: {e}")
        return []


def download_html(url, save_directory):
    """下载单个详情页并保存"""
    try:
        response = scraper.get(url, timeout=15)
        response.raise_for_status()
        html_content = response.text

        # 强制使用 URL 的最后一段作为唯一文件名
        raw_name = url.split('/')[-1].split('?')[0]
        if len(raw_name) < 3:
            raw_name = f"data_{random.randint(10000, 99999)}"

        safe_filename = sanitize_filename(raw_name)
        file_path = os.path.join(save_directory, f"{safe_filename}.html")

        # 断点续传保护
        if os.path.exists(file_path):
            print(f"    ⏩ 文件已存在，跳过: {safe_filename}.html")
            return True

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        print(f"    ⬇️ 成功保存: {safe_filename}.html")
        return True

    except Exception as e:
        print(f"    ❌ 下载失败 {url}: {e}")
        with open(os.path.join(save_directory, "error_log.txt"), 'a', encoding='utf-8') as f:
            f.write(f"{url} - Error: {e}\n")
        return False


# ================= 主程序 =================
if __name__ == "__main__":
    print(f"\n{'=' * 50}\n🚀 IGF 极简通用数据抓取引擎启动\n{'=' * 50}\n")

    # 根据输入的 URL 自动生成极具辨识度的文件夹名称
    # 例如输入 .../workshop-proposals-2017，文件夹就是 data_workshop-proposals-2017_时间戳
    url_slug = TARGET_LIST_URL.split('/')[-1].split('?')[0]
    if not url_slug:
        url_slug = "igf_custom_data"

    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_save_dir = f"data_{url_slug}_{current_time}"

    if not os.path.exists(run_save_dir):
        os.makedirs(run_save_dir)
        print(f"📁 已自动创建专属数据归档目录: {run_save_dir}\n")

    # 1. 抓取链接
    target_links = get_all_target_links(TARGET_LIST_URL)

    # 2. 批量下载
    if target_links:
        print(f"\n🚀 开始批量下载，数据将安全保存至本地...\n")
        for index, link in enumerate(target_links):
            print(f"进度 [{index + 1}/{len(target_links)}]", end=" ")
            download_html(link, run_save_dir)

            # 随机停顿防封禁
            time.sleep(random.uniform(1.0, 2.5))

        print(f"\n🎉 任务圆满完成！所有文件已保存在文件夹：{run_save_dir}")
    else:
        print("\n⚠️ 任务终止：未在目标页面中找到任何符合条件的详情页。")