import os
import re
import json
from pathlib import Path
from bs4 import BeautifulSoup


def extract_metadata(file_path: Path):

    folder = file_path.parent.name
    filename = file_path.name
    year, session_type = "Unknown", "Unknown"

    folder_match = re.search(r'^data_([a-zA-Z0-9\-]+)-(\d{4})_', folder)
    if folder_match:
        session_type = folder_match.group(1).replace('-', ' ').title()
        year = folder_match.group(2)
    elif "igf_raw_data" in folder:
        session_type = "Workshop"
        year_match = re.search(r'igf_raw_data_(\d{4})', folder)
        if year_match:
            year = year_match.group(1)
    # Had considered many factors, including separating all types of meetings. Here, I will only conduct tests on the "ws" and "open forum" types.
    if year == "Unknown":
        file_match = re.search(r'igf-(\d{4})-([a-zA-Z0-9\-]+?)-\d+', filename, re.I)
        if file_match:
            year = file_match.group(1)
            if session_type == "Unknown":
                raw_type = file_match.group(2).lower()
                if 'ws' in raw_type or 'workshop' in raw_type:
                    session_type = "Workshop"
                elif 'open-forum' in raw_type:
                    session_type = "Open Forum"
                else:
                    session_type = raw_type.replace('-', ' ').title()

    return year, session_type


def parse_igf_page(soup: BeautifulSoup, year: str, session_type: str, file_path: Path):

    for tag in soup(['script', 'style', 'meta', 'link', 'noscript', 'head', 'nav', 'footer', 'header', 'aside']):
        tag.decompose()

    noise_patterns = re.compile(r'(header|footer|nav|sidebar|menu|accessibility|contact|breadcrumb|user-menu)', re.I)
    for tag in soup.find_all(attrs={"class": noise_patterns}):
        tag.decompose()
    for tag in soup.find_all(attrs={"id": noise_patterns}):
        tag.decompose()


    raw_text = soup.get_text(separator='\n', strip=True)


    raw_text = re.sub(r'^(Welcome to the United Nations|Skip to main content)[\s\|A-Za-z]*\n', '', raw_text, flags=re.I)
    if "UNITED NATIONS\nContact information" in raw_text:
        raw_text = raw_text.split("UNITED NATIONS\nContact information")[0]
    elif "Secretariat of the Internet Governance Forum" in raw_text:
        raw_text = raw_text.split("Secretariat of the Internet Governance Forum")[0]


    def extract_field(pattern, text):
        m = re.search(pattern, text, re.I | re.DOTALL)
        return m.group(1).strip() if m else None

    theme = extract_field(r'\nTheme\n([^\n]+)', raw_text)
    subtheme = extract_field(r'\nSubtheme\n([^\n]+)', raw_text)
    website = extract_field(r'Organization\'s Website\n([^\n]+)', raw_text)
    speakers = extract_field(
        r'\nSpeakers\n(.*?)(?=\nOnsite Moderator|\nOnline Moderator|\nRapporteur|\nSDGs|\nFormat|\nDescription)',
        raw_text)
    sdgs = extract_field(r'\nSDGs\n(.*?)(?=\nTargets:|\nFormat|\nDescription)', raw_text)
    format_type = extract_field(r'\nFormat\n([^\n]+)', raw_text)


    description = extract_field(
        r'\nDescription\n(.*?)(?=\nReport|\nThe co-organisers|\nCall to Action|\nSession Report|$)', raw_text)
    report = extract_field(r'\nReport\n(.*)', raw_text) or extract_field(r'\nSession Report.*?\n(.*)', raw_text)


    clean_content = raw_text
    if "Description\n" in clean_content:
        clean_content = "Description:\n" + clean_content.split("Description\n", 1)[1]


    clean_content = clean_content.replace('\xa0', ' ')
    clean_content = re.sub(r'\n{3,}', '\n\n', clean_content).strip()

    return {
        "folder_name": file_path.parent.name,
        "file_name": file_path.name,
        "year": year,
        "session_type": session_type,
        "metadata": {
            "theme": theme or "N/A",
            "subtheme": subtheme or "N/A",
            "website": website or "N/A",
            "speakers": [s.strip() for s in speakers.split('\n') if s.strip()] if speakers else [],
            "sdgs": [s.strip() for s in sdgs.split('\n') if s.strip()] if sdgs else [],
            "format": format_type or "N/A"
        },
        "description": description.strip() if description else "N/A",
        "report": report.strip() if report else "N/A",
        "content": clean_content
    }


def process_html_files(input_dir, output_file):
    extracted_data = []
    html_files = list(Path(input_dir).rglob('*.html'))
    total_files = len(html_files)

    if total_files == 0:
        print("DO NOT FIND HTML FILES")
        return

    print(f" The number of document {total_files} ")

    for index, file_path in enumerate(html_files, 1):
        try:
            year, session_type = extract_metadata(file_path)
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                soup = BeautifulSoup(f.read(), 'html.parser')

            item_data = parse_igf_page(soup, year, session_type, file_path)
            extracted_data.append(item_data)

            if index % 200 == 0:
                print(f"{index} / {total_files} ...")

        except Exception as e:
            print(f"fail {file_path.name} ： {e}")


    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(extracted_data, f, ensure_ascii=False, indent=2)
    print(f"Success: {output_file}")


if __name__ == "__main__":
    INPUT_FOLDER = r"C:\Users\guhao\PyCharmMiscProject"
    OUTPUT_JSON = "./conference_texts_structured.json"

    process_html_files(INPUT_FOLDER, OUTPUT_JSON)