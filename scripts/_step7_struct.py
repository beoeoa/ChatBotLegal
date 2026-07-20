import sys, json
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# forms_manifest forms structure
fm = json.loads(Path("notebook_data/forms/forms_manifest.json").read_text(encoding="utf-8"))
forms = fm.get("forms")
print("forms type", type(forms).__name__)
if isinstance(forms, dict):
    keys = list(forms.keys())
    print("forms dict keys count", len(keys))
    print("first keys", keys[:5])
    first = forms[keys[0]]
    print("sample", json.dumps(first, ensure_ascii=False)[:600])
elif isinstance(forms, list):
    print("forms list", len(forms))
    print("sample", json.dumps(forms[0], ensure_ascii=False)[:600])

# index structure
fi = json.loads(Path("notebook_data/forms/haiphong_official_form_index.json").read_text(encoding="utf-8"))
print("\nindex type", type(fi).__name__)
if isinstance(fi, dict):
    print("keys", list(fi.keys())[:20])
    for k,v in fi.items():
        if isinstance(v, list):
            print("list", k, len(v))
            if v:
                print("sample keys", list(v[0].keys()) if isinstance(v[0], dict) else type(v[0]))
                print(json.dumps(v[0], ensure_ascii=False)[:500])
        elif isinstance(v, dict):
            print("dict", k, len(v))
        else:
            print(k, type(v).__name__, str(v)[:80])

# priority 200
p200 = Path("notebook_data/forms/priority_200_forms.json")
if p200.exists():
    d = json.loads(p200.read_text(encoding="utf-8"))
    print("\npriority_200 type", type(d).__name__)
    if isinstance(d, dict):
        print("keys", list(d.keys())[:20])
        for k,v in d.items():
            if isinstance(v, list):
                print("list", k, len(v))
                if v:
                    print(json.dumps(v[0], ensure_ascii=False)[:400])
    elif isinstance(d, list):
        print("len", len(d))
        print(json.dumps(d[0], ensure_ascii=False)[:400])

# existing crawl-related scripts
for p in Path("scripts").glob("*form*"):
    print("script", p.name, p.stat().st_size)
for p in Path("scripts").glob("*dvc*"):
    print("script", p.name, p.stat().st_size)
for p in Path("scripts").glob("*crawl*"):
    print("script", p.name, p.stat().st_size)
