import pandas as pd
import os


def export_all_frequencies(csv_path: str):
    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print("error")
        return


    df['Year'] = pd.to_numeric(df['Year'], errors='coerce')
    target_cols = ['Themes', 'Speakers', 'Organizations', 'Keywords']

    for col in target_cols:
        if col in df.columns:
            df[col + '_List'] = df[col].fillna('').astype(str).apply(
                lambda x: [
                    item.strip() for item in str(x).split('|')
                    if item.strip() and item.strip().upper() not in ['N/A', 'NAN', 'NONE']
                ]
            )
        else:
            df[col + '_List'] = [[] for _ in range(len(df))]


    all_themes = df.explode('Themes_List')['Themes_List'].dropna()
    all_themes = all_themes[all_themes != '']
    theme_counts = all_themes.value_counts().reset_index()
    theme_counts.columns = ['Theme', 'Frequency']

    theme_csv = 'igf_freq_all_themes.csv'
    theme_counts.to_csv(theme_csv, index=False, encoding='utf-8-sig')
    print(f"file:{theme_csv} number of the topic{len(theme_counts)}")


    all_orgs = df.explode('Organizations_List')['Organizations_List'].dropna()
    all_orgs = all_orgs[all_orgs != '']
    org_counts = all_orgs.value_counts().reset_index()
    org_counts.columns = ['Organization', 'Frequency']

    org_csv = 'igf_freq_all_organizations.csv'
    org_counts.to_csv(org_csv, index=False, encoding='utf-8-sig')
    print(f"organizations{org_csv} the number of organizations{len(org_counts)} ")


    speakers_df = df.explode('Speakers_List').dropna(subset=['Speakers_List'])
    speakers_df = speakers_df[speakers_df['Speakers_List'] != '']

    speaker_counts = speakers_df['Speakers_List'].value_counts()

    speaker_records = []
    for speaker, count in speaker_counts.items():
        speaker_sessions = speakers_df[speakers_df['Speakers_List'] == speaker]
        associated_orgs = speaker_sessions.explode('Organizations_List')['Organizations_List'].dropna()
        associated_orgs = associated_orgs[associated_orgs != '']

        top_org = "(Unknown)"
        if not associated_orgs.empty:
            top_org = associated_orgs.value_counts().index[0]

        speaker_records.append({
            'Speaker': speaker,
            'Frequency': count,
            'Top_Associated_Organization': top_org
        })

    speaker_freq_df = pd.DataFrame(speaker_records)
    speaker_csv = 'igf_freq_all_speakers.csv'
    speaker_freq_df.to_csv(speaker_csv, index=False, encoding='utf-8-sig')
    print(f"file name:{speaker_csv} the number of speaker:{len(speaker_freq_df)} ")


    if 'Keywords' in df.columns:
        all_keywords = df.explode('Keywords_List')['Keywords_List'].dropna()
        all_keywords = all_keywords[all_keywords != '']
        keyword_counts = all_keywords.value_counts().reset_index()
        keyword_counts.columns = ['Keyword', 'Frequency']

        keyword_csv = 'igf_freq_all_keywords.csv'
        keyword_counts.to_csv(keyword_csv, index=False, encoding='utf-8-sig')
        print(f"file name:{keyword_csv} the number of keywords{len(keyword_counts)} ")




if __name__ == "__main__":
    CSV_FILE_PATH = "igf_historical_data_deep_clean_v2.csv"
    export_all_frequencies(CSV_FILE_PATH)