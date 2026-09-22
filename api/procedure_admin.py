"""Admin CRUD for the ward directory; assignments commit with the record."""
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field, ConfigDict

from api.auth import get_request_role, get_request_user_id
from api.legal_domains import canonicalize_legal_domain
from api.organization_service import domains_for_unit
from api.system_settings import active_organization_units
from open_notebook.database.repository import repo_query

router = APIRouter(prefix='/procedures/admin/records', tags=['ward-procedures'])


class ProcedureFormWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    form_id: str | None = None
    catalog_procedure_id: str | None = None
    name: str = Field(min_length=2, max_length=1000)
    file_type: str = Field(default='pdf', pattern=r'^(pdf|doc|docx|xls|xlsx|file|eform)$')
    download_url: str = Field(min_length=1, max_length=4000)
    official_level: str = Field(default='official', pattern=r'^(official|reference)$')
    review_status: str = Field(
        default='approved', pattern=r'^(approved|candidate_pending_review)$'
    )


class ProcedureWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=3, max_length=1000)
    domain_slug: str = Field(min_length=1, max_length=100)
    primary_organization_unit_id: str
    supporting_organization_unit_ids: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list, max_length=100)
    documents_required: list[str] = Field(default_factory=list, max_length=100)
    guidance: str = Field(default='', max_length=10000)
    submission_place: str = Field(default='', max_length=2000)
    legal_basis: list[str] = Field(default_factory=list, max_length=100)
    duration: str = Field(default='', max_length=2000)
    fee: str = Field(default='', max_length=2000)
    forms: list[ProcedureFormWrite] = Field(default_factory=list, max_length=30)
    reason: str = Field(min_length=10, max_length=2000)


def require_admin(request):
    if get_request_role(request) != 'admin':
        raise HTTPException(403, 'Chỉ quản trị viên được sửa danh mục thủ tục.')


def record_key(identity):
    key = identity.removeprefix('ward_procedure:')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', key):
        raise HTTPException(422, 'Mã thủ tục không hợp lệ.')
    return key


async def validated_payload(body):
    units = {unit.id: unit for unit in await active_organization_units() if unit.is_active}
    ids = list(dict.fromkeys([body.primary_organization_unit_id, *body.supporting_organization_unit_ids]))
    if any(identity not in units for identity in ids):
        raise HTTPException(422, 'Phòng ban không tồn tại hoặc đã ngừng hoạt động.')
    primary = units[body.primary_organization_unit_id]
    domain = canonicalize_legal_domain(body.domain_slug)
    if domain not in {canonicalize_legal_domain(d) for d in domains_for_unit(primary)}:
        raise HTTPException(422, 'Lĩnh vực không thuộc phòng ban chủ trì.')
    for form in body.forms:
        if bool(form.form_id) != bool(form.catalog_procedure_id):
            raise HTTPException(422, 'Cần chọn cả biểu mẫu và thủ tục gốc từ kho chung.')
        url = form.download_url.strip()
        if not (url.startswith('https://') or url.startswith('/api/')):
            raise HTTPException(
                422,
                'Đường dẫn biểu mẫu phải là HTTPS hoặc đường dẫn /api/ nội bộ.',
            )
    payload = body.model_dump(exclude={'reason'})
    for index, form in enumerate(body.forms):
        if form.form_id:
            from api.procedure_form_links import resolve_catalog_link
            linked = resolve_catalog_link(form.model_dump())
            if linked['review_status'] != 'approved':
                raise HTTPException(422, 'Mẫu không còn được công khai cho thủ tục hoặc đối tượng này. Hãy chọn lại từ kho biểu mẫu.')
            payload['forms'][index] = linked
    payload.update(department=primary.name, domain_slug=body.domain_slug,
                   supporting_organization_unit_ids=[i for i in ids if i != primary.id],
                   updated=datetime.now(timezone.utc))
    relations = [{'procedure_id': '', 'organization_unit_id': identity,
                  'responsibility': 'primary' if identity == primary.id else 'support',
                  'created': payload['updated'], 'updated': payload['updated']} for identity in ids]
    return payload, relations


def public_record(record):
    value = dict(record)
    value['id'] = str(value.get('id') or '').removeprefix('ward_procedure:')
    value.setdefault('steps', [])
    value.setdefault('documents_required', [])
    value.setdefault('guidance', '')
    value.setdefault('submission_place', '')
    value.setdefault('legal_basis', [])
    value.setdefault('duration', '')
    value.setdefault('fee', '')
    value.setdefault('forms', [])
    from api.procedure_form_links import resolve_catalog_link
    value['forms'] = [resolve_catalog_link(form) for form in value['forms']]
    value.setdefault('supporting_organization_unit_ids', [])
    return value


