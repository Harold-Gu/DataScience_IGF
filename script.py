import os
import time
import random
from bs4 import BeautifulSoup
import re
import cloudscraper
from datetime import datetime

# ================= 配置区 =================
# 请将这里的 URL 替换为你正在爬取的 406 个提案的列表页网址
LIST_PAGE_URL = "https://www.intgovforum.org/en/workshop-proposals-2017"
# 这里设置文件夹的前缀名，后面会自动拼接时间戳
BASE_SAVE_DIR_PREFIX = "igf_raw_data"
BASE_URL = "https://www.intgovforum.org"

# 创建一个 scraper 实例，模拟真实的 Windows Chrome 浏览器指纹
scraper = cloudscraper.create_scraper(browser={
    'browser': 'chrome',
    'platform': 'windows',
    'desktop': True
})


# ==========================================

def sanitize_filename(title):
    """清理标题中的特殊字符，使其能作为合法的文件名保存"""
    clean_title = re.sub(r'[\\/*?:"<>|]', "", title)
    clean_title = clean_title.replace('\n', ' ').replace('\r', '')
    return clean_title[:100].strip()


def get_all_proposal_links(list_url):
    """从总列表页获取所有具体提案的链接"""
    print(f"正在请求列表页: {list_url}")
    try:
        response = scraper.get(list_url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        links = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            # 根据 IGF 的链接命名习惯进行启发式过滤
            if 'igf-2017-ws-' in href.lower() or 'workshop' in href.lower():
                full_link = BASE_URL + href if href.startswith('/') else href
                if full_link not in links:
                    links.append(full_link)

        print(f"✅ 在列表页中找到了 {len(links)} 个提案链接。")
        return links
    except Exception as e:
        print(f"❌ 获取列表页失败: {e}")
        return []


def download_html(url, save_directory):
    """下载单个网页并保存为 HTML 文件"""
    try:
        response = scraper.get(url, timeout=15)
        response.raise_for_status()
        html_content = response.text

        # 强制使用 URL 的最后一段作为文件名，保证绝对唯一
        raw_name = url.split('/')[-1]
        raw_name = raw_name.split('?')[0]

        if len(raw_name) < 3:
            raw_name = f"proposal_{random.randint(10000, 99999)}"

        safe_filename = sanitize_filename(raw_name)
        file_path = os.path.join(save_directory, f"{safe_filename}.html")

        # 断点续传：文件已存在则跳过 (针对单次运行中的中断)
        if os.path.exists(file_path):
            print(f"⏩ 文件已存在，跳过: {safe_filename}.html")
            return True

        # 写入文件
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        print(f"⬇️ 成功保存: {safe_filename}.html")
        return True

    except Exception as e:
        print(f"❌ 下载失败 {url}: {e}")
        with open(os.path.join(save_directory, "error_log.txt"), 'a', encoding='utf-8') as f:
            f.write(f"{url} - Error: {e}\n")
        return False


# ================= 主程序 =================
if __name__ == "__main__":
    # 【新增功能】：生成带有当前时间戳的动态文件夹名称
    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_save_dir = f"{BASE_SAVE_DIR_PREFIX}_{current_time}"

    # 创建本次运行的专属目录
    if not os.path.exists(run_save_dir):
        os.makedirs(run_save_dir)
        print(f"📁 已创建本次运行的专属数据文件夹: {run_save_dir}")

    target_links = get_all_proposal_links(LIST_PAGE_URL)

    if not target_links:
        print("没有找到任何链接，请检查 get_all_proposal_links 中的查找规则。")
    else:
        print(f"\n🚀 开始批量下载，共计 {len(target_links)} 个任务...\n")

        for index, link in enumerate(target_links):
            print(f"进度 [{index + 1}/{len(target_links)}]", end=" ")
            # 传入本次动态生成的文件夹路径
            download_html(link, run_save_dir)

            time.sleep(random.uniform(1.5, 3.5))

        print("\n🎉 全部下载任务执行完毕！请查看目录：", run_save_dir)