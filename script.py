import os
import time
import random
from bs4 import BeautifulSoup
import re
import cloudscraper
from datetime import datetime


#The URLs to be crawled should be determined based on the specific circumstances.
LIST_PAGE_URL = "https://www.intgovforum.org/en/workshop-proposals-2017"
#Folder prefix
BASE_SAVE_DIR_PREFIX = "igf_raw_data"
BASE_URL = "https://www.intgovforum.org"

# Scraper instance, simulating the actual Windows Chrome browser fingerprint
scraper = cloudscraper.create_scraper(browser={
    'browser': 'chrome',
    'platform': 'windows',
    'desktop': True
})


def sanitize_filename(title):

    clean_title = re.sub(r'[\\/*?:"<>|]', "", title)
    clean_title = clean_title.replace('\n', ' ').replace('\r', '')
    return clean_title[:100].strip()


def get_all_proposal_links(list_url):
    print(f"requesting the list page: {list_url}")
    try:
        response = scraper.get(list_url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        links = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            # The suffixes for different years need to be modified accordingly. These modifications are based on the analysis of the URL logic and are made as guesses.
            if 'igf-2017-ws-' in href.lower() or 'workshop' in href.lower():
                full_link = BASE_URL + href if href.startswith('/') else href
                if full_link not in links:
                    links.append(full_link)

        print(f"On the list page, {len(links)} proposal links were found.")
        return links
    except Exception as e:
        print(f"Failed to retrieve the list page: {e}")
        return []


def download_html(url, save_directory):
    try:
        response = scraper.get(url, timeout=15)
        response.raise_for_status()
        html_content = response.text

        # Use the last part of the URL as the file name
        raw_name = url.split('/')[-1]
        raw_name = raw_name.split('?')[0]

        if len(raw_name) < 3:
            raw_name = f"proposal_{random.randint(10000, 99999)}"

        safe_filename = sanitize_filename(raw_name)
        file_path = os.path.join(save_directory, f"{safe_filename}.html")

        # If the file already exists, skip it.
        if os.path.exists(file_path):
            print(f"The file already exists. Skipping.: {safe_filename}.html")
            return True

        # write
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        print(f"Save as: {safe_filename}.html")
        return True

    except Exception as e:
        print(f"False {url}: {e}")
        with open(os.path.join(save_directory, "error_log.txt"), 'a', encoding='utf-8') as f:
            f.write(f"{url} - Error: {e}\n")
        return False



if __name__ == "__main__":
    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_save_dir = f"{BASE_SAVE_DIR_PREFIX}_{current_time}"
    if not os.path.exists(run_save_dir):
        os.makedirs(run_save_dir)
        print(f"Folder name: {run_save_dir}")

    target_links = get_all_proposal_links(LIST_PAGE_URL)

    if not target_links:
        print("No links were found.")
    else:
        print(f"\n start  task number:{len(target_links)} \n")

        for index, link in enumerate(target_links):
            print(f"[{index + 1}/{len(target_links)}]", end=" ")

            download_html(link, run_save_dir)

            time.sleep(random.uniform(1.5, 3.5))

        print("\nsuccess", run_save_dir)