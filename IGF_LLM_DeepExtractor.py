"""
IGF LLM Deep Extractor (Refactored)
Multi-threaded extraction using local Ollama LLM (qwen2.5) to extract
speakers, organizations, themes, and keywords from HTML session pages.
Supports checkpoint-based resume.

Uses igf_common for shared utilities.
"""
import os
import json
import re
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from bs4 import BeautifulSoup
import ollama

from igf_common import (
    extract_meta_from_filename,
    clean_html_to_text,
    find_igf_html_files,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class IGFMultiThreadExtractorLLM:
    """Multi-threaded LLM-based extractor for IGF session data."""

    def __init__(
        self,
        root_dir: str,
        model_name: str = "qwen2.5",
        output_csv: str = "igf_historical_data_deep_clean.csv",
        max_workers: int = 4,
    ):
        self.root_dir = Path(root_dir)
        self.model_name = model_name
        self.output_csv = output_csv
        self.max_workers = max_workers

        self.data_records = []
        self.lock = threading.Lock()

        # Load existing progress for resume
        if os.path.exists(self.output_csv):
            self.existing_df = pd.read_csv(self.output_csv)
            self.processed_files = set(self.existing_df["Source_File"].dropna().tolist())
            logging.info(f"Resume mode: {len(self.processed_files)} files already processed, will skip.")
        else:
            self.existing_df = pd.DataFrame()
            self.processed_files = set()

    # ------- LLM extraction -------

    def extract_with_llm(self, text_content: str, title: str) -> dict:
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
                messages=[{"role": "user", "content": prompt}],
                format="json",
                options={"temperature": 0.0},
            )

            content = response["message"]["content"].strip()
            data = json.loads(content)

            return {
                "speakers": " | ".join(data.get("speakers", [])),
                "organizations": " | ".join(data.get("organizations", [])),
                "themes": " | ".join(data.get("themes", [])),
                "keywords": " | ".join(data.get("keywords", [])),
            }
        except Exception as e:
            logging.error(f"LLM extraction error: {e}")
            return {"speakers": "N/A", "organizations": "N/A", "themes": "N/A", "keywords": "N/A"}

    # ------- Per-file processing -------

    def process_single_file(self, file_path: Path):
        filename = file_path.name

        with self.lock:
            if filename in self.processed_files:
                return None

        year, session_type = extract_meta_from_filename(filename)

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                soup = BeautifulSoup(f, "html.parser")

            title_elem = soup.find("h1", class_="page-title") or soup.find("h1")
            title = title_elem.get_text(strip=True) if title_elem else filename

            clean_text = clean_html_to_text(soup)
            extracted = self.extract_with_llm(clean_text, title)

            paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 20]
            description = " ".join(paragraphs) if paragraphs else clean_text[:500]

            record = {
                "Source_File": filename,
                "Year": year,
                "Session_Type": session_type,
                "Title": title,
                "Themes": extracted["themes"] if extracted["themes"] else "N/A",
                "Speakers": extracted["speakers"] if extracted["speakers"] else "N/A",
                "Organizations": extracted["organizations"] if extracted["organizations"] else "N/A",
                "Keywords": extracted["keywords"] if extracted["keywords"] else "N/A",
                "Description": description,
            }

            with self.lock:
                self.data_records.append(record)
                self.processed_files.add(filename)

            return record

        except Exception as e:
            logging.warning(f"Error processing {filename}: {e}")
            return None

    # ------- Checkpoint save -------

    def save_checkpoint(self):
        with self.lock:
            if not self.data_records:
                return

            new_df = pd.DataFrame(self.data_records)
            combined_df = (
                pd.concat([self.existing_df, new_df], ignore_index=True)
                if not self.existing_df.empty
                else new_df
            )
            combined_df.to_csv(self.output_csv, index=False, encoding="utf-8-sig")

            self.existing_df = combined_df
            self.data_records = []
            logging.info(f"Checkpoint saved: {len(self.existing_df)} total records.")

    # ------- Pipeline -------

    def run_pipeline(self):
        logging.info(f"Starting LLM extraction (Model: {self.model_name}, Workers: {self.max_workers})...")

        valid_html_files = find_igf_html_files(self.root_dir)
        files_to_process = [p for p in valid_html_files if p.name not in self.processed_files]

        logging.info(f"Total HTML files: {len(valid_html_files)}, need to process: {len(files_to_process)}")

        if not files_to_process:
            logging.info("All files already processed.")
            return

        completed_count = 0
        checkpoint_batch = 20

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.process_single_file, fp): fp for fp in files_to_process}

            for future in as_completed(futures):
                completed_count += 1
                if completed_count % checkpoint_batch == 0:
                    logging.info(f"Progress: {completed_count} / {len(files_to_process)}")
                    self.save_checkpoint()

        self.save_checkpoint()
        logging.info("All processing complete.")


if __name__ == "__main__":
    ROOT_DIRECTORY = r".\PyCharmMiscProject"

    extractor = IGFMultiThreadExtractorLLM(
        root_dir=ROOT_DIRECTORY,
        model_name="qwen2.5",
        output_csv="igf_historical_data_deep_clean_v2.csv",
        max_workers=4,
    )
    extractor.run_pipeline()
