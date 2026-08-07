"""
IGF Data Cleaning & EDA (Refactored)
Cleans raw IGF CSV data (synonym normalization, deduplication) and
generates 3 exploratory visualization plots.

Uses igf_common for shared utilities.
"""
import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from igf_common import normalize_list_string


def clean_igf_data(input_csv: str, output_csv: str) -> pd.DataFrame:
    """Load raw IGF CSV, clean/fill missing values, normalize lists, and save."""
    print(f"Cleaning data: {input_csv} ...")

    df = pd.read_csv(input_csv, na_values=["N/A", "<null>", "", "Unknown"])

    df.dropna(subset=["Year", "Title"], inplace=True)

    df["Speakers_Raw"] = df["Speakers_Raw"].fillna("Not_Specified")
    df["Themes"] = df["Themes"].fillna("Uncategorized")
    df["Description"] = df["Description"].fillna("No description available.")

    df["Year"] = df["Year"].astype(int)

    df["Speakers_List"] = df["Speakers_Raw"].apply(normalize_list_string)
    df["Themes_List"] = df["Themes"].apply(normalize_list_string)

    df.sort_values(by="Year", inplace=True)
    df.reset_index(drop=True, inplace=True)

    df.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print(f"Cleaned data saved to: {output_csv}")
    return df


class IGFAcademicEDA:
    """Generate academic-style EDA plots for IGF data."""

    def __init__(self, df: pd.DataFrame, output_dir: str = "."):
        self.df = df
        self.output_dir = output_dir

        plt.style.use("seaborn-v0_8-whitegrid")
        plt.rcParams["figure.dpi"] = 300
        plt.rcParams["font.family"] = "serif"

    def plot_yearly_trend(self):
        """Line plot: number of sessions per year."""
        plt.figure(figsize=(10, 5))
        yearly_counts = self.df.groupby("Year").size()
        sns.lineplot(
            x=yearly_counts.index, y=yearly_counts.values,
            marker="o", linewidth=2.5, color="#2c3e50",
        )
        plt.title("IGF Sessions Trend", fontsize=14)
        plt.xlabel("Year", fontsize=12)
        plt.ylabel("Number of Sessions", fontsize=12)
        plt.xticks(yearly_counts.index, rotation=45)
        plt.tight_layout()

        out_path = os.path.join(self.output_dir, "fig_1_yearly_trend.png")
        plt.savefig(out_path)
        plt.close()

    def plot_session_types(self):
        """Pie chart: distribution of session types (grouping <2% into 'Others')."""
        plt.figure(figsize=(8, 8))
        type_counts = self.df["Session_Type"].value_counts()
        threshold = type_counts.sum() * 0.02
        mask = type_counts > threshold
        tail = type_counts.loc[~mask]

        plot_data = type_counts.loc[mask].copy()
        if not tail.empty:
            plot_data["Others"] = tail.sum()

        colors = sns.color_palette("pastel")[0:len(plot_data)]
        plt.pie(
            plot_data, labels=plot_data.index, autopct="%1.1f%%",
            colors=colors, startangle=140, textprops={"fontsize": 11},
        )
        plt.title("Distribution of IGF Session Types", fontsize=14, fontweight="bold")
        plt.tight_layout()

        out_path = os.path.join(self.output_dir, "fig_2_session_types.png")
        plt.savefig(out_path)
        plt.close()

    def plot_top_speakers(self, top_n: int = 15):
        """Horizontal bar chart: most active speakers."""
        speakers_exploded = self.df.explode("Speakers_List").dropna(subset=["Speakers_List"])
        speakers_exploded = speakers_exploded[speakers_exploded["Speakers_List"] != ""]
        speaker_counts = speakers_exploded["Speakers_List"].value_counts().head(top_n)

        plt.figure(figsize=(10, 6))
        sns.barplot(
            x=speaker_counts.values, y=speaker_counts.index,
            palette="viridis", hue=speaker_counts.index, legend=False,
        )
        plt.title(f"Top {top_n} Most Active Speakers at IGF", fontsize=14, fontweight="bold")
        plt.xlabel("Number of Sessions Attended", fontsize=12)
        plt.ylabel("Speaker Name", fontsize=12)
        plt.tight_layout()

        out_path = os.path.join(self.output_dir, "fig_3_top_speakers.png")
        plt.savefig(out_path)
        plt.close()


if __name__ == "__main__":
    INPUT_CSV = "igf_historical_data_clean_v3.csv"
    OUTPUT_CSV = "igf_paper_ready_data.csv"

    if os.path.exists(INPUT_CSV):
        df_clean = clean_igf_data(INPUT_CSV, OUTPUT_CSV)
        eda = IGFAcademicEDA(df_clean)
        eda.plot_yearly_trend()
        eda.plot_session_types()
        eda.plot_top_speakers()
