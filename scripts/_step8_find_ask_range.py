from pathlib import Path
import re

p = Path("scripts/e2e_step8_regression.py")
text = p.read_text(encoding="utf-8")

# Find ask function by regex (from def ask to next def or end of try block)
# Use a simpler approach: find the function and replace everything until the next top-level def
lines = text.split("\n")
ask_start = None
ask_end = None
for i, line in enumerate(lines):
    if line.startswith("def ask(client"):
        ask_start = i
    elif ask_start is not None and i > ask_start and (line.startswith("def ") or line.startswith("class ")):
        ask_end = i
        break

if ask_start is None:
    print("ERROR: could not find ask function")
elif ask_end is None:
    print(f"ERROR: ask function starts at {ask_start} but no end found")
else:
    print(f"Found ask function: lines {ask_start+1} to {ask_end}")
    # Print the range
    for i in range(ask_start, min(ask_end+5, len(lines))):
        print(f"{i+1}: {lines[i][:80]}")
