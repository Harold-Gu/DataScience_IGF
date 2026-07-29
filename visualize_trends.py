import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import warnings


warnings.filterwarnings("ignore")

# style
sns.set_theme(style="whitegrid")
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def clean_multi_value_col(df, col_name):

    if col_name in df.columns:
        return df[col_name].fillna('').astype(str).apply(
            lambda x: [
                item.strip() for item in str(x).split('|')
                if item.strip() and item.strip().upper() not in ['N/A', 'NAN', 'NONE']
            ]
        )
    return pd.Series([[]] * len(df))


def visualize_advanced_trends(csv_path: str):

    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"error{csv_path}。")
        return

    df['Year'] = pd.to_numeric(df['Year'], errors='coerce')
    df = df.dropna(subset=['Year'])
    df['Year'] = df['Year'].astype(int)
    all_years = range(df['Year'].min(), df['Year'].max() + 1)

    for col in ['Themes', 'Speakers', 'Organizations']:
        df[col + '_List'] = clean_multi_value_col(df, col)



    themes_df = df[['Year', 'Themes_List']].explode('Themes_List').dropna().reset_index(drop=True)
    themes_df = themes_df[themes_df['Themes_List'] != '']

    top_themes = themes_df['Themes_List'].value_counts().head(6).index
    themes_trend = themes_df[themes_df['Themes_List'].isin(top_themes)]
    theme_pivot = pd.crosstab(themes_trend['Year'], themes_trend['Themes_List']).reindex(all_years, fill_value=0)

    plt.figure(figsize=(12, 6))
    for theme in top_themes:
        plt.plot(theme_pivot.index, theme_pivot[theme], marker='o', linewidth=2, label=theme)
    plt.title('Top 6 IGF Global Themes Evolution', fontsize=16, fontweight='bold')
    plt.xticks(all_years, rotation=45)
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.tight_layout()
    plt.savefig('igf_trend_1_overall_themes.png', dpi=300)
    plt.close()


    org_theme_df = df[['Year', 'Organizations_List', 'Themes_List']].explode('Organizations_List').reset_index(
        drop=True)
    org_theme_df = org_theme_df.explode('Themes_List').reset_index(drop=True)
    org_theme_df = org_theme_df[
        (org_theme_df['Organizations_List'] != '') & (org_theme_df['Themes_List'] != '')].dropna()

    top_4_orgs = org_theme_df['Organizations_List'].value_counts().head(4).index

    fig, axes = plt.subplots(2, 2, figsize=(16, 12), sharex=True)
    axes = axes.flatten()

    for i, org in enumerate(top_4_orgs):
        org_data = org_theme_df[org_theme_df['Organizations_List'] == org]
        org_top_themes = org_data['Themes_List'].value_counts().head(4).index
        org_trend = org_data[org_data['Themes_List'].isin(org_top_themes)]

        pivot = pd.crosstab(org_trend['Year'], org_trend['Themes_List']).reindex(all_years, fill_value=0)

        for theme in org_top_themes:
            axes[i].plot(pivot.index, pivot[theme], marker='s', linewidth=1.5, label=theme)

        axes[i].set_title(f'Focus Themes: {org}', fontweight='bold', fontsize=13)
        axes[i].set_xticks(all_years)
        axes[i].tick_params(axis='x', rotation=45)
        axes[i].legend(fontsize=9, loc='upper left')

    plt.suptitle('Topic Shifts Across Top 4 Organizations', fontsize=18, fontweight='bold')
    plt.tight_layout()
    plt.savefig('igf_trend_2_org_themes.png', dpi=300)
    plt.close()


    spk_org_df = df[['Year', 'Speakers_List', 'Organizations_List']].explode('Speakers_List').reset_index(drop=True)
    spk_org_df = spk_org_df.explode('Organizations_List').reset_index(drop=True)
    spk_org_df = spk_org_df[(spk_org_df['Speakers_List'] != '') & (spk_org_df['Organizations_List'] != '')].dropna()

    top_speakers = spk_org_df['Speakers_List'].value_counts().head(8).index
    spk_trend = spk_org_df[spk_org_df['Speakers_List'].isin(top_speakers)]

    yearly_spk_org = spk_trend.groupby(['Year', 'Speakers_List', 'Organizations_List']).size().reset_index(
        name='Session_Count')

    primary_orgs = yearly_spk_org.sort_values('Session_Count', ascending=False).drop_duplicates(
        ['Year', 'Speakers_List'])

    plt.figure(figsize=(15, 7))

    sns.scatterplot(
        data=primary_orgs,
        x='Year',
        y='Speakers_List',
        hue='Organizations_List',
        s=250,
        marker='s',
        palette='tab20',
        edgecolor='black',
        linewidth=0.5
    )

    plt.title('Core Speakers: Shifts in Represented Organizations Over Time', fontsize=16, fontweight='bold')
    plt.xticks(all_years, rotation=45)
    plt.ylabel('Core Speakers', fontsize=12)
    plt.xlabel('Year', fontsize=12)

    plt.grid(True, axis='y', linestyle='--', alpha=0.7)


    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', title='Primary Organization (By Year)')
    plt.tight_layout()
    plt.savefig('igf_trend_3_speaker_orgs.png', dpi=300)
    plt.close()




if __name__ == "__main__":
    CSV_FILE_PATH = "igf_historical_data_deep_clean_v2.csv"
    visualize_advanced_trends(CSV_FILE_PATH)