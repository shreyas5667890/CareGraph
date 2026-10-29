import pandas as pd
from pathlib import Path

CSV_PATH = Path("Symptom_Weights.csv")


def main():
    if not CSV_PATH.exists():
        print(f"ERROR: File not found: {CSV_PATH.resolve()}")
        return

    # The CSV does not have a header row.
    df = pd.read_csv(
        CSV_PATH,
        header=None,
        names=["symptom", "symptom_id"]
    )

    # Clean the values.
    df["symptom"] = df["symptom"].astype(str).str.strip().str.lower()
    df["symptom_id"] = pd.to_numeric(
        df["symptom_id"], errors="coerce"
    )

    print("=" * 70)
    print("SYMPTOM DATASET INSPECTION")
    print("=" * 70)

    print(f"\nTotal rows: {len(df)}")
    print(f"Unique symptoms: {df['symptom'].nunique()}")
    print(f"Missing values:\n{df.isnull().sum()}")

    print("\nFirst 10 symptoms:")
    print(df.head(10).to_string(index=False))

    print("\nDuplicate symptom names:")
    duplicates = df[df.duplicated("symptom", keep=False)]

    if duplicates.empty:
        print("None")
    else:
        print(duplicates.to_string(index=False))

    print("\nSymptom vocabulary:")
    for symptom in sorted(df["symptom"].dropna().unique()):
        print(f"  - {symptom}")

    # Save a clean vocabulary for the normalization stage.
    vocabulary = sorted(df["symptom"].dropna().unique())

    output_path = Path("symptom_vocabulary.csv")
    pd.DataFrame({"symptom": vocabulary}).to_csv(
        output_path, index=False
    )

    print(f"\nVocabulary saved to: {output_path.resolve()}")
    print("\nInspection complete.")


if __name__ == "__main__":
    main()
