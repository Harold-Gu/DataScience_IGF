import os
import re
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class IGFDataExtractorV4:
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

    def _is_valid_speaker(self, name: str) -> bool:
        """核心防污染校验器：严格甄别并过滤非人名、角色标签与占位符"""
        if not name or len(name) < 3 or len(name) > 40:
            return False

        name_lower = name.lower().strip()

        # 1. 绝对不能包含的无效角色、组织或UI关键词
        invalid_keywords = [
            'speaker', 'moderator', 'panelist', 'organizer', 'organiser',
            'opening remarks', 'civil society', 'alphabetical order',
            'onsite', 'welcome', 'keynote', 'introduction', 'closing',
            'remote', 'chair', 'rapporteur', 'session', 'http', 'www',
            'group', 'forum', 'igf', 'tbc', 'tba', 'various', 'panel',
            'speakers', 'panelists', 'moderators', 'organizers', 'participants',
            'remote hub', 'remote moderator', 'interventions', 'agenda'
        ]
        for kw in invalid_keywords:
            if kw in name_lower:
                return False

        # 2. 排除带编号的占位符（如 "Speaker 1", "Organizer 2"）
        if re.search(r'\b(speaker|panelist|moderator|organizer)\s*\d+\b', name_lower):
            return False

        # 3. 必须包含英文字母（排除纯数字、纯符号）
        if not re.search(r'[a-zA-Z]', name):
            return False

        return True

    def _extract_by_dom_speakers(self, soup: BeautifulSoup):
        """DOM 树结构提取：优先抓取超链接或列表项中的真实人名"""
        speakers = []
        speaker_classes = re.compile(
            r'field-name-field-(speakers|panelists|participants|moderators|speakers-panelists|organizer|person)', re.I
        )
        containers = soup.find_all(class_=speaker_classes)
        for c in containers:
            # 优先提取 <a> 标签和 <li> 标签，通常这些里面包的是精准的人名
            links = [a.get_text(strip=True) for a in c.find_all('a')]
            lis = [li.get_text(strip=True) for li in c.find_all('li')]

            candidates = links if links else lis
            if not candidates:
                candidates = [s.strip() for s in c.stripped_strings]

            for item in candidates:
                item_clean = item.rstrip(':').strip()
                if self._is_valid_speaker(item_clean):
                    speakers.append(item_clean)
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
        """核心后备正则引擎：针对文本块做精准匹配与拆分"""
        if not full_text:
            return "N/A"

        if target_type == "speakers":
            patterns = [
                r'(?:Speakers|Panellists|Panelists|Moderators|Speakers/Panellists|Organizers|Organisers|Speaker\(s\))\s*[:\-]\s*([^\n\r]+)',
                r'(?:Speakers|Panellists|Panelists)\s*\n\s*([^\n\r]+)'
            ]
            for pattern in patterns:
                match = re.search(pattern, full_text, re.I)
                if match:
                    content = match.group(1).strip()
                    content = re.sub(r'<(.*?)>', '', content)
                    # 按照逗号或 and 将可能的一长串名字拆开
                    parts = re.split(r'[,;]|\band\b', content)
                    valid_parts = []
                    for p in parts:
                        p_clean = p.rstrip(':').strip()
                        if self._is_valid_speaker(p_clean):
                            valid_parts.append(p_clean)
                    if valid_parts:
                        return " | ".join(valid_parts)
            return "N/A"

        elif target_type == "themes":
            patterns = [
                r'(?:Theme|Sub-theme|Subtheme|Main Theme|Tags|Category)\s*[:\-]\s*([^\n\r]+)',
                r'(?:Theme|Subtheme)\s*\n\s*([^\n\r]+)'
            ]
            for pattern in patterns:
                match = re.search(pattern, full_text, re.I)
                if match:
                    content = match.group(1).strip()
                    content = re.sub(r'<(.*?)>', '', content)
                    if 2 < len(content) < 300:
                        return content
        return "N/A"

    def _clean_field(self, items_list, field_type):
        """清洗并去重字段"""
        clean_items = []
        for item in list(dict.fromkeys(items_list)):
            c_item = re.sub(r'\s+', ' ', item).rstrip(':').strip()
            if field_type == "speakers":
                if self._is_valid_speaker(c_item):
                    clean_items.append(c_item)
            else:
                if c_item and len(c_item) > 2:
                    clean_items.append(c_item)
        return " | ".join(clean_items) if clean_items else "N/A"

    def parse_html(self, file_path: Path):
        filename = file_path.name
        year, session_type = self._extract_meta_from_filename(filename)

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                soup = BeautifulSoup(f, 'html.parser')

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
            speakers = self._clean_field(speakers_list, "speakers")
            if speakers == "N/A":
                speakers = self._extract_by_regex(full_text, "speakers")

            # 3. Themes 提取
            themes_list = self._extract_by_dom_themes(soup)
            themes = self._clean_field(themes_list, "themes")
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

        logging.info(f"锁定 {len(valid_html_files)} 个有效文件，开始多层过滤解析...")
        for i, file_path in enumerate(valid_html_files):
            self.parse_html(file_path)
            if (i + 1) % 500 == 0:
                logging.info(f"已解析 {i + 1} / {len(valid_html_files)}...")

        return pd.DataFrame(self.data_records)


if __name__ == "__main__":
    ROOT_DIRECTORY = r"C:\Users\guhao\PyCharmMiscProject"

    extractor = IGFDataExtractorV4(ROOT_DIRECTORY)
    df = extractor.run_pipeline()

    if not df.empty:
        output_csv = "igf_historical_data_clean_v4.csv"
        df.to_csv(output_csv, index=False, encoding='utf-8-sig')
        logging.info(f"✅ 解析完成！数据已保存至 {output_csv}")

        valid_speakers = (df['Speakers_Raw'] != 'N/A').sum()
        valid_themes = (df['Themes'] != 'N/A').sum()
        valid_desc = (df['Description'] != 'N/A').sum()
        print(f"\n================ 数据提取质量报告 (V4) ================")
        print(f"- 演讲者有效提取率: {valid_speakers} / {len(df)} ({valid_speakers / len(df) * 100:.1f}%)")
        print(f"- 主题标签有效提取率: {valid_themes} / {len(df)} ({valid_themes / len(df) * 100:.1f}%)")
        print(f"- 描述文本有效提取率: {valid_desc} / {len(df)} ({valid_desc / len(df) * 100:.1f}%)")
        print(f"==========================================================")