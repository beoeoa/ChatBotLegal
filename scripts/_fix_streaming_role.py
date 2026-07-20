from pathlib import Path
p = Path("frontend/src/components/search/StreamingResponse.tsx")
text = p.read_text(encoding="utf-8")

# Add role to destructuring
old = """  citations,
  groundingStatus,
}: StreamingResponseProps) {"""
new = """  citations,
  groundingStatus,
  role = 'citizen',
}: StreamingResponseProps) {"""
if old in text:
    text = text.replace(old, new, 1)
    print("OK: role in destructuring")
else:
    print("ERROR: destructuring pattern not found")
    raise SystemExit(1)

# Ensure interface has role
if "role?: 'citizen' | 'officer' | 'admin'" not in text:
    old_iface = "  groundingStatus?: string | null\n}"
    new_iface = "  groundingStatus?: string | null\n  role?: 'citizen' | 'officer' | 'admin'\n}"
    if old_iface in text:
        text = text.replace(old_iface, new_iface, 1)
        print("OK: role in interface")
    else:
        print("ERROR: interface pattern not found")
        raise SystemExit(1)
else:
    print("OK: interface already has role")

# Ensure ragTrace block is gated by admin + showRagTrace if available
# Current objective: only admin + showRagTrace. Component may not have showRagTrace prop yet.
# Check current gate
if "ragTrace && role === 'admin' && (" in text:
    print("OK: ragTrace currently gated by admin")
elif "ragTrace && (" in text:
    text = text.replace("ragTrace && (", "ragTrace && role === 'admin' && (", 1)
    print("OK: added admin gate")
else:
    print("WARN: unexpected ragTrace condition")

p.write_text(text, encoding="utf-8", newline="\n")
print("StreamingResponse.tsx patched")