@router.get('')
async def list_records(request: Request):
    require_admin(request)
    rows = await repo_query(
        'SELECT * FROM ward_procedure ORDER BY updated DESC, name ASC;', {}
    )
    # Old versions of the seed importer created generated record IDs. Keep
    # one current logical procedure in the UI without rewriting that history.
    seen = set()
    items = []
    for row in rows:
        key = (
            str(row.get('domain_slug') or ''),
            str(row.get('name') or '').strip().casefold(),
        )
        if row.get('archived') or key in seen:
            continue
        seen.add(key)
        items.append(public_record(row))
    return {'items': items, 'total': len(items)}


@router.post('', status_code=201)
async def create(body: ProcedureWrite, request: Request):
    require_admin(request)
    payload, relations = await validated_payload(body)
    identity = 'managed_' + uuid.uuid4().hex
    payload.update(catalog_status='approved', archived=False, created=payload['updated'])
    return await write(identity, payload, relations, body.reason, actor=get_request_user_id(request), create=True)


async def write(identity, payload, relations, reason, *, actor, create=False):
    for relation in relations:
        relation['procedure_id'] = identity
    operation = 'CREATE' if create else 'UPDATE'
    await repo_query(
        'BEGIN TRANSACTION; '
        + ('' if create else "IF array::len(SELECT id FROM type::thing('ward_procedure', $key)) = 0 { THROW 'procedure_not_found'; }; ")
        + "LET $before = SELECT * FROM type::thing('ward_procedure', $key); "
        + f"{operation} type::thing('ward_procedure', $key) MERGE $payload; "
        'DELETE procedure_organization_unit WHERE procedure_id = $key; '
        'FOR $relation IN $relations { CREATE procedure_organization_unit CONTENT $relation; }; '
        'CREATE procedure_admin_change SET procedure_id = $key, change = $change, before = $before; '
        'COMMIT TRANSACTION;',
        {'key':identity, 'payload':payload, 'relations':relations,
         'change':{'procedure_id':identity,'action':'create' if create else 'update',
                   'reason':reason,'actor':actor,'after':payload,'created':payload['updated']}},
    )
    rows = await repo_query("SELECT * FROM type::thing('ward_procedure', $key);", {'key':identity})
    try:
        from api.procedure_runtime_catalog import sync_managed_procedure_catalog
        await sync_managed_procedure_catalog()
        from api.routers.search import _get_canonical_form_catalog
        _get_canonical_form_catalog.cache_clear()
    except Exception as exc:
        logger.exception("Managed procedure publication refresh failed after CRUD")
        raise HTTPException(
            503,
            "Thủ tục đã được lưu nhưng chưa đồng bộ được sang trang công khai và chatbot. Hãy thử đồng bộ lại.",
        ) from exc
    return rows[0]


@router.put('/{identity}')
async def update(identity: str, body: ProcedureWrite, request: Request):
    require_admin(request)
    identity = record_key(identity)
    rows = await repo_query("SELECT * FROM type::thing('ward_procedure', $key);", {'key':identity})
    if not rows:
        raise HTTPException(404, 'Không tìm thấy thủ tục.')
    if rows[0].get('archived') is True:
        raise HTTPException(409, 'Thủ tục đã được lưu trữ; hãy tạo bản ghi mới thay vì kích hoạt lại bản cũ.')
    payload, relations = await validated_payload(body)
    return await write(identity, payload, relations, body.reason, actor=get_request_user_id(request))


@router.delete('/{identity}', status_code=204)
async def delete(identity: str, request: Request):
    require_admin(request)
    identity = record_key(identity)
    rows = await repo_query("SELECT * FROM type::thing('ward_procedure', $key);", {'key':identity})
    if not rows:
        raise HTTPException(404, 'Không tìm thấy thủ tục.')
    target = rows[0]
    duplicates = await repo_query(
        'SELECT * FROM ward_procedure WHERE domain_slug = $domain AND name = $name;',
        {'domain': target.get('domain_slug'), 'name': target.get('name')},
    )
    now = datetime.now(timezone.utc)
    await repo_query(
        "BEGIN TRANSACTION; CREATE procedure_admin_change CONTENT $change; "
        "UPDATE ward_procedure MERGE { archived: true, updated: $updated } "
        "WHERE domain_slug = $domain AND name = $name; "
        'DELETE procedure_organization_unit WHERE procedure_id IN $keys; COMMIT TRANSACTION;',
        {
            'domain': target.get('domain_slug'),
            'name': target.get('name'),
            'keys': [str(row.get('id') or '').removeprefix('ward_procedure:') for row in duplicates],
            'change': {
                'procedure_id': identity,
                'action': 'delete',
                'actor': get_request_user_id(request),
                'before': duplicates or rows,
                'created': now,
            },
            'updated': now,
        },
    )
    try:
        from api.procedure_runtime_catalog import sync_managed_procedure_catalog
        await sync_managed_procedure_catalog()
        from api.routers.search import _get_canonical_form_catalog
        _get_canonical_form_catalog.cache_clear()
    except Exception as exc:
        logger.exception("Managed procedure publication refresh failed after archive")
        raise HTTPException(
            503,
            "Thủ tục đã được lưu trữ nhưng chưa đồng bộ được sang trang công khai và chatbot. Hãy thử đồng bộ lại.",
        ) from exc
