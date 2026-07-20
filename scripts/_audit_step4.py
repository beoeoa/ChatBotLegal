from pathlib import Path

files = {
    "search_page": Path(r"frontend/src/app/(dashboard)/search/page.tsx"),
    "command_palette": Path(r"frontend/src/components/common/CommandPalette.tsx"),
    "sidebar": Path(r"frontend/src/components/layout/AppSidebar.tsx"),
}

for name, p in files.items():
    text = p.read_text(encoding="utf-8")
    print(f"==== {name} ====")
    print(f"size={len(text)} lines={text.count(chr(10))+1}")
    for key in [
        "canUseSearchTab",
        "mode=search",
        "mode=ask",
        "TabsContent value=\"search\"",
        "MessageCircleQuestion",
        "role === 'citizen'",
        "role === 'officer'",
        "role === 'admin'",
    ]:
        print(f"  {key}: {text.count(key)}")
