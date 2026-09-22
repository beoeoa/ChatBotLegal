"""Recognize retired ancestors proven by completed activation receipts only."""
from sqlalchemy import text


def retired_ancestor_ids(connection, replacement_id):
    if not replacement_id or connection.dialect.name != 'postgresql':
        return set()
    if not connection.execute(text("SELECT to_regclass('legal_activation_operation')")).scalar():
        return set()
    rows = connection.execute(text("SELECT document_id,result FROM legal_activation_operation WHERE result IS NOT NULL")).mappings().all()
    parents = {}
    for row in rows:
        receipt = row['result'] or {}
        old = receipt.get('old_document') or {}
        if receipt.get('status') == 'replaced' and old.get('document_id'):
            parents[int(row['document_id'])] = int(old['document_id'])
    return lineage_ancestors(parents, int(replacement_id))


def lineage_ancestors(parents, identity):
    visited = {identity}
    ancestors = set()
    while identity in parents:
        identity = parents[identity]
        if identity in visited:
            break
        visited.add(identity)
        ancestors.add(identity)
    return ancestors
