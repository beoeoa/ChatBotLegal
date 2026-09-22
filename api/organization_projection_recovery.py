"""Retry recorded department projections through the existing audited API.

The pending authority row is the durable queue. Reuse the normal optimistic
fingerprint check so a newer admin decision wins over a delayed retry.
"""
import asyncio
import os
import httpx
from loguru import logger


async def recover_pending_assignments():
    from open_notebook.database.repository import repo_query
    pending = await repo_query('SELECT document_id FROM document_organization_assignment WHERE confirmation_status="projection_pending" LIMIT 50;')
    if not pending:
        return 0
    password = os.getenv('OPEN_NOTEBOOK_ADMIN_PASSWORD') or os.getenv('OPEN_NOTEBOOK_PASSWORD')
    if not password:
        return 0
    from api.routers.legal_search import _management_unit_projection, _organization_assignment_fingerprint
    projections = await _management_unit_projection([str(row['document_id']) for row in pending])
    recovered = 0
    base_url = os.getenv('LEGAL_LOCAL_API_URL', 'http://127.0.0.1:5055')
    async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
        login = await client.post('/api/auth/login', json={'identifier': 'admin', 'password': password})
        login.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + login.json()['token']
        for row in pending:
            identity = str(row['document_id'])
            projection = projections.get(identity) or {}
            if projection.get('organization_assignment_status') != 'projection_pending':
                continue
            response = await client.post(f'/api/legal/management/documents/{identity}/organization-assignment', json={
                'assignment_state': projection['organization_assignment_state'],
                'primary_organization_unit_id': projection.get('primary_organization_unit_id'),
                'organization_unit_ids': projection.get('organization_unit_ids') or [],
                'expected_fingerprint': _organization_assignment_fingerprint(projection),
                'reason': 'Tự động đồng bộ lại phân công đã ghi nhận; giữ nguyên quyết định và nội dung pháp luật.',
            })
            if response.is_success:
                recovered += 1
            elif response.status_code != 409:
                logger.warning('organization_projection_retry_failed document={} status={}', identity, response.status_code)
    return recovered


async def organization_projection_recovery_loop():
    while True:
        await asyncio.sleep(30)
        try:
            await recover_pending_assignments()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('organization_projection_recovery_failed type={}', type(exc).__name__)
