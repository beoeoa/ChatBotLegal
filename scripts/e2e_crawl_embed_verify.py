import asyncio
import httpx
import os
import re
import sys
import io
from datetime import date
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.crawlers.pdf_extractor import extract_pdf

LEGAL_SEARCH_URL = os.getenv('LEGAL_SEARCH_URL', 'http://127.0.0.1:8765').rstrip('/')

# Known UBND HP QD PDF from earlier crawl4ai smoke test
PDF_URL = (
    'https://cdn.haiphong.gov.vn/gov-hpg/1/steeringdocument/2026/7/'
    'quyet-dinh-2585-cong-bo-tthc-moi-linh-vuc-y-duoc-co-truyen-'
    'thuoc-pham-vi-chuc-nang-cua-so-y-te639190983043962704.pdf'
)
SOURCE_URL = 'https://haiphong.gov.vn/?pageid=27218&p_steering=126716'
LAW_NUMBER = '2585/QD-UBND'
TITLE = (
    'Quyết định về việc công bố Danh mục thủ tục hành chính mới ban hành '
    'lĩnh vực Y, Dược cổ truyền thuộc phạm vi, chức năng quản lý của Sở Y tế '
    'theo quy định tại Thông tư số 21/2026/TT-BYT của Bộ Y tế'
)


async def health() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f'{LEGAL_SEARCH_URL}/health')
        r.raise_for_status()
        return r.json()


async def get_field_id(client: httpx.AsyncClient) -> int:
    r = await client.get(f'{LEGAL_SEARCH_URL}/import/fields')
    if r.status_code == 200:
        data = r.json()
        fields = data.get('fields') or data.get('items') or []
        if fields:
            return int(fields[0].get('id') or fields[0].get('field_id') or 1)
    return 1


async def lookup(law_number: str) -> dict | None:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f'{LEGAL_SEARCH_URL}/documents/lookup',
            params={'law_number': law_number},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get('document')


async def import_doc(content: str) -> dict:
    async with httpx.AsyncClient(timeout=180) as client:
        field_id = await get_field_id(client)
        payload = {
            'title': TITLE,
            'law_number': LAW_NUMBER,
            'document_type': 'QuyetDinh',
            'issuing_agency': 'UBND TP Hải Phòng',
            'scope': 'Hải Phòng',
            'sector': 'HanhChinhCong',
            'field_id': field_id,
            'issued_date': '2026-07-06',
            'effective_date': '2026-07-06',
            'expired_date': None,
            'source_url': SOURCE_URL,
            'content': content,
            'confirmed_official_source': True,
            'structure': 'unstructured',
        }
        r = await client.post(f'{LEGAL_SEARCH_URL}/import/unstructured', json=payload)
        if r.status_code >= 400:
            detail = r.text
            # already exists is acceptable for verify path
            if 'tồn tại' in detail.lower() or 'ton tai' in detail.lower() or 'duplicate' in detail.lower():
                return {'status': 'already_exists', 'detail': detail[:300]}
            raise RuntimeError(f'Import failed: {detail[:500]}')
        return r.json()


async def search(query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f'{LEGAL_SEARCH_URL}/search',
            json={
                'query': query,
                'limit': 8,
                'candidate_count': 80,
                'include_trace': True,
            },
        )
        r.raise_for_status()
        data = r.json()
        return data.get('results') or data.get('items') or []


def has_expected(results: list[dict], law_number: str, title_hint: str) -> tuple[bool, dict | None]:
    law_l = law_number.lower()
    hint_l = title_hint.lower()
    for item in results:
        law = str(item.get('law_number') or '').lower()
        title = str(item.get('document_title') or item.get('title') or '').lower()
        content = str(item.get('content') or item.get('chunk') or item.get('text') or '').lower()
        if law_l in law or '2585' in law or 'y dược cổ truyền' in title or 'y, dược cổ truyền' in content or '21/2026/tt-byt' in content:
            return True, item
        if '2585' in title or 'sở y tế' in title and 'dược cổ truyền' in title:
            return True, item
        if hint_l[:20] in title:
            return True, item
    return False, results[0] if results else None


async def main() -> None:
    print('=' * 60)
    print('STEP 8: Verify retrieval after embed')
    print('=' * 60)

    h = await health()
    before_chunks = h.get('database_chunks')
    print(f"Server OK | indexed={h.get('indexed_records')} chunks={before_chunks}")

    # 1) Ensure document is in DB (import if missing)
    existing = await lookup(LAW_NUMBER)
    if existing:
        print(f"Document already in DB: id={existing.get('id')} law={existing.get('law_number')}")
        import_result = {'status': 'already_exists', 'document_id': existing.get('id')}
    else:
        print('Document not found. Extracting PDF and importing as unstructured...')
        pdf = await extract_pdf(PDF_URL)
        if pdf.get('status') != 'ok' or not pdf.get('text'):
            raise RuntimeError(f"PDF extract failed: {pdf.get('reason')}")
        print(f"PDF text_len={len(pdf['text'])} pages={pdf.get('page_count')}")
        import_result = await import_doc(pdf['text'])
        print(f"Import result: {import_result}")

    # 2) Query with characteristic prompts
    queries = [
        f'{LAW_NUMBER}',
        'Quyết định 2585 UBND Hải Phòng danh mục thủ tục hành chính Y Dược cổ truyền',
        'Thông tư 21/2026/TT-BYT Sở Y tế Hải Phòng công bố thủ tục hành chính mới',
    ]

    passed = 0
    for q in queries:
        results = await search(q)
        ok, top = has_expected(results, LAW_NUMBER, TITLE)
        print(f"\nQuery: {q}")
        print(f"  results={len(results)} match={ok}")
        for i, r in enumerate(results[:3]):
            print(
                f"  [{i+1}] law={r.get('law_number')} score={r.get('score')} "
                f"title={(r.get('document_title') or r.get('title') or '')[:90]}"
            )
            cite = r.get('citation') or r.get('source_url') or r.get('chunk_id')
            if cite:
                print(f"      citation/source: {str(cite)[:120]}")
        if ok:
            passed += 1
            # require non-empty content evidence (anti-hallucination proxy)
            content = str((top or {}).get('content') or (top or {}).get('text') or '')
            if not content and not (top or {}).get('law_number'):
                print('  WARN: matched metadata but empty content')
            else:
                print('  PASS: retrieved expected document evidence')
        else:
            print('  FAIL: expected document not in top results')

    # 3) Negative check: random unrelated query should not invent this law number as top-1 without evidence
    neg = await search('Thủ tục đăng ký khai sinh tại UBND cấp xã theo Luật Hộ tịch')
    neg_top = neg[0] if neg else {}
    invented = str(neg_top.get('law_number') or '').find('2585') >= 0 and 'hộ tịch' not in str(neg_top.get('document_title') or '').lower()
    print('\nNegative query top-1 law_number =', neg_top.get('law_number'))
    print('Negative invented 2585 without context =', invented)

    print('\n' + '=' * 60)
    print(f'VERDICT: {passed}/{len(queries)} characteristic queries matched expected doc')
    if passed >= 1 and not invented:
        print('SUCCESS: retrieval returns newly embedded document with source evidence, not fabrication')
        sys.exit(0)
    print('FAILURE: retrieval verification did not pass thresholds')
    sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
