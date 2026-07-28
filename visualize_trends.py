import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

# 设置图表风格与字体（确保兼容性）
sns.set_theme(style="whitegrid")
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial', 'DejaVu Sans']  # 兼容中英文字体
plt.rcParams['axes.unicode_minus'] = False


def generate_trend_charts(csv_path: str):
    print(f"📂 正在加载数据以生成趋势图表: {csv_path}...\n")
    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"❌ 找不到文件 {csv_path}。请确认路径。")
        return

    # 1. 基础数据清洗
    df['Year'] = pd.to_numeric(df['Year'], errors='coerce')
    df = df.dropna(subset=['Year'])
    df['Year'] = df['Year'].astype(int)

    # 拆解多值字段
    for col in ['Themes', 'Speakers']:
        if col in df.columns:
            df[col + '_List'] = df[col].fillna('').astype(str).apply(
                lambda x: [
                    item.strip() for item in str(x).split('|')
                    if item.strip() and item.strip().upper() not in ['N/A', 'NAN', 'NONE']
                ]
            )
        else:
            print(f"⚠️ 缺失必要的列: {col}")
            return

    # ==========================================
    # 图表 1：历年最热主题演变趋势 (Top 6)
    # ==========================================
    print("📈 正在计算主题时间序列并生成图表...")

    # 【修复点】：增加 .reset_index(drop=True) 清洗掉由于 explode 产生的重复索引
    themes_df = df.explode('Themes_List').dropna(subset=['Themes_List']).reset_index(drop=True)
    themes_df = themes_df[themes_df['Themes_List'] != '']

    # 找出全局 Top 6 主题
    top_themes = themes_df['Themes_List'].value_counts().head(6).index.tolist()

    # 过滤出只包含 Top 6 主题的数据，并按年份聚合
    themes_trend = themes_df[themes_df['Themes_List'].isin(top_themes)]
    theme_pivot = pd.crosstab(themes_trend['Year'], themes_trend['Themes_List'])

    # 补全可能缺失的年份
    all_years = range(df['Year'].min(), df['Year'].max() + 1)
    theme_pivot = theme_pivot.reindex(all_years, fill_value=0)

    # 绘图
    plt.figure(figsize=(12, 6))
    for theme in top_themes:
        plt.plot(theme_pivot.index, theme_pivot[theme], marker='o', linewidth=2, label=theme)

    plt.title('Top 6 IGF Core Themes Evolution Over Time', fontsize=16, fontweight='bold')
    plt.xlabel('Year', fontsize=12)
    plt.ylabel('Frequency of Discussion', fontsize=12)
    plt.xticks(all_years, rotation=45)
    plt.legend(title='Themes', bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.tight_layout()
    plt.savefig('igf_theme_trends.png', dpi=300)
    plt.close()
    print("✅ 主题趋势图已保存为 -> igf_theme_trends.png")

    # ==========================================
    # 图表 2：头部发言人历年活跃度趋势 (Top 8)
    # ==========================================
    print("📈 正在计算头部发言人时间序列并生成图表...")

    # 【修复点】：增加 .reset_index(drop=True) 清洗掉由于 explode 产生的重复索引
    speakers_df = df.explode('Speakers_List').dropna(subset=['Speakers_List']).reset_index(drop=True)
    speakers_df = speakers_df[speakers_df['Speakers_List'] != '']

    # 找出全局 Top 8 发言人
    top_speakers = speakers_df['Speakers_List'].value_counts().head(8).index.tolist()

    # 过滤出只包含 Top 8 发言人的数据，并按年份聚合
    speakers_trend = speakers_df[speakers_df['Speakers_List'].isin(top_speakers)]
    speaker_pivot = pd.crosstab(speakers_trend['Year'], speakers_trend['Speakers_List'])

    # 补全缺失年份
    speaker_pivot = speaker_pivot.reindex(all_years, fill_value=0)

    # 绘图
    plt.figure(figsize=(12, 6))
    for speaker in top_speakers:
        plt.plot(speaker_pivot.index, speaker_pivot[speaker], marker='s', linestyle='--', linewidth=1.5, label=speaker)

    plt.title('Top 8 Most Active Speakers Across Years', fontsize=16, fontweight='bold')
    plt.xlabel('Year', fontsize=12)
    plt.ylabel('Number of Sessions Attended', fontsize=12)
    plt.xticks(all_years, rotation=45)

    plt.yticks(np.arange(0, speaker_pivot.max().max() + 2, step=1))

    plt.legend(title='Speakers', bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.tight_layout()
    plt.savefig('igf_speaker_trends.png', dpi=300)
    plt.close()
    print("✅ 发言人活跃度趋势图已保存为 -> igf_speaker_trends.png")


if __name__ == "__main__":
    CSV_FILE_PATH = "igf_historical_data_deep_clean_v2.csv"
    generate_trend_charts(CSV_FILE_PATH)