from api.legal_form_catalog import FormCatalog
from scripts.monitor_canonical_forms import monitor_catalog


def test_monitor_is_read_only_and_reports_pending_revalidation_without_network():
    catalog = FormCatalog.load_default()
    before = [dict(item) for item in catalog.forms]

    report = monitor_catalog(catalog, network=False, write=False)

    assert catalog.forms == before
    assert report["form_count"] == len(before)
    assert report["catalog_mutated"] is False
    assert report["network_enabled"] is False
    assert report["contains_user_content"] is False
