from pathlib import Path

# 1) use-ask.ts: add faqs state + mapping
hook = Path("frontend/src/lib/hooks/use-ask.ts")
ht = hook.read_text(encoding="utf-8")

# Add faqs to AskState interface
if "faqs:" not in ht:
    ht = ht.replace(
        "  recommendedForms: AskResponse['recommended_forms'] | null",
        "  recommendedForms: AskResponse['recommended_forms'] | null\n  faqs: AskResponse['faqs'] | null",
        1,
    )
    print("OK: faqs in AskState")
else:
    print("SKIP: faqs already in AskState")

# Add faqs to initial state
if "recommendedForms: null," in ht and "faqs: null," not in ht:
    ht = ht.replace(
        "      recommendedForms: null,",
        "      recommendedForms: null,\n      faqs: null,",
        1,
    )
    print("OK: faqs in initial state")

# Add faqs to setState response mapping
if "procedureDetail: response.procedure_detail" in ht:
    ht = ht.replace(
        "        procedureDetail: response.procedure_detail || null,\n      recommendedForms: (response.recommended_forms as any[]) || null,",
        "        procedureDetail: response.procedure_detail || null,\n        recommendedForms: (response.recommended_forms as any[]) || null,\n        faqs: (response.faqs as any[]) || null,",
        1,
    )
    print("OK: faqs in response mapping")

# Add faqs to reset states
for pattern in ["      ragTrace: null,\n      procedureDetail: null,\n      recommendedForms: null,"]:
    if pattern in ht and "faqs: null," not in ht:
        ht = ht.replace(pattern, pattern.replace("recommendedForms: null,", "recommendedForms: null,\n      faqs: null,"))
        print("OK: faqs in reset")

hook.write_text(ht, encoding="utf-8", newline="\n")
print("use-ask.ts updated")

# 2) StreamingResponse: add FAQAccordion import + render
sr = Path("frontend/src/components/search/StreamingResponse.tsx")
st = sr.read_text(encoding="utf-8")

# Add import
if "FAQAccordion" not in st:
    st = st.replace(
        "import { StreamingResponse } from '@/components/search/StreamingResponse'",
        "import { FAQAccordion } from '@/components/search/FAQAccordion'\nimport { StreamingResponse } from '@/components/search/StreamingResponse'",
        1,
    )
    # Actually this is inside StreamingResponse itself, so add import at top
    st = st.replace(
        'import type { AskResponse, RagTrace } from \'@/lib/types/search\'',
        'import type { AskResponse, RagTrace } from \'@/lib/types/search\'\nimport { FAQAccordion } from \'./FAQAccordion\'',
        1,
    )
    print("OK: FAQAccordion import")
else:
    print("SKIP: FAQAccordion import exists")

# Add faqs prop to interface
if "faqs?: FaqItem[]" not in st:
    st = st.replace(
        "  groundingStatus?: string | null\n  role?: 'citizen' | 'officer' | 'admin'\n  showRagTrace?: boolean\n}",
        "  groundingStatus?: string | null\n  role?: 'citizen' | 'officer' | 'admin'\n  showRagTrace?: boolean\n  faqs?: Array<{ id: string; question: string; answer: string; steps?: string[]; form_ids?: string[]; domain?: string; ward_scope?: string | null }> | null\n}",
        1,
    )
    print("OK: faqs in interface")

# Add faqs to destructuring
if "showRagTrace = false," in st:
    st = st.replace(
        "  showRagTrace = false,\n}: StreamingResponseProps) {",
        "  showRagTrace = false,\n  faqs,\n}: StreamingResponseProps) {",
        1,
    )
    print("OK: faqs in destructure")

# Replace procedure card with FAQAccordion when FAQs exist
# Current: renders procedureDetail forms card
# New: if faqs.length > 0, render FAQAccordion; else render procedure reference only
old_card = '''{((procedureDetail?.forms && procedureDetail.forms.length > 0) || (recommendedForms && recommendedForms.length > 0)) && (
        <Card className="border-primary/20 bg-primary/5">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2 text-primary">
              <Sparkles className="h-4 w-4" />
              Biểu mẫu liên quan
            </CardTitle>'''

new_card = '''{(faqs && faqs.length > 0) && (
        <FAQAccordion faqs={faqs} />
      )}

{(!faqs || faqs.length === 0) && ((procedureDetail?.forms && procedureDetail.forms.length > 0) || (recommendedForms && recommendedForms.length > 0)) && (
        <Card className="border-primary/20 bg-primary/5">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2 text-primary">
              <Sparkles className="h-4 w-4" />
              Biểu mẫu liên quan
            </CardTitle>'''

if old_card in st:
    st = st.replace(old_card, new_card, 1)
    print("OK: FAQAccordion renders before forms")
else:
    print("WARN: procedure card pattern not found exactly")

# Also hide long procedure card - check if there's a separate procedure card
# Currently the procedure card IS the forms card. The procedure summary/name was inline.
# Let's also add a short procedure reference when procedure_detail exists but no FAQs
if "{procedureDetail?.name}" not in st and "procedureDetail" in st:
    # Find where forms card ends and add short procedure ref before it
    pass  # Already handled by FAQ priority

sr.write_text(st, encoding="utf-8", newline="\n")
print("StreamingResponse.tsx updated")

# 3) page.tsx: pass faqs to StreamingResponse
pg = Path("frontend/src/app/(dashboard)/search/page.tsx")
pt = pg.read_text(encoding="utf-8")
if "faqs={ask.faqs}" not in pt:
    pt = pt.replace(
        "                  groundingStatus={ask.groundingStatus}\n                />",
        "                  groundingStatus={ask.groundingStatus}\n                  faqs={ask.faqs}\n                />",
        1,
    )
    print("OK: page passes faqs")
else:
    print("SKIP: page already passes faqs")
pg.write_text(pt, encoding="utf-8", newline="\n")

print("\n=== STEP 6 COMPLETE ===")
