import pandas as pd


def analyze_igf_data(csv_path: str):
    print(f"📂 正在加载数据文件: {csv_path}...\n")
    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"❌ 找不到文件 {csv_path}，请确保路径正确。")
        return

    # 1. 数据预处理
    df['Year'] = pd.to_numeric(df['Year'], errors='coerce')

    target_cols = ['Themes', 'Speakers', 'Organizations']

    for col in target_cols:
        if col in df.columns:
            # 防御性处理：先充填空值 .fillna('')，并在 lambda 内用 str(x) 确保类型安全
            df[col + '_List'] = df[col].fillna('').astype(str).apply(
                lambda x: [
                    item.strip() for item in str(x).split('|')
                    if item.strip() and item.strip().upper() not in ['N/A', 'NAN', 'NONE']
                ]
            )
        else:
            print(f"⚠️ 警告: 未找到列 '{col}'，已填充空列表。")
            df[col + '_List'] = [[] for _ in range(len(df))]

    print("-" * 50)

    # ==========================================
    # 任务 1：全局主题频次 (Top 15)
    # ==========================================
    print("🏆 【全局最热主题 Top 15】")
    all_themes = df.explode('Themes_List')['Themes_List'].dropna()
    all_themes = all_themes[all_themes != '']
    top_themes = all_themes.value_counts().head(15)

    if top_themes.empty:
        print("  (暂无有效主题数据，请检查 CSV 内容)")
    else:
        for i, (theme, count) in enumerate(top_themes.items(), 1):
            print(f"{i:2d}. {theme:<35} (出现 {count} 次)")
    print("-" * 50)

    # ==========================================
    # 任务 2：每年最热主题 (Top 3)
    # ==========================================
    print("📅 【历年最热主题演变 (Top 3)】")
    yearly_themes_df = df.explode('Themes_List').dropna(subset=['Themes_List', 'Year'])
    yearly_themes_df = yearly_themes_df[yearly_themes_df['Themes_List'] != '']

    if not yearly_themes_df.empty:
        yearly_themes_df['Year'] = yearly_themes_df['Year'].astype(int)
        yearly_counts = yearly_themes_df.groupby(['Year', 'Themes_List']).size().reset_index(name='Count')
        top_yearly = yearly_counts.sort_values(['Year', 'Count'], ascending=[True, False]).groupby('Year').head(3)

        for year, group in top_yearly.groupby('Year'):
            themes_str = ", ".join([f"{row['Themes_List']}({row['Count']})" for _, row in group.iterrows()])
            print(f"[{year}] {themes_str}")
    else:
        print("  (暂无按年份的有效主题数据)")
    print("-" * 50)

    # ==========================================
    # 任务 3：最常发言的发言人及其最高频关联组织 (Top 15)
    # ==========================================
    print("🎤 【最活跃发言人 Top 15 及其关联组织】")
    speakers_df = df.explode('Speakers_List').dropna(subset=['Speakers_List'])
    speakers_df = speakers_df[speakers_df['Speakers_List'] != '']
    top_speakers = speakers_df['Speakers_List'].value_counts().head(15)

    if top_speakers.empty:
        print("  (暂无有效发言人数据)")
    else:
        print(
            f"{'排名':<4} | {'发言人 (Speaker)':<30} | {'会议数':<6} | {'最常关联组织 (Top Associated Organization)'}")
        print("-" * 90)

        for i, (speaker, count) in enumerate(top_speakers.items(), 1):
            speaker_sessions = speakers_df[speakers_df['Speakers_List'] == speaker]
            associated_orgs = speaker_sessions.explode('Organizations_List')['Organizations_List'].dropna()
            associated_orgs = associated_orgs[associated_orgs != '']

            if not associated_orgs.empty:
                top_org = associated_orgs.value_counts().index[0]
                org_count = associated_orgs.value_counts().iloc[0]
                org_display = f"{top_org} (共现 {org_count} 次)"
            else:
                org_display = "未知 (Unknown)"

            print(f"{i:<4} | {speaker:<30} | {count:<6} | {org_display}")
        print("-" * 90)
    print("✅ 统计分析完成！")


if __name__ == "__main__":
    CSV_FILE_PATH = "igf_historical_data_deep_clean_v2.csv"
    analyze_igf_data(CSV_FILE_PATH)