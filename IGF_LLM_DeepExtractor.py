import os
import json
import re
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup
import ollama
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class IGFDeepExtractorLLM:
    def __init__(self, root_dir: str, model_name: str = "qwen2.5", output_csv: str = "igf_deep_extracted.csv"):
        self.root_dir = Path(root_dir)
        self.model_name = model_name
        self.output_csv = output_csv
        self.data_records = []

        # 加载已有进度（断点续传）
        if os.path.exists(self.output_csv):
            self.existing_df = pd.read_csv(self.output_csv)
            self.processed_files = set(self.existing_df['Source_File'].tolist())
            logging.info(f"📂 发现已有进度，已跳过 {len(self.processed_files)} 个已处理文件。")
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
        """剥离杂质，提取高质量上下文文本"""
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.extract()

        # 提取正文，并清理多余空白
        text = soup.get_text(separator='\n')
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        # 限制长度以防超出 LLM 上下文窗口 (取前约 2000 个字符)
        return "\n".join(lines)[:2000]

    def extract_with_llm(self, text_content: str, title: str) -> dict:
        """利用大模型进行深度语义实体抽取"""
        prompt = f"""
Analyze the following Internet Governance Forum (IGF) session text. Extract the following information and return ONLY a JSON object.

Rules for extraction:
1. "speakers": A list of strings. Extract ALL real human names mentioned as speakers, moderators, panelists, or participants. Exclude job titles, UI placeholders (e.g., "Speaker 1"), and organizations.
2. "organizations": A list of strings. Extract ALL organizations, companies, universities, NGOs, governments, or institutions mentioned (e.g., "UN", "Internet Society", "Microsoft", "Civil Society").
3. "themes": A list of strings. Identify 1 to 3 core governance themes discussed (e.g., "Artificial Intelligence", "Cybersecurity", "Human Rights", "Data Privacy").

JSON Schema:
{{
  "speakers": ["name1", "name2"],
  "organizations": ["org1", "org2"],
  "themes": ["theme1", "theme2"]
}}

Session Title: {title}
Session Text:
{text_content}
"""
        try:
            # 使用 format='json' 强制模型输出 JSON 格式
            response = ollama.chat(
                model=self.model_name,
                messages=[{'role': 'user', 'content': prompt}],
                format='json',
                options={'temperature': 0.0}  # 温度设为 0，保证信息提取的确定性和精准度
            )

            content = response['message']['content'].strip()
            data = json.loads(content)

            return {
                "speakers": " | ".join(data.get("speakers", [])),
                "organizations": " | ".join(data.get("organizations", [])),
                "themes": " | ".join(data.get("themes", []))
            }
        except Exception as e:
            logging.error(f"⚠️ LLM 提取失败: {e}")
            return {"speakers": "N/A", "organizations": "N/A", "themes": "N/A"}

    def parse_html(self, file_path: Path):
        filename = file_path.name
        if filename in self.processed_files:
            return None  # 已处理过，跳过

        year, session_type = self._extract_meta_from_filename(filename)

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                soup = BeautifulSoup(f, 'html.parser')

            title_elem = soup.find('h1', class_='page-title') or soup.find('h1')
            title = title_elem.get_text(strip=True) if title_elem else filename

            # 获取清理后的正文
            clean_text = self._clean_html_to_text(soup)

            # 调用大模型提取
            extracted = self.extract_with_llm(clean_text, title)

            # 提取基础描述
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
                "Description": description
            }

            self.data_records.append(record)
            self.processed_files.add(filename)
            return record

        except Exception as e:
            logging.warning(f"⚠️ 文件读取异常 {filename}: {e}")
            return None

    def save_checkpoint(self):
        """保存当前进度到 CSV"""
        if not self.data_records:
            return

        new_df = pd.DataFrame(self.data_records)
        combined_df = pd.concat([self.existing_df, new_df], ignore_index=True) if not self.existing_df.empty else new_df
        combined_df.to_csv(self.output_csv, index=False, encoding='utf-8-sig')

        # 更新 existing_df 并清空当前缓冲记录，准备下一轮
        self.existing_df = combined_df
        self.data_records = []
        logging.info(f"💾 进度已保存！当前总记录数: {len(self.existing_df)}")

    def run_pipeline(self):
        logging.info(f"🚀 启动深度解析引擎 (Model: {self.model_name})...")
        valid_html_files = [p for p in self.root_dir.rglob("*.html") if
                            not any(x in p.parts for x in ['.venv', '__pycache__'])]

        logging.info(f"共锁定 {len(valid_html_files)} 个文件。")

        processed_in_this_run = 0
        for file_path in valid_html_files:
            if file_path.name in self.processed_files:
                continue

            self.parse_html(file_path)
            processed_in_this_run += 1

            # 每处理 20 个文件自动保存一次，防止长时间运行意外中断
            if processed_in_this_run % 20 == 0:
                self.save_checkpoint()

        # 运行结束，保存最后的数据
        self.save_checkpoint()
        logging.info("✅ 深度提取任务全部完成！")


if __name__ == "__main__":
    # 请修改为你的实际路径
    ROOT_DIRECTORY = r"C:\Users\guhao\PyCharmMiscProject"

    # 推荐使用 qwen2.5 (提取逻辑极强) 或 llama3
    extractor = IGFDeepExtractorLLM(
        root_dir=ROOT_DIRECTORY,
        model_name="qwen2.5",
        output_csv="igf_historical_data_deep_clean.csv"
    )

    extractor.run_pipeline()