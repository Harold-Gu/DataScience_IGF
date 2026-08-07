"""
IGF HTML-to-JSON Structured Extractor (Refactored)
Parses HTML pages into structured JSON with metadata fields
(theme, subtheme, speakers, SDGs, format, description, report).

Uses igf_common for shared utilities.
"""
import re
import json
from pathlib import Path
from bs4 import BeautifulSoup

from igf_common import (
    extract_meta_from_filename,
    extract_year_from_url,
    decompose_noise_tags,
    safe_extract_field,
)


def extract_metadata(file_path: Path):
    """Extract year and session type from folder/filename heuristics."""
    folder = file_path.parent.name
    filename = file_path.name
    year, session_type = "Unknown", "Unknown"

    folder_match = re.search(r"^data_([a-zA-Z0-9\-]+)-(\d{4})_", folder)
    if folder_match:
        session_type = folder_match.group(1).replace("-", " ").title()
        year = folder_match.group(2)
    elif "igf_raw_data" in folder:
        session_type = "Workshop"
        year_match = re.search(r"igf_raw_data_(\d{4})", folder)
        if year_match:
            year = year_match.group(1)

    if year == "Unknown":
        year, session_type = extract_meta_from_filename(filename)

    return year, session_type


def parse_igf_page(soup: BeautifulSoup, year: str, session_type: str, file_path: Path) -> dict:
    """Parse a single IGF HTML page into structured dict."""
    decompose_noise_tags(soup)

    raw_text = soup.get_text(separator="\n", strip=True)

    # Trim UN boilerplate
    raw_text = re.sub(r"^(Welcome to the United Nations|Skip to main content)[\s\|A-Za-z]*\n", "", raw_text, flags=re.I)
    if "UNITED NATIONS\nContact information" in raw_text:
        raw_text = raw_text.split("UNITED NATIONS\nContact information")[0]
    elif "Secretariat of the Internet Governance Forum" in raw_text:
        raw_text = raw_text.split("Secretariat of the Internet Governance Forum")[0]

    # Extract structured fields
    theme = safe_extract_field(r"\nTheme\n([^\n]+)", raw_text)
    subtheme = safe_extract_field(r"\nSubtheme\n([^\n]+)", raw_text)
    website = safe_extract_field(r"Organization's Website\n([^\n]+)", raw_text)
    speakers_raw = safe_extract_field(
        r"\nSpeakers\n(.*?)(?=\nOnsite Moderator|\nOnline Moderator|\nRapporteur|\nSDGs|\nFormat|\nDescription)",
        raw_text,
    )
    sdgs_raw = safe_extract_field(r"\nSDGs\n(.*?)(?=\nTargets:|\nFormat|\nDescription)", raw_text)
    format_type = safe_extract_field(r"\nFormat\n([^\n]+)", raw_text)
    description = safe_extract_field(
        r"\nDescription\n(.*?)(?=\nReport|\nThe co-organisers|\nCall to Action|\nSession Report|$)",
        raw_text,
    )
    report = safe_extract_field(r"\nReport\n(.*)", raw_text) or safe_extract_field(r"\nSession Report.*?\n(.*)", raw_text)

    # Keep description+ content
    clean_content = raw_text
    if "Description\n" in clean_content:
        clean_content = "Description:\n" + clean_content.split("Description\n", 1)[1]

    clean_content = clean_content.replace("\xa0", " ")
    clean_content = re.sub(r"\n{3,}", "\n\n", clean_content).strip()

    return {
        "folder_name": file_path.parent.name,
        "file_name": file_path.name,
        "year": year,
        "session_type": session_type,
        "metadata": {
            "theme": theme or "N/A",
            "subtheme": subtheme or "N/A",
            "website": website or "N/A",
            "speakers": [s.strip() for s in speakers_raw.split("\n") if s.strip()] if speakers_raw else [],
            "sdgs": [s.strip() for s in sdgs_raw.split("\n") if s.strip()] if sdgs_raw else [],
            "format": format_type or "N/A",
        },
        "description": description.strip() if description else "N/A",
        "report": report.strip() if report else "N/A",
        "content": clean_content,
    }


def process_html_files(input_dir: str, output_file: str):
    """Main pipeline: find HTML files, parse each, write JSON output."""
    extracted_data = []
    html_files = list(Path(input_dir).rglob("*.html"))
    total_files = len(html_files)

    if total_files == 0:
        print("No HTML files found.")
        return

    print(f"Found {total_files} HTML files.")

    for index, file_path in enumerate(html_files, 1):
        try:
            year, session_type = extract_metadata(file_path)
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                soup = BeautifulSoup(f.read(), "html.parser")

            item_data = parse_igf_page(soup, year, session_type, file_path)
            extracted_data.append(item_data)

            if index % 200 == 0:
                print(f"{index} / {total_files} ...")

        except Exception as e:
            print(f"Failed: {file_path.name} - {e}")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(extracted_data, f, ensure_ascii=False, indent=2)
    print(f"Done! Output: {output_file}")


if __name__ == "__main__":
    INPUT_FOLDER = r"C:\Users\guhao\PyCharmMiscProject"
    OUTPUT_JSON = "./conference_texts_structured.json"
    process_html_files(INPUT_FOLDER, OUTPUT_JSON)
