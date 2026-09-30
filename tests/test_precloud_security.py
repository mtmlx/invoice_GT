from dataclasses import replace
from types import SimpleNamespace
import io

import pytest

from clickup_integration import auth
from clickup_integration.invoice_sync import _find_existing_invoice, _existing_duplicate_invoices_for_retry, _resolve_posted_sales_invoice_after_post
from inspection_reports.config import InspectionReportSettings
from inspection_reports.models import SharePointItem
from inspection_reports.workflow import InspectionReportWorkflow


def test_oauth_invalid_callbacks_do_not_consume_listener(monkeypatch):
    paths = ['/callback?code=bad', '/callback?code=bad&state=wrong',
             '/callback?code=bad&state=ok&state=ok', '/callback?code=bad&state=%E2%98%83',
             '/callback/extra?code=bad&state=ok', '/callback?code=a&code=b&state=ok',
             '/callback?code=good&state=ok']
    statuses = []
    class Server:
        def __init__(self, address, handler): self.handler = handler
        def handle_request(self):
            handler = object.__new__(self.handler)
            handler.path = paths.pop(0)
            handler._respond = lambda status, message: statuses.append(status)
            handler.do_GET()
        def server_close(self): pass
    monkeypatch.setattr(auth, 'HTTPServer', Server)
    result = auth.wait_for_oauth_callback(SimpleNamespace(redirect_uri='http://localhost:8765/callback'), expected_state='ok')
    assert result == {'code': 'good', 'state': 'ok'}
    assert statuses == [400,400,400,400,404,400,200]


@pytest.mark.parametrize('row', [ {'customerNumber':'VICTIM'}, {}, {'customerNumber':'OURS','customerId':'other-id'} ])
def test_duplicate_customer_conflicts_block_reuse_without_fel_reads(row):
    invoice = {'id':'invoice', 'number':'GTFVR00001', **row}
    class BC:
        def find_entities(self, *args, **kwargs): return [invoice]
        def get_posted_invoice_fel_description_by_number(self,*args,**kwargs): pytest.fail('unbound FEL read')
    found = _find_existing_invoice(bc_client=BC(),market='GT',reference='ref',customer_number='OURS',customer_id='our-id')
    assert found is not None  # still blocks creation
    result = {'status':'duplicate_invoice','market':'GT',
        'proposed_bc_invoices':[{'invoice_group':'ALL','proposed_bc_payload':{'customerNumber':'OURS','customerId':'our-id'}}],
        'duplicate_invoices':[{'invoice_group':'ALL','existing_invoice':found}]}
    assert _existing_duplicate_invoices_for_retry(result) == []


def test_same_customer_retry_remains_supported():
    result = {'status':'duplicate_invoice','market':'GT',
        'proposed_bc_invoices':[{'invoice_group':'ALL','proposed_bc_payload':{'customerNumber':'OURS'}}],
        'duplicate_invoices':[{'invoice_group':'ALL','existing_invoice':{'number':'GTFVR00001','customerNumber':'OURS'}}]}
    assert len(_existing_duplicate_invoices_for_retry(result)) == 1


@pytest.mark.parametrize('fallback', [False, True])
def test_posted_resolution_rejects_wrong_customer(fallback):
    class BC:
        def get_entity(self,*args,**kwargs): return None if fallback else {'number':'GTFVR00001','customerNumber':'VICTIM'}
        def get_posted_sales_invoice_by_external_document_number(self,*args,**kwargs): return {'number':'GTFVR00001','customerNumber':'VICTIM'}
    with pytest.raises(ValueError,match='customer'):
        _resolve_posted_sales_invoice_after_post(bc_client=BC(),created_invoice={'id':'draft','externalDocumentNumber':'ref'},market='GT',expected_customer={'customerNumber':'OURS'})


@pytest.mark.parametrize('change', [{'id':'other'},{'drive_id':'other'},{'name':'private.pdf'},{'mime_type':'text/plain'},{'is_folder':True}])
def test_report_recovery_rejects_unrelated_graph_items(tmp_path,change):
    expected = SharePointItem(id='report',drive_id='trusted',name='VIN123.pdf',path='/reports/VIN123.pdf',web_url=None,mime_type='application/pdf')
    supplied = replace(expected, **change)
    class SP:
        def get_item_from_share_url(self,url): return expected if url == 'https://trusted/folder' else supplied
        def find_child_file_by_name_from_folder_item(self,**kwargs): return expected
        def download_item(self,*args): pytest.fail('unauthorized content download')
    settings=replace(InspectionReportSettings.from_env(),sharepoint_output_folder_url='https://trusted/folder',output_dir=tmp_path)
    workflow=InspectionReportWorkflow(settings=settings,clickup_client=object(),sharepoint_client=SP())
    result=workflow._ensure_existing_report_file_attachment({'task_id':'task','name':'VIN123','report_fields':{'VIN number':'VIN123'},'custom_fields':{}},report_url='https://unrelated/item')
    assert result['status']=='download_failed'
    assert not list(tmp_path.iterdir())
