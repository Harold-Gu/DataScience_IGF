"""
IGF Yearly Top Organizations Analysis (Refactored)
Ranks top-N organizations per year and generates a global activity heatmap.

Uses igf_common for shared utilities.
"""
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from igf_common import parse_multi_value_col

sns.set_theme(style="whitegrid")
plt.rcParams["font.sans-serif"] = ["SimHei", "Arial", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def show_yearly_top_organizations(csv_path: str, top_n: int = 3):
    """Rank top-N organizations per year, export CSV, generate heatmap."""
    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"Error: {csv_path} not found.")
        return

    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df = df.dropna(subset=["Year"])
    df["Year"] = df["Year"].astype(int)

    df["Organizations_List"] = parse_multi_value_col(df["Organizations"])

    org_df = df[["Year", "Organizations_List"]].explode("Organizations_List").dropna().reset_index(drop=True)
    org_df = org_df[org_df["Organizations_List"] != ""]

    yearly_counts = org_df.groupby(["Year", "Organizations_List"]).size().reset_index(name="Frequency")
    yearly_counts["Rank"] = yearly_counts.groupby("Year")["Frequency"].rank(method="first", ascending=False)
    top_yearly = yearly_counts[yearly_counts["Rank"] <= top_n].sort_values(["Year", "Rank"])

    # Print yearly top orgs
    for year, group in top_yearly.groupby("Year"):
        org_details = []
        for _, row in group.iterrows():
            org_details.append(f"No.{int(row['Rank'])} {row['Organizations_List']} ({row['Frequency']})")
        print(f"{year}  " + "  |  ".join(org_details))

    # Export CSV
    export_df = top_yearly[["Year", "Rank", "Organizations_List", "Frequency"]].copy()
    export_df.columns = ["Year", "Rank", "Organization", "Session_Count"]
    output_csv = "igf_yearly_top_organizations.csv"
    export_df.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print(f"\nTop {top_n} per year saved to: {output_csv}")

    # Global top-12 heatmap
    top_global_orgs = org_df["Organizations_List"].value_counts().head(12).index.tolist()
    matrix_df = org_df[org_df["Organizations_List"].isin(top_global_orgs)]
    pivot_table = pd.crosstab(matrix_df["Organizations_List"], matrix_df["Year"])

    all_years = range(df["Year"].min(), df["Year"].max() + 1)
    pivot_table = pivot_table.reindex(columns=all_years, fill_value=0)

    plt.figure(figsize=(14, 8))
    sns.heatmap(
        pivot_table, cmap="YlGnBu", annot=True, fmt="d",
        linewidths=0.5, cbar_kws={"label": "Frequency"},
    )
    plt.title("Top Global Organizations Activity Heatmap Across Years", fontsize=16, fontweight="bold")
    plt.xlabel("Year", fontsize=12)
    plt.ylabel("Organizations", fontsize=12)
    plt.xticks(rotation=45)
    plt.tight_layout()

    heatmap_png = "igf_yearly_orgs_heatmap.png"
    plt.savefig(heatmap_png, dpi=300)
    plt.close()
    print(f"Heatmap saved: {heatmap_png}")


if __name__ == "__main__":
    CSV_FILE_PATH = "igf_historical_data_deep_clean_v2.csv"
    show_yearly_top_organizations(CSV_FILE_PATH, top_n=3)
