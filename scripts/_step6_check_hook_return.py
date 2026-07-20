from pathlib import Path

# Verify use-ask exports faqs in return object
ut = Path("frontend/src/lib/hooks/use-ask.ts").read_text(encoding="utf-8")
print("=== use-ask return ===")
for i, line in enumerate(ut.splitlines(), 1):
    if "return" in line or "faqs" in line or "procedureDetail" in line or "recommendedForms" in line:
        print(f"{i}: {line}")

# Ensure faqs is spread from state
if "faqs:" not in ut and "faqs," not in ut:
    print("WARN: faqs may not be returned from hook")
else:
    print("faqs present in hook")

# Check return statement near end
lines = ut.splitlines()
for i, line in enumerate(lines, 1):
    if line.strip().startswith("return {") or line.strip() == "return {":
        for j in range(i, min(i+40, len(lines)+1)):
            print(f"{j}: {lines[j-1]}")
        break
