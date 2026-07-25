import os
import re
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class IGFDataExtractorV3:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.data_records = []

    def _extract_meta_from_filename(self, filename: str):
        year, session_type = "Unknown", "Unknown"
        match = re.search(r'igf-(\d{4})-([a-zA-Z0-9\-]+?)-\d+', filename, re.I)
        if match:
            year = match.group(1)
            raw_type = match.group(2).lower()
            if 'ws' in raw_type or 'workshop' in raw_type:
                session_type = "Workshop"
            elif 'open-forum' in raw_type:
                session_type = "Open Forum"
            elif 'lightning-talk' in raw_type:
                session_type = "Lightning Talk"
            elif 'networking' in raw_type:
                session_type = "Networking Session"
            else:
                session_type = raw_type.replace('-', ' ').title()
        return year, session_type

    def _extract_by_dom_speakers(self, soup: BeautifulSoup):
        """DOM 树结构提取"""
        speakers = []
        speaker_classes = re.compile(
            r'field-name-field-(speakers|panelists|participants|moderators|speakers-panelists|organizer|person)', re.I
        )
        containers = soup.find_all(class_=speaker_classes)
        for c in containers:
            items = [s.strip() for s in c.stripped_strings if len(s.strip()) > 2]
            speakers.extend(items)
        return speakers

    def _extract_by_dom_themes(self, soup: BeautifulSoup):
        """DOM 树结构提取主题"""
        themes = []
        theme_classes = re.compile(r'field-name-field-(tags|theme|subtheme|category|main-theme|taxonomy)', re.I)
        containers = soup.find_all(class_=theme_classes)
        for c in containers:
            items = [t.get_text(strip=True) for t in c.find_all(['a', 'div', 'span', 'li']) if t.get_text(strip=True)]
            themes.extend(items)
        return themes

    def _extract_by_regex(self, full_text: str, target_type: str) -> str:
        """核心后备引擎：直接对全文文本做正则匹配"""
        if not full_text:
            return "N/A"

        if target_type == "speakers":
            # 匹配正文中类似 Speakers: Alice, Bob 或 Panellists: ... 的内容
            patterns = [
                r'(?:Speakers|Panellists|Panelists|Moderators|Speakers/Panellists|Organizers|Organisers|Speaker\(s\))\s*[:\-]\s*([^\n\r]+)',
                r'(?:Speakers|Panellists|Panelists)\s*\n\s*([^\n\r]+)'
            ]
        elif target_type == "themes":
            # 匹配正文中类似 Theme: Digital Governance 或 Subtheme: ... 的内容
            patterns = [
                r'(?:Theme|Sub-theme|Subtheme|Main Theme|Tags|Category)\s*[:\-]\s*([^\n\r]+)',
                r'(?:Theme|Subtheme)\s*\n\s*([^\n\r]+)'
            ]
        else:
            return "N/A"

        for pattern in patterns:
            match = re.search(pattern, full_text, re.I)
            if match:
                content = match.group(1).strip()
                # 截断过长或跨行污染的数据
                content = re.sub(r'<(.*?)>', '', content)  # 去除可能残存的html标签
                if 2 < len(content) < 300:
                    return content
        return "N/A"

    def _clean_field(self, items_list, stop_words):
        clean_items = []
        for item in list(dict.fromkeys(items_list)):
            c_item = re.sub(r'\s+', ' ', item).strip()
            if c_item and c_item.lower() not in stop_words and len(c_item) > 2:
                clean_items.append(c_item)
        return " | ".join(clean_items) if clean_items else "N/A"

    def parse_html(self, file_path: Path):
        filename = file_path.name
        year, session_type = self._extract_meta_from_filename(filename)

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                soup = BeautifulSoup(f, 'html.parser')

            # 提取页面全文本用于正则匹配
            full_text = soup.get_text(separator='\n')

            # 1. Title
            title_elem = soup.find('h1', class_='page-title') or soup.find('h1')
            if title_elem:
                title = title_elem.get_text(strip=True)
            else:
                title_tag = soup.find('title')
                title = title_tag.get_text(strip=True).split('|')[0].strip() if title_tag else filename

            # 2. Speakers 提取 (DOM 优先 -> 失败则转正则)
            speakers_list = self._extract_by_dom_speakers(soup)
            speakers = self._clean_field(speakers_list, {'speakers', 'panelists', 'moderator', 'n/a', 'none'})
            if speakers == "N/A":
                speakers = self._extract_by_regex(full_text, "speakers")

            # 3. Themes 提取 (DOM 优先 -> 失败则转正则)
            themes_list = self._extract_by_dom_themes(soup)
            themes = self._clean_field(themes_list, {'theme', 'subtheme', 'tags', 'category', 'n/a'})
            if themes == "N/A":
                themes = self._extract_by_regex(full_text, "themes")

            # 4. Description 提取
            paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 20]
            description = " ".join(paragraphs) if paragraphs else full_text[:1000].replace('\n', ' ')

            self.data_records.append({
                "Source_File": filename,
                "Year": year,
                "Session_Type": session_type,
                "Title": title,
                "Themes": themes,
                "Speakers_Raw": speakers,
                "Description": description
            })

        except Exception as e:
            logging.warning(f"⚠️ 解析文件异常 {filename}: {e}")

    def run_pipeline(self):
        logging.info(f"🚀 开始精准扫描数据文件...")
        valid_html_files = []
        for file_path in self.root_dir.rglob("*.html"):
            path_parts = [part.lower() for part in file_path.parts]
            if any(p.startswith('.') or p in ['venv', 'site-packages', '__pycache__'] for p in path_parts):
                continue
            if any(p.startswith('data_') or p.startswith('igf_raw_data') for p in path_parts):
                if "proposal" not in file_path.name.lower():
                    valid_html_files.append(file_path)

        logging.info(f"锁定 {len(valid_html_files)} 个有效文件，开始双引擎解析...")
        for i, file_path in enumerate(valid_html_files):
            self.parse_html(file_path)
            if (i + 1) % 500 == 0:
                logging.info(f"已解析 {i + 1} / {len(valid_html_files)}...")

        return pd.DataFrame(self.data_records)


if __name__ == "__main__":
    ROOT_DIRECTORY = r"C:\Users\guhao\PyCharmMiscProject"

    extractor = IGFDataExtractorV3(ROOT_DIRECTORY)
    df = extractor.run_pipeline()

    if not df.empty:
        output_csv = "igf_historical_data_clean_v3.csv"
        df.to_csv(output_csv, index=False, encoding='utf-8-sig')
        logging.info(f"✅ 解析完成！数据已保存至 {output_csv}")

        # 输出提取质量报告
        valid_speakers = (df['Speakers_Raw'] != 'N/A').sum()
        valid_themes = (df['Themes'] != 'N/A').sum()
        valid_desc = (df['Description'] != 'N/A').sum()
        print(f"\n================ 数据提取质量报告 (V3) ================")
        print(f"- 演讲者有效提取率: {valid_speakers} / {len(df)} ({valid_speakers / len(df) * 100:.1f}%)")
        print(f"- 主题标签有效提取率: {valid_themes} / {len(df)} ({valid_themes / len(df) * 100:.1f}%)")
        print(f"- 描述文本有效提取率: {valid_desc} / {len(df)} ({valid_desc / len(df) * 100:.1f}%)")
        print(f"==========================================================")