"""
IGF Frequency Analysis (Refactored)
Exports frequency counts for themes, organizations, speakers, and keywords.

Uses igf_common for shared utilities.
"""
import pandas as pd

from igf_common import parse_multi_value_col


def export_all_frequencies(csv_path: str):
    """Load CSV, parse multi-value columns, export frequency CSVs for each dimension."""
    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"Error: {csv_path} not found.")
        return

    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    target_cols = ["Themes", "Speakers", "Organizations", "Keywords"]

    for col in target_cols:
        if col in df.columns:
            df[col + "_List"] = parse_multi_value_col(df[col])
        else:
            df[col + "_List"] = [[] for _ in range(len(df))]

    # Themes frequency
    all_themes = df.explode("Themes_List")["Themes_List"].dropna()
    all_themes = all_themes[all_themes != ""]
    theme_counts = all_themes.value_counts().reset_index()
    theme_counts.columns = ["Theme", "Frequency"]
    theme_csv = "igf_freq_all_themes.csv"
    theme_counts.to_csv(theme_csv, index=False, encoding="utf-8-sig")
    print(f"Themes: {theme_csv} ({len(theme_counts)} unique)")

    # Organizations frequency
    all_orgs = df.explode("Organizations_List")["Organizations_List"].dropna()
    all_orgs = all_orgs[all_orgs != ""]
    org_counts = all_orgs.value_counts().reset_index()
    org_counts.columns = ["Organization", "Frequency"]
    org_csv = "igf_freq_all_organizations.csv"
    org_counts.to_csv(org_csv, index=False, encoding="utf-8-sig")
    print(f"Organizations: {org_csv} ({len(org_counts)} unique)")

    # Speakers frequency (with top associated organization)
    speakers_df = df.explode("Speakers_List").dropna(subset=["Speakers_List"])
    speakers_df = speakers_df[speakers_df["Speakers_List"] != ""]
    speaker_counts = speakers_df["Speakers_List"].value_counts()

    speaker_records = []
    for speaker, count in speaker_counts.items():
        speaker_sessions = speakers_df[speakers_df["Speakers_List"] == speaker]
        associated_orgs = speaker_sessions.explode("Organizations_List")["Organizations_List"].dropna()
        associated_orgs = associated_orgs[associated_orgs != ""]
        top_org = associated_orgs.value_counts().index[0] if not associated_orgs.empty else "(Unknown)"
        speaker_records.append({
            "Speaker": speaker,
            "Frequency": count,
            "Top_Associated_Organization": top_org,
        })

    speaker_freq_df = pd.DataFrame(speaker_records)
    speaker_csv = "igf_freq_all_speakers.csv"
    speaker_freq_df.to_csv(speaker_csv, index=False, encoding="utf-8-sig")
    print(f"Speakers: {speaker_csv} ({len(speaker_freq_df)} unique)")

    # Keywords frequency
    if "Keywords" in df.columns:
        all_keywords = df.explode("Keywords_List")["Keywords_List"].dropna()
        all_keywords = all_keywords[all_keywords != ""]
        keyword_counts = all_keywords.value_counts().reset_index()
        keyword_counts.columns = ["Keyword", "Frequency"]
        keyword_csv = "igf_freq_all_keywords.csv"
        keyword_counts.to_csv(keyword_csv, index=False, encoding="utf-8-sig")
        print(f"Keywords: {keyword_csv} ({len(keyword_counts)} unique)")


if __name__ == "__main__":
    CSV_FILE_PATH = "igf_historical_data_deep_clean_v2.csv"
    export_all_frequencies(CSV_FILE_PATH)
