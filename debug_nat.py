import pandas as pd
import numpy as np

# Test NaT boolean evaluation
nat_val = pd.NaT
print(f"pd.NaT: {repr(nat_val)}")
print(f"bool(pd.NaT): {bool(nat_val)}")

# Test None boolean evaluation
none_val = None
print(f"None: {repr(none_val)}")
print(f"bool(None): {bool(none_val)}")

# Test what happens in our DataFrame
df = pd.DataFrame([{"date_posted": None}])
print(f"\nDataFrame with None:")
print(df)
print(f"df.iloc[0]['date_posted']: {repr(df.iloc[0]['date_posted'])}")
print(f"bool(df.iloc[0]['date_posted']): {bool(df.iloc[0]['date_posted'])}")

# After _prepare_frame processing
from app import _prepare_frame
result = _prepare_frame(df)
print(f"\nAfter _prepare_frame:")
print(result)
print(f"result.iloc[0]['date_posted']: {repr(result.iloc[0]['date_posted'])}")
print(f"bool(result.iloc[0]['date_posted']): {bool(result.iloc[0]['date_posted'])}")