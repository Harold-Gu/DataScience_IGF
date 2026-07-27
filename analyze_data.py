import pandas as pd
import re
import matplotlib.pyplot as plt
import seaborn as sns
from wordcloud import WordCloud, STOPWORDS
import os


# ==========================================
# 模块一：数据清洗与规范化 Pipeline
# ==========================================
def clean_igf_data(input_csv: str, output_csv: str) -> pd.DataFrame:
    """
    IGF 历史数据深度清洗管道
    """
    print(f"🚀 开始读取并清洗数据: {input_csv} ...")
    # 1. 读取数据，将全部为空的列或全是 N/A 的值转化为真正的 NaN
    df = pd.read_csv(input_csv, na_values=['N/A', '<null>', '', 'Unknown'])

    # 2. 缺失值处理
    # 丢弃没有年份或没有标题的严重残缺数据
    df.dropna(subset=['Year', 'Title'], inplace=True)
    # 填充非核心字段
    df['Speakers_Raw'] = df['Speakers_Raw'].fillna('Not_Specified')
    df['Themes'] = df['Themes'].fillna('Uncategorized')
    df['Description'] = df['Description'].fillna('No description available.')

    # 3. 数据类型转换 (确保 Year 是整型)
    df['Year'] = df['Year'].astype(int)

    # 4. 文本规范化与实体统一 (Entity Resolution)
    # 团队协作点：可让组员根据后续挖掘需求，继续扩充此字典
    synonym_map = {
        'ai': 'artificial intelligence',
        'machine learning': 'artificial intelligence',
        'cyber security': 'cybersecurity',
        'data protection': 'data privacy',
        'iot': 'internet of things'
    }

    def normalize_list_string(text: str) -> list:
        if text in ['Not_Specified', 'Uncategorized']:
            return []

        # 按 '|' 分割，去除两端空格，转为小写
        items = [item.strip().lower() for item in text.split('|')]

        # 映射同义词并去重
        normalized_items = []
        for item in items:
            item = re.sub(r'\s+', ' ', item)  # 替换连续空格
            item = synonym_map.get(item, item)  # 查字典映射
            normalized_items.append(item.title())  # 转为首字母大写

        return list(set(normalized_items))

    # 应用规范化，将字符串转化为 Python List 格式的字符串表达式 (方便存入 CSV)
    df['Speakers_List'] = df['Speakers_Raw'].apply(normalize_list_string)
    df['Themes_List'] = df['Themes'].apply(normalize_list_string)

    # 按年份升序排序
    df.sort_values(by='Year', inplace=True)
    df.reset_index(drop=True, inplace=True)

    # 导出清洗后的数据用于后续 NLP/模型训练
    df.to_csv(output_csv, index=False, encoding='utf-8-sig')
    print(f"✅ 数据清洗完成！已保存为: {output_csv}")

    return df


