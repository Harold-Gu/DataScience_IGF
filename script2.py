import os
import time
import random
from bs4 import BeautifulSoup
import re
import cloudscraper
from datetime import datetime
from urllib.parse import urlparse


# Put the URL into the following link
TARGET_LIST_URL = "https://www.intgovforum.org/en/networking-sessions-2021"


BASE_URL = "https://www.intgovforum.org"


scraper = cloudscraper.create_scraper(browser={
    'browser': 'chrome',
    'platform': 'windows',
    'desktop': True
})


def sanitize_filename(title):
    clean_title = re.sub(r'[\\/*?:"<>|]', "", title)
    clean_title = clean_title.replace('\n', ' ').replace('\r', '')
    return clean_title[:100].strip()


def extract_year_from_url(url):

    match = re.search(r'(20\d{2})', url)
    return match.group(1) if match else None


def get_all_target_links(list_url):

    print(f"request this web page {list_url}")
    try:
        response = scraper.get(list_url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        target_year = extract_year_from_url(list_url)

        links = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href'].lower()
            is_target = False


            if target_year and f'igf-{target_year}-' in href:
                is_target = True

            elif any(kw in href for kw in ['workshop', 'open-forum', 'lightning', 'session','network','day','pre-event','launches','awards']):
                is_target = True

            if is_target:
                full_link = BASE_URL + a_tag['href'] if a_tag['href'].startswith('/') else a_tag['href']
                if full_link not in links:
                    links.append(full_link)

        print(f"A total of {len(links)} data detail page links were found.")
        return links
    except Exception as e:
        print(f"error: {e}")
        return []


def download_html(url, save_directory):

    try:
        response = scraper.get(url, timeout=15)
        response.raise_for_status()
        html_content = response.text

        raw_name = url.split('/')[-1].split('?')[0]
        if len(raw_name) < 3:
            raw_name = f"data_{random.randint(10000, 99999)}"

        safe_filename = sanitize_filename(raw_name)
        file_path = os.path.join(save_directory, f"{safe_filename}.html")


        if os.path.exists(file_path):
            print(f"skip {safe_filename}.html")
            return True

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        print(f" save successful: {safe_filename}.html")
        return True

    except Exception as e:
        print(f" error {url}: {e}")
        with open(os.path.join(save_directory, "error_log.txt"), 'a', encoding='utf-8') as f:
            f.write(f"{url} - Error: {e}\n")
        return False


if __name__ == "__main__":

    url_slug = TARGET_LIST_URL.split('/')[-1].split('?')[0]
    if not url_slug:
        url_slug = "igf_custom_data"

    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_save_dir = f"data_{url_slug}_{current_time}"

    if not os.path.exists(run_save_dir):
        os.makedirs(run_save_dir)
        print(f"save to {run_save_dir}\n")


    target_links = get_all_target_links(TARGET_LIST_URL)


    if target_links:

        for index, link in enumerate(target_links):
            print(f"[{index + 1}/{len(target_links)}]", end=" ")
            download_html(link, run_save_dir)

            time.sleep(random.uniform(1.0, 2.5))

        print(f"\n🎉 success{run_save_dir}")
    else:
        print("\nerror")