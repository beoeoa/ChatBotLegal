"""Resolve directory links against the current published form catalog, not copies."""
from datetime import date


def resolve_catalog_link(record):
    if not record.get('form_id') or not record.get('catalog_procedure_id'):
        return dict(record)
    from api.form_governance_service import get_form_governance_service
    from api.form_router_v3 import resolve_forms
    value = dict(record)
    release = get_form_governance_service().repository.active_release() or {}
    manifest = release.get('manifest') or {}
    result = resolve_forms(question=record['catalog_procedure_id'], manifest=manifest,
                           audience='citizen', legal_as_of=date.today())
    form = next((item for item in result.get('recommended_forms', [])
                 if item['form_id'] == record['form_id']), None)
    if not form:
        return {**value, 'review_status': 'candidate_pending_review', 'download_url': ''}
    return {**value, 'name': form['name'], 'file_type': form.get('file_type') or 'file',
            'download_url': form.get('download_url') or form.get('source_url') or '',
            'official_level': 'official', 'review_status': 'approved'}
