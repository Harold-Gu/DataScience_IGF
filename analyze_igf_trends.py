import pandas as pd
import os


def export_all_frequencies(csv_path: str):
    print(f"📂 正在加载主数据文件: {csv_path}...\n")
    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"❌ 找不到文件 {csv_path}，请确保路径正确。")
        return

    # 1. 数据预处理
    print("⏳ 正在清洗与拆解多值字段...")
    df['Year'] = pd.to_numeric(df['Year'], errors='coerce')

    # 包含了 V2 脚本中可能新增的 Keywords 列
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

    print("-" * 50)
    print("🚀 开始统计全量数据并生成独立报表，请稍候...\n")

    # ==========================================
    # 任务 1：全量主题频次 (All Themes)
    # ==========================================
    all_themes = df.explode('Themes_List')['Themes_List'].dropna()
    all_themes = all_themes[all_themes != '']
    theme_counts = all_themes.value_counts().reset_index()
    theme_counts.columns = ['Theme', 'Frequency']

    theme_csv = 'igf_freq_all_themes.csv'
    theme_counts.to_csv(theme_csv, index=False, encoding='utf-8-sig')
    print(f"✅ 全量主题频次已保存至 -> {theme_csv} (共 {len(theme_counts)} 个独立主题)")

    # ==========================================
    # 任务 2：全量组织机构频次 (All Organizations)
    # ==========================================
    all_orgs = df.explode('Organizations_List')['Organizations_List'].dropna()
    all_orgs = all_orgs[all_orgs != '']
    org_counts = all_orgs.value_counts().reset_index()
    org_counts.columns = ['Organization', 'Frequency']

    org_csv = 'igf_freq_all_organizations.csv'
    org_counts.to_csv(org_csv, index=False, encoding='utf-8-sig')
    print(f"✅ 全量组织频次已保存至 -> {org_csv} (共 {len(org_counts)} 个独立组织)")

    # ==========================================
    # 任务 3：全量发言人及最常关联组织 (All Speakers)
    # ==========================================
    print("⏳ 正在深度计算所有发言人的组织共现关系 (可能需要几秒钟)...")
    speakers_df = df.explode('Speakers_List').dropna(subset=['Speakers_List'])
    speakers_df = speakers_df[speakers_df['Speakers_List'] != '']

    speaker_counts = speakers_df['Speakers_List'].value_counts()

    speaker_records = []
    for speaker, count in speaker_counts.items():
        # 提取该发言人参与的所有会议
        speaker_sessions = speakers_df[speakers_df['Speakers_List'] == speaker]
        # 统计他在这些会议中同时出现的组织
        associated_orgs = speaker_sessions.explode('Organizations_List')['Organizations_List'].dropna()
        associated_orgs = associated_orgs[associated_orgs != '']

        top_org = "未知 (Unknown)"
        if not associated_orgs.empty:
            top_org = associated_orgs.value_counts().index[0]  # 取共现次数最高的组织

        speaker_records.append({
            'Speaker': speaker,
            'Frequency': count,
            'Top_Associated_Organization': top_org
        })

    speaker_freq_df = pd.DataFrame(speaker_records)
    speaker_csv = 'igf_freq_all_speakers.csv'
    speaker_freq_df.to_csv(speaker_csv, index=False, encoding='utf-8-sig')
    print(f"✅ 全量发言人频次已保存至 -> {speaker_csv} (共 {len(speaker_freq_df)} 位发言人)")

    # ==========================================
    # 任务 4：(可选) 全量专有名词/关键词 (All Keywords)
    # ==========================================
    if 'Keywords' in df.columns:
        all_keywords = df.explode('Keywords_List')['Keywords_List'].dropna()
        all_keywords = all_keywords[all_keywords != '']
        keyword_counts = all_keywords.value_counts().reset_index()
        keyword_counts.columns = ['Keyword', 'Frequency']

        keyword_csv = 'igf_freq_all_keywords.csv'
        keyword_counts.to_csv(keyword_csv, index=False, encoding='utf-8-sig')
        print(f"✅ 全量关键词频次已保存至 -> {keyword_csv} (共 {len(keyword_counts)} 个关键词)")

    print("-" * 50)
    print("🎉 所有全量数据提取完毕！请在左侧项目目录中查看生成的四个 CSV 文件。")


if __name__ == "__main__":
    # 指向大模型清洗出来的源文件
    CSV_FILE_PATH = "igf_historical_data_deep_clean_v2.csv"
    export_all_frequencies(CSV_FILE_PATH)