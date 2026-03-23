import pandas as pd

try:
    dfs = pd.read_html("tmp_ezmoney.html")
    print(f"Found {len(dfs)} tables.")
    for i, df in enumerate(dfs):
        print(f"Table {i} shape: {df.shape}")
        if not df.empty and df.shape[1] > 2:
            print(df.head(3))
except Exception as e:
    print("Error:", e)
