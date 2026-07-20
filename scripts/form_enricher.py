import sys, io, json, re, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from DrissionPage import ChromiumPage, ChromiumOptions

BASE = "https://thuvienphapluat.vn/bieumau"
OUT_DIR = r"J:\ChatBotLegal\notebook_data\forms"
os.makedirs(OUT_DIR, exist_ok=True)

# Load existing manifest
with open(os.path.join(OUT_DIR, "forms_manifest.json"), "r", encoding="utf-8") as f:
    data = json.load(f)

DOMAIN_KEYWORDS = data.get("forms", {}).get("ho_tich", [])  # just to check structure
forms_data = data["forms"]

# Flatten all forms
all_forms = []
for domain, forms in forms_data.items():
    for f in forms:
        all_forms.append(f)

print(f"Total forms to enrich: {len(all_forms)}")

def main():
    co = ChromiumOptions()
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-sandbox")
    co.set_argument("--window-size=1280,800")
    co.set_argument("--window-position=-2000,0")
    co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    
    page = ChromiumPage(co)
    
    print("Loading base page for CF bypass...")
    page.get(BASE)
    for i in range(20):
        time.sleep(3)
        title = page.title
        if "just a moment" not in title.lower():
            print(f"  Ready: {title}")
            break
    else:
        print("  CF not resolved")
        page.quit()
        return
    
    enriched = 0
    for idx, form in enumerate(all_forms):
        if form.get("enriched"):
            continue
        print(f"[{idx+1}/{len(all_forms)}] {form['title'][:40]}...", end=" ", flush=True)
        try:
            # Navigate to detail page (these bypass CF)
            page.get(form["full_url"])
            time.sleep(4)
            
            html = page.html
            # Extract: tags, download button, field/category, updated date
            tags = re.findall(r'href="/bieumau\?tag=([^"]+)"', html)
            has_download = 'data-action="download"' in html
            # Find updated date
            date_match = re.search(r'Cập nhật:\s*</span>\s*<[^>]*>\s*([0-9/]+)', html)
            updated = date_match.group(1) if date_match else None
            # Find field/category
            field_match = re.search(r'<b>Lĩnh vực:</b>\s*<[^>]*>([^<]+)', html)
            field_name = field_match.group(1).strip() if field_match else None
            # Find document type
            type_match = re.search(r'<b>Loại mẫu:</b>\s*<[^>]*>([^<]+)', html)
            form_type = type_match.group(1).strip() if type_match else None
            # Find legal basis references
            legal_refs = re.findall(r'href="(/vphc/[^"]+)"[^>]*>([^<]+)', html)
            
            form["tags"] = tags[:10]
            form["has_download"] = has_download
            form["updated_date"] = updated
            form["field_name"] = field_name
            form["form_type"] = form_type
            form["legal_refs"] = [{"url": u, "title": t.strip()} for u, t in legal_refs[:5]]
            form["enriched"] = True
            enriched += 1
            print(f"OK (tags={len(tags)}, type={form_type})")
        except Exception as ex:
            print(f"ERROR: {str(ex)[:60]}")
        
        # Save progress every 20
        if (idx + 1) % 20 == 0:
            print(f"  Saving progress... {enriched} enriched")
            with open(os.path.join(OUT_DIR, "forms_manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"total_crawled": data["total_crawled"], "total_classified": data["total_classified"], "forms": forms_data}, f, ensure_ascii=False, indent=2)
        
        time.sleep(1)
    
    page.quit()
    
    # Final save
    with open(os.path.join(OUT_DIR, "forms_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"total_crawled": data["total_crawled"], "total_classified": data["total_classified"], "forms": forms_data, "enriched": enriched}, f, ensure_ascii=False, indent=2)
    
    print(f"\n=== ENRICHMENT COMPLETE ===")
    print(f"  Enriched: {enriched}/{len(all_forms)}")

main()
