from pathlib import Path

# Update frontend types
tp = Path("frontend/src/lib/types/search.ts")
tt = tp.read_text(encoding="utf-8")
if "faqs?" not in tt:
    # insert after procedure_summary or recommended_forms
    if "procedure_summary?: string" in tt:
        tt = tt.replace(
            "procedure_summary?: string",
            "procedure_summary?: string\n  faqs?: Array<{\n    id: string\n    question: string\n    answer: string\n    steps?: string[]\n    form_ids?: string[]\n    domain?: string\n    ward_scope?: string | null\n    review_status?: string\n  }>",
            1,
        )
        print("OK: types search.ts faqs")
    else:
        print("WARN: procedure_summary not found")
else:
    print("SKIP: types already has faqs")
tp.write_text(tt, encoding="utf-8", newline="\n")

# Update use-ask to pass faqs
hook = Path("frontend/src/lib/hooks/use-ask.ts")
ht = hook.read_text(encoding="utf-8")
print("use-ask has procedureDetail", "procedureDetail" in ht)
print("use-ask has faqs", "faqs" in ht)
for i, line in enumerate(ht.splitlines(), 1):
    if any(k in line for k in ["procedureDetail", "recommendedForms", "ragTrace", "finalAnswer", "setState", "response."]):
        print(f"{i}: {line[:160]}")