# ==========================================
# 模块二：探索性数据分析与学术出图
# ==========================================
class IGFAcademicEDA:
    def __init__(self, df: pd.DataFrame, output_dir: str = "."):
        self.df = df
        self.output_dir = output_dir

        # 设置学术论文常用的绘图风格与高清分辨率
        plt.style.use('seaborn-v0_8-whitegrid')
        plt.rcParams['figure.dpi'] = 300
        plt.rcParams['font.family'] = 'serif'

    def plot_yearly_trend(self):
        """1. 历年会议数量趋势曲线"""
        print("📊 正在生成历年趋势图...")
        plt.figure(figsize=(10, 5))
        yearly_counts = self.df.groupby('Year').size()

        sns.lineplot(x=yearly_counts.index, y=yearly_counts.values,
                     marker='o', linewidth=2.5, color='#2c3e50')

        plt.title('IGF Sessions Trend', fontsize=14, fontweight='bold')
        plt.xlabel('Year', fontsize=12)
        plt.ylabel('Number of Sessions', fontsize=12)

        # 确保 x 轴只显示整数年份
        plt.xticks(yearly_counts.index, rotation=45)
        plt.tight_layout()

        out_path = os.path.join(self.output_dir, 'fig_1_yearly_trend.png')
        plt.savefig(out_path)
        plt.close()

    def plot_session_types(self):
        """2. 会议类型占比比例分布"""
        print("📊 正在生成会议类型饼图...")
        plt.figure(figsize=(8, 8))

        type_counts = self.df['Session_Type'].value_counts()
        threshold = type_counts.sum() * 0.02  # 低于2%的归为Others
        mask = type_counts > threshold
        tail = type_counts.loc[~mask]

        plot_data = type_counts.loc[mask].copy()
        if not tail.empty:
            plot_data['Others'] = tail.sum()

        colors = sns.color_palette('pastel')[0:len(plot_data)]
        plt.pie(plot_data, labels=plot_data.index, autopct='%1.1f%%',
                colors=colors, startangle=140, textprops={'fontsize': 11})

        plt.title('Distribution of IGF Session Types', fontsize=14, fontweight='bold')
        plt.tight_layout()

        out_path = os.path.join(self.output_dir, 'fig_2_session_types.png')
        plt.savefig(out_path)
        plt.close()

    def plot_top_speakers(self, top_n=15):
        """3. 高频嘉宾统计"""
        print(f"📊 正在生成 Top {top_n} 演讲者柱状图...")
        # 展开列表进行统计
        speakers_exploded = self.df.explode('Speakers_List').dropna(subset=['Speakers_List'])
        # 过滤掉空列表产生的 NaN
        speakers_exploded = speakers_exploded[speakers_exploded['Speakers_List'] != '']
        speaker_counts = speakers_exploded['Speakers_List'].value_counts().head(top_n)

        plt.figure(figsize=(10, 6))
        sns.barplot(x=speaker_counts.values, y=speaker_counts.index, palette='viridis', hue=speaker_counts.index,
                    legend=False)

        plt.title(f'Top {top_n} Most Active Speakers at IGF', fontsize=14, fontweight='bold')
        plt.xlabel('Number of Sessions Attended', fontsize=12)
        plt.ylabel('Speaker Name', fontsize=12)
        plt.tight_layout()

        out_path = os.path.join(self.output_dir, 'fig_3_top_speakers.png')
        plt.savefig(out_path)
        plt.close()

    def generate_wordcloud(self):
        """4. 核心描述文本的词云分析"""
        print("📊 正在生成描述文本词云...")
        text_corpus = " ".join(self.df['Description'].astype(str).tolist())

        # 增加领域特定的停用词
        custom_stopwords = set(STOPWORDS)
        custom_stopwords.update(
            ['session', 'discussion', 'panel', 'will', 'participant', 'discuss', 'igf', 'workshop', 'speakers'])

        wordcloud = WordCloud(width=1200, height=600,
                              background_color='white',
                              colormap='inferno',
                              stopwords=custom_stopwords,
                              max_words=150).generate(text_corpus)

        plt.figure(figsize=(12, 6))
        plt.imshow(wordcloud, interpolation='bilinear')
        plt.axis('off')
        plt.title('High-Frequency Keywords in IGF Session Descriptions', fontsize=14, fontweight='bold')
        plt.tight_layout()

        out_path = os.path.join(self.output_dir, 'fig_4_wordcloud.png')
        plt.savefig(out_path)
        plt.close()


# ==========================================
# 主执行入口
# ==========================================
if __name__ == "__main__":
    # 根据你的截图，输入文件就是当前目录下的 igf_historical_data_clean_v3.csv
    INPUT_CSV = "igf_historical_data_clean_v3.csv"
    # 清洗后生成的新数据集名称
    OUTPUT_CSV = "igf_paper_ready_data.csv"

    # 1. 运行数据清洗
    if os.path.exists(INPUT_CSV):
        df_clean = clean_igf_data(INPUT_CSV, OUTPUT_CSV)

        # 2. 运行学术出图
        print("\n🚀 开始生成分析图表...")
        eda = IGFAcademicEDA(df_clean)

        eda.plot_yearly_trend()
        eda.plot_session_types()
        eda.plot_top_speakers()
        eda.generate_wordcloud()

        print("\n🎉 全部任务完成！请检查当前目录下的 PNG 图片文件与清洗后的 CSV 文件。")
    else:
        print(f"❌ 找不到输入文件：{INPUT_CSV}，请确保该文件在当前目录下。")