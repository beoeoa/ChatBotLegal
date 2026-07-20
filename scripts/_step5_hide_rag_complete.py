from pathlib import Path

# 1) StreamingResponse: add showRagTrace prop and gate by admin && showRagTrace
p = Path("frontend/src/components/search/StreamingResponse.tsx")
text = p.read_text(encoding="utf-8")

# interface
old_iface = """  groundingStatus?: string | null
  role?: 'citizen' | 'officer' | 'admin'
}"""
new_iface = """  groundingStatus?: string | null
  role?: 'citizen' | 'officer' | 'admin'
  showRagTrace?: boolean
}"""
if old_iface in text:
    text = text.replace(old_iface, new_iface, 1)
    print("OK: interface showRagTrace")
else:
    print("ERROR: interface not found for showRagTrace")
    raise SystemExit(1)

# destructure
old_params = """  groundingStatus,
  role = 'citizen',
}: StreamingResponseProps) {"""
new_params = """  groundingStatus,
  role = 'citizen',
  showRagTrace = false,
}: StreamingResponseProps) {"""
if old_params in text:
    text = text.replace(old_params, new_params, 1)
    print("OK: destructure showRagTrace")
else:
    print("ERROR: destructure not found")
    raise SystemExit(1)

# gate
old_gate = "      {ragTrace && role === 'admin' && ("
new_gate = "      {ragTrace && role === 'admin' && showRagTrace && ("
if old_gate in text:
    text = text.replace(old_gate, new_gate, 1)
    print("OK: gate admin && showRagTrace")
else:
    print("ERROR: gate not found")
    raise SystemExit(1)

p.write_text(text, encoding="utf-8", newline="\n")

# 2) page.tsx pass role + showRagTrace
p2 = Path("frontend/src/app/(dashboard)/search/page.tsx")
text2 = p2.read_text(encoding="utf-8")
old_usage = """                <StreamingResponse
                  isStreaming={ask.isStreaming}
                  strategy={ask.strategy}
                  answers={ask.answers}
                  finalAnswer={ask.finalAnswer}
                  ragTrace={ask.ragTrace}
                  procedureDetail={ask.procedureDetail}
                  recommendedForms={ask.recommendedForms || undefined}
                  citations={ask.citations || undefined}
                  groundingStatus={ask.groundingStatus}
                />"""
new_usage = """                <StreamingResponse
                  isStreaming={ask.isStreaming}
                  strategy={ask.strategy}
                  answers={ask.answers}
                  finalAnswer={ask.finalAnswer}
                  ragTrace={ask.ragTrace}
                  procedureDetail={ask.procedureDetail}
                  recommendedForms={ask.recommendedForms || undefined}
                  citations={ask.citations || undefined}
                  groundingStatus={ask.groundingStatus}
                  role={role}
                  showRagTrace={showRagTrace}
                />"""
if old_usage in text2:
    text2 = text2.replace(old_usage, new_usage, 1)
    print("OK: page passes role + showRagTrace")
else:
    print("ERROR: StreamingResponse usage not found")
    # debug
    idx = text2.find("<StreamingResponse")
    print(repr(text2[idx:idx+500]))
    raise SystemExit(1)

# Ensure admin-only checkbox remains
if "role === 'admin' && (" in text2 and "show-rag-trace" in text2:
    print("OK: checkbox still admin-only")
else:
    # inspect
    if "show-rag-trace" in text2:
        print("NOTE: checkbox exists; checking surrounding role guard")
    else:
        print("WARN: checkbox missing")

p2.write_text(text2, encoding="utf-8", newline="\n")
print("DONE step 5 hide RAG for non-admin")
