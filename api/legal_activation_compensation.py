"""Independent compensation attempts after a resumed activation fails checks."""

from api.legal_document_serving_state import rollback_action_for_transition


def compensate_failed_verification(set_state, *, document_id, version_key,
                                   old_document_id=None, old_transition=None):
    receipt = {}
    if old_transition is not None:
        try:
            rollback_action = rollback_action_for_transition(old_transition)
            receipt['old_restore'] = set_state(
                str(old_document_id), action=rollback_action,
                requested_by='approved-draft-recovery',
                reason='Restore exact previous serving state after failed activation verification',
                expected_revision=str(old_transition.get('state_revision') or ''),
            )
        except Exception as exc:
            receipt['old_restore'] = {'status': 'failed', 'error_class': type(exc).__name__}
    # A failure restoring the old version must never skip excluding the new,
    # unverified version. Keep the original activation error retryable.
    try:
        receipt['new_exclude'] = set_state(
            str(document_id), action='exclude', requested_by='approved-draft-recovery',
            reason='activation_pending_verification:' + version_key,
        )
    except Exception as exc:
        receipt['new_exclude'] = {'status': 'failed', 'error_class': type(exc).__name__}
    return receipt
