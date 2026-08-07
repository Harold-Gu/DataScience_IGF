"""
IGF DOM-Based HTML Extractor (V4 - Refactored)
Extracts session metadata from HTML files using BeautifulSoup DOM-first approach,
with regex fallback. Outputs a clean CSV.

Uses igf_common for shared utilities.
"""
import re
import logging
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup

from igf_common import (
    extract_meta_from_filename,
    is_valid_speaker,
    find_igf_html_files,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class IGFDataExtractorV4:
    """DOM-first HTML extractor for IGF session data."""

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.data_records = []

    # ------- DOM extraction helpers -------

    def _extract_by_dom_speakers(self, soup: BeautifulSoup) -> list[str]:
        speakers = []
        speaker_classes = re.compile(
            r"field-name-field-(speakers|panelists|participants|moderators|speakers-panelists|organizer|person)", re.I
        )
        containers = soup.find_all(class_=speaker_classes)
        for c in containers:
            links = [a.get_text(strip=True) for a in c.find_all("a")]
            lis = [li.get_text(strip=True) for li in c.find_all("li")]
            candidates = links if links else lis
            if not candidates:
                candidates = [s.strip() for s in c.stripped_strings]
            for item in candidates:
                item_clean = item.rstrip(":").strip()
                if is_valid_speaker(item_clean):
                    speakers.append(item_clean)
        return speakers

    def _extract_by_dom_themes(self, soup: BeautifulSoup) -> list[str]:
        themes = []
        theme_classes = re.compile(r"field-name-field-(tags|theme|subtheme|category|main-theme|taxonomy)", re.I)
        containers = soup.find_all(class_=theme_classes)
        for c in containers:
            items = [t.get_text(strip=True) for t in c.find_all(["a", "div", "span", "li"]) if t.get_text(strip=True)]
            themes.extend(items)
        return themes

    # ------- Regex fallback -------

    def _extract_by_regex(self, full_text: str, target_type: str) -> str:
        if not full_text:
            return "N/A"

        if target_type == "speakers":
            patterns = [
                r"(?:Speakers|Panellists|Panelists|Moderators|Speakers/Panellists|Organizers|Organisers|Speaker\(s\))\s*[:\-]\s*([^\n\r]+)",
                r"(?:Speakers|Panellists|Panelists)\s*\n\s*([^\n\r]+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, full_text, re.I)
                if match:
                    content = match.group(1).strip()
                    content = re.sub(r"<(.*?)>", "", content)
                    parts = re.split(r"[,;]|\band\b", content)
                    valid_parts = [p.rstrip(":").strip() for p in parts if is_valid_speaker(p.rstrip(":").strip())]
                    if valid_parts:
                        return " | ".join(valid_parts)
            return "N/A"

        elif target_type == "themes":
            patterns = [
                r"(?:Theme|Sub-theme|Subtheme|Main Theme|Tags|Category)\s*[:\-]\s*([^\n\r]+)",
                r"(?:Theme|Subtheme)\s*\n\s*([^\n\r]+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, full_text, re.I)
                if match:
                    content = match.group(1).strip()
                    content = re.sub(r"<(.*?)>", "", content)
                    if 2 < len(content) < 300:
                        return content
        return "N/A"

    # ------- Field cleaning -------

    def _clean_field(self, items_list: list, field_type: str) -> str:
        clean_items = []
        for item in list(dict.fromkeys(items_list)):
            c_item = re.sub(r"\s+", " ", item).rstrip(":").strip()
            if field_type == "speakers":
                if is_valid_speaker(c_item):
                    clean_items.append(c_item)
            else:
                if c_item and len(c_item) > 2:
                    clean_items.append(c_item)
        return " | ".join(clean_items) if clean_items else "N/A"

    # ------- Single file parser -------

    def parse_html(self, file_path: Path):
        filename = file_path.name
        year, session_type = extract_meta_from_filename(filename)

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                soup = BeautifulSoup(f, "html.parser")

            full_text = soup.get_text(separator="\n")

            # Title
            title_elem = soup.find("h1", class_="page-title") or soup.find("h1")
            if title_elem:
                title = title_elem.get_text(strip=True)
            else:
                title_tag = soup.find("title")
                title = title_tag.get_text(strip=True).split("|")[0].strip() if title_tag else filename

            # Speakers: DOM first, regex fallback
            speakers = self._clean_field(self._extract_by_dom_speakers(soup), "speakers")
            if speakers == "N/A":
                speakers = self._extract_by_regex(full_text, "speakers")

            # Themes: DOM first, regex fallback
            themes = self._clean_field(self._extract_by_dom_themes(soup), "themes")
            if themes == "N/A":
                themes = self._extract_by_regex(full_text, "themes")

            # Description
            paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 20]
            description = " ".join(paragraphs) if paragraphs else full_text[:1000].replace("\n", " ")

            self.data_records.append({
                "Source_File": filename,
                "Year": year,
                "Session_Type": session_type,
                "Title": title,
                "Themes": themes,
                "Speakers_Raw": speakers,
                "Description": description,
            })

        except Exception as e:
            logging.warning(f"Error parsing {filename}: {e}")

    # ------- Pipeline -------

    def run_pipeline(self) -> pd.DataFrame:
        logging.info("Scanning for HTML data files...")
        valid_html_files = find_igf_html_files(self.root_dir)
        logging.info(f"Found {len(valid_html_files)} valid HTML files.")

        for i, file_path in enumerate(valid_html_files):
            self.parse_html(file_path)
            if (i + 1) % 500 == 0:
                logging.info(f"Parsed {i + 1} / {len(valid_html_files)} ...")

        return pd.DataFrame(self.data_records)


if __name__ == "__main__":
    ROOT_DIRECTORY = r".\PyCharmMiscProject"

    extractor = IGFDataExtractorV4(ROOT_DIRECTORY)
    df = extractor.run_pipeline()

    if not df.empty:
        output_csv = "igf_historical_data_clean_v4.csv"
        df.to_csv(output_csv, index=False, encoding="utf-8-sig")
        logging.info(f"Saved to {output_csv}")

        valid_speakers = (df["Speakers_Raw"] != "N/A").sum()
        valid_themes = (df["Themes"] != "N/A").sum()
        valid_desc = (df["Description"] != "N/A").sum()

        print(f"Speaker extraction rate:  {valid_speakers} / {len(df)} ({valid_speakers / len(df) * 100:.1f}%)")
        print(f"Theme extraction rate:    {valid_themes} / {len(df)} ({valid_themes / len(df) * 100:.1f}%)")
        print(f"Description extraction rate: {valid_desc} / {len(df)} ({valid_desc / len(df) * 100:.1f}%)")
