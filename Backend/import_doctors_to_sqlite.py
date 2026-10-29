"""Rebuild caregraph_doctors.db from the supplied Excel directory.
Run: python Backend/import_doctors_to_sqlite.py
"""
from pathlib import Path
import sqlite3
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "Data" / "CareGraph_Doctor_Directory_Mumbai_Thane_Synthetic.xlsx"
DB = ROOT / "Data" / "caregraph_doctors.db"
df = pd.read_excel(XLSX, sheet_name="Doctors")
required = {
    "Doctor_ID", "Doctor_Name", "Specialization", "Age", "Experience_Years",
    "Description", "Address", "City", "State", "Latitude", "Longitude",
    "Consultation_Fee_INR", "Rating_Out_of_5", "Data_Status",
}
missing = required - set(df.columns)
if missing:
    raise ValueError(f"Missing required columns: {sorted(missing)}")
DB.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(DB) as con:
    df.to_sql("doctors", con, if_exists="replace", index=False)
    con.execute("CREATE INDEX IF NOT EXISTS idx_doctors_specialization ON doctors(Specialization)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_doctors_city ON doctors(City)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_doctors_specialty_city ON doctors(Specialization, City)")
print(f"Imported {len(df)} doctor records into {DB}")
