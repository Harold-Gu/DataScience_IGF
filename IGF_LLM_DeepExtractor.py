import os
import json
import re
import logging
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from bs4 import BeautifulSoup
import ollama

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class IGFMultiThreadExtractorLLM:
    def __init__(self, root_dir: str, model_name: str = "qwen2.5",
                 output_csv: str = "igf_historical_data_deep_clean.csv", max_workers: int = 4):
        self.root_dir = Path(root_dir)
        self.model_name = model_name
        self.output_csv = output_csv
        self.max_workers = max_workers

        self.data_records = []
        self.lock = threading.Lock()  # 线程锁，确保线程安全

        # 加载已有进度（断点续传）
        if os.path.exists(self.output_csv):
            self.existing_df = pd.read_csv(self.output_csv)
            self.processed_files = set(self.existing_df['Source_File'].dropna().tolist())
            logging.info(f"📂 发现已有进度，已自动跳过 {len(self.processed_files)} 个已处理文件。")
        else:
            self.existing_df = pd.DataFrame()
            self.processed_files = set()

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
            else:
                session_type = raw_type.replace('-', ' ').title()
        return year, session_type

    def _clean_html_to_text(self, soup: BeautifulSoup) -> str:
        """剥离 HTML 杂质，重点抓取正文与会议陈述 (Statement/Transcript) 区域"""
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.extract()

        # 优先寻找包含 Statement, Transcript, Body 的重点容器；若无则提取全局
        statement_containers = soup.find_all(
            class_=re.compile(r'field-name-field-(statement|transcript|body|content|description)', re.I)
        )

        if statement_containers:
            text_blocks = [c.get_text(separator='\n') for c in statement_containers]
            text = "\n".join(text_blocks)
        else:
            text = soup.get_text(separator='\n')

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        # 扩大文本上限至 ~3500 字符，确保覆盖更长的主旨陈述
        return "\n".join(lines)[:3500]

    def extract_with_llm(self, text_content: str, title: str) -> dict:
        """调用本地 LLM 抽取人名、组织、主题以及专有名词/关键词"""
        prompt = f"""
Analyze the following Internet Governance Forum (IGF) session and statement text. Extract the required information and return ONLY a JSON object.

Rules for extraction:
1. "speakers": A list of strings. Extract ALL real human names mentioned as speakers, moderators, panelists, or statement givers. Exclude job titles, UI placeholders (e.g., "Speaker 1"), and organizations.
2. "organizations": A list of strings. Extract ALL organizations, companies, universities, NGOs, governments, or institutions mentioned (e.g., "UN", "ICANN", "Internet Society", "Microsoft").
3. "themes": A list of strings. Identify 1 to 3 core governance themes discussed (e.g., "Artificial Intelligence", "Cybersecurity", "Human Rights", "Data Privacy").
4. "keywords": A list of strings. Extract meaningful, domain-specific special nouns, technical terms, policy frameworks, protocol names, or unique concepts mentioned in the session text (e.g., "DNS Abuse", "Data Sovereignty", "GDPR", "Zero Trust", "Algorithmic Bias", "Encryption"). Avoid generic words like "meeting", "discussion", "today", "people".

JSON Schema:
{{
  "speakers": ["name1", "name2"],
  "organizations": ["org1", "org2"],
  "themes": ["theme1", "theme2"],
  "keywords": ["term1", "term2"]
}}

Session Title: {title}
Session Text:
{text_content}
"""
        try:
            response = ollama.chat(
                model=self.model_name,
                messages=[{'role': 'user', 'content': prompt}],
                format='json',
                options={'temperature': 0.0}  # 低温确保结果稳定与高确定性
            )

            content = response['message']['content'].strip()
            data = json.loads(content)

            return {
                "speakers": " | ".join(data.get("speakers", [])),
                "organizations": " | ".join(data.get("organizations", [])),
                "themes": " | ".join(data.get("themes", [])),
                "keywords": " | ".join(data.get("keywords", []))
            }
        except Exception as e:
            logging.error(f"⚠️ LLM 提取失败: {e}")
            return {"speakers": "N/A", "organizations": "N/A", "themes": "N/A", "keywords": "N/A"}

    def process_single_file(self, file_path: Path):
        """单文件解析逻辑"""
        filename = file_path.name

        with self.lock:
            if filename in self.processed_files:
                return None

        year, session_type = self._extract_meta_from_filename(filename)

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                soup = BeautifulSoup(f, 'html.parser')

            title_elem = soup.find('h1', class_='page-title') or soup.find('h1')
            title = title_elem.get_text(strip=True) if title_elem else filename

            clean_text = self._clean_html_to_text(soup)
            extracted = self.extract_with_llm(clean_text, title)

            paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 20]
            description = " ".join(paragraphs) if paragraphs else clean_text[:500]

            record = {
                "Source_File": filename,
                "Year": year,
                "Session_Type": session_type,
                "Title": title,
                "Themes": extracted["themes"] if extracted["themes"] else "N/A",
                "Speakers": extracted["speakers"] if extracted["speakers"] else "N/A",
                "Organizations": extracted["organizations"] if extracted["organizations"] else "N/A",
                "Keywords": extracted["keywords"] if extracted["keywords"] else "N/A",  # 新增关键词/专有名词列
                "Description": description
            }

            with self.lock:
                self.data_records.append(record)
                self.processed_files.add(filename)

            return record

        except Exception as e:
            logging.warning(f"⚠️ 文件读取异常 {filename}: {e}")
            return None

    def save_checkpoint(self):
        """保存进度到 CSV"""
        with self.lock:
            if not self.data_records:
                return

            new_df = pd.DataFrame(self.data_records)
            combined_df = pd.concat([self.existing_df, new_df],
                                    ignore_index=True) if not self.existing_df.empty else new_df
            combined_df.to_csv(self.output_csv, index=False, encoding='utf-8-sig')

            self.existing_df = combined_df
            self.data_records = []
            logging.info(f"💾 进度已刷盘保存！当前数据库总记录数: {len(self.existing_df)}")

    def run_pipeline(self):
        logging.info(f"🚀 启动多线程深度提取引擎 V2 (Model: {self.model_name}, Workers: {self.max_workers})...")
        valid_html_files = [p for p in self.root_dir.rglob("*.html") if
                            not any(x in p.parts for x in ['.venv', '__pycache__'])]

        files_to_process = [p for p in valid_html_files if p.name not in self.processed_files]
        logging.info(f"锁定 {len(valid_html_files)} 个文件，当前待处理: {len(files_to_process)} 个。")

        if not files_to_process:
            logging.info("🎉 所有文件均已处理完毕，无需重复执行！")
            return

        completed_count = 0
        checkpoint_batch = 20  # 每 20 个文件自动刷盘一次

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.process_single_file, fp): fp for fp in files_to_process}

            for future in as_completed(futures):
                completed_count += 1
                if completed_count % checkpoint_batch == 0:
                    logging.info(f"⚡ 并发进度: 已完成 {completed_count} / {len(files_to_process)}")
                    self.save_checkpoint()

        self.save_checkpoint()
        logging.info("✅ 深度实体与关键词提取任务全部完成！")


if __name__ == "__main__":
    ROOT_DIRECTORY = r"C:\Users\guhao\PyCharmMiscProject"

    extractor = IGFMultiThreadExtractorLLM(
        root_dir=ROOT_DIRECTORY,
        model_name="qwen2.5",
        output_csv="igf_historical_data_deep_clean_v2.csv",
        max_workers=4
    )

    extractor.run_pipeline()