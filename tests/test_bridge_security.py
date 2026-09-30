from types import SimpleNamespace
import asyncio
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from webhook_bridge.main import _safe_form_urlencoded

class StreamRequest:
    headers={'content-type':'application/x-www-form-urlencoded', 'content-length':'1'}
    def __init__(self,chunks): self.chunks=chunks; self.consumed=0
    async def stream(self):
        for chunk in self.chunks:
            self.consumed+=1
            yield chunk


def test_stream_limit_stops_before_consuming_remaining_body():
    request=StreamRequest([b'a=' + b'x'*(256*1024-2), b'y', b'never'])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(_safe_form_urlencoded(request))
    assert caught.value.status_code == 413
    assert request.consumed == 2


def test_exact_byte_limit_and_unknown_unicode_fields_preserved():
    result=asyncio.run(_safe_form_urlencoded(StreamRequest([b'a='+b'x'*(256*1024-2)])))
    assert len(result['a']) == 256*1024-2
    assert asyncio.run(_safe_form_urlencoded(StreamRequest([b'Body=%E2%98%83&Unknown=&NumMedia=1&MediaUrl0=https%3A%2F%2Fexample.com']))) == {'Body':'☃','Unknown':'','NumMedia':'1','MediaUrl0':'https://example.com'}


def test_form_field_limit_counts_repeated_blank_names():
    with pytest.raises(HTTPException) as caught:
        asyncio.run(_safe_form_urlencoded(StreamRequest([b'&'.join([b'a=']*257)])))
    assert caught.value.status_code == 413
    assert asyncio.run(_safe_form_urlencoded(StreamRequest([b'&'.join([b'a=']*256)]))) == {'a':''}


def test_invalid_utf8_is_client_error():
    with pytest.raises(HTTPException) as caught:
        asyncio.run(_safe_form_urlencoded(StreamRequest([b'Body=\xff'])))
    assert caught.value.status_code == 400


def test_missing_signature_rejected_without_reading_body(monkeypatch):
    from webhook_bridge import main
    from whatsapp_integration.config import WhatsAppSettings
    monkeypatch.setattr(WhatsAppSettings,'from_env',lambda **kwargs:SimpleNamespace(twilio_validate_signature=True,twilio_auth_token='test'))
    request=StreamRequest([b'never'])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(main.whatsapp_inbound(request,x_twilio_signature=None))
    assert caught.value.status_code == 401
    assert request.consumed == 0


def test_readiness_exception_is_not_public(monkeypatch, caplog):
    from webhook_bridge import main
    def fail(): raise RuntimeError('secret-diagnostic-marker')
    monkeypatch.setattr(main.InvoiceAutomationSettings, 'from_env', fail)
    response=TestClient(main.app).get('/clickup/webhooks/invoice-sync/readiness')
    assert response.json()['status']=='not_ready'
    assert 'secret-diagnostic-marker' not in response.text
    assert 'secret-diagnostic-marker' in caplog.text


@pytest.mark.parametrize('route', ['customer-sync','invoice-sync','inspection-invoice-sync','invoice-delivery-recovery'])
def test_unexpected_route_errors_are_not_public(monkeypatch, caplog, route):
    from webhook_bridge import main
    monkeypatch.setenv('CLICKUP_WEBHOOK_TOKEN','test-token')
    def fail(): raise RuntimeError('private-provider-diagnostic')
    monkeypatch.setattr(main.ClickUpSettings, 'from_env', fail)
    response=TestClient(main.app).post('/clickup/webhooks/'+route,headers={'X-Webhook-Token':'test-token'},json={'task_id':'task-1','invoice_numbers':['GTFVRTEST1']})
    assert response.status_code==500
    assert 'private-provider-diagnostic' not in response.text
    assert 'private-provider-diagnostic' in caplog.text


@pytest.mark.parametrize('market', ['GT','MX'])
def test_preflight_nested_errors_are_sanitized(monkeypatch, caplog, market):
    import json
    from clickup_integration import invoice_sync as sync
    def fail(**kwargs): raise RuntimeError('private-preflight-marker')
    monkeypatch.setattr(sync, '_read_customer_invoicing_fel_row', fail)
    helper=sync._validate_customer_fel_readiness if market=='GT' else sync._resolve_market_invoice_settings
    result=helper(bc_client=object(),bc_customer={'id':'customer-id','number':'C001'},market=market,customer_number='C001',customer_id='customer-id')
    assert result['status'].endswith('_api_unavailable')
    assert result['customer_number']=='C001' and result['customer_id']=='customer-id'
    assert 'private-preflight-marker' not in json.dumps(result)
    assert 'private-preflight-marker' in caplog.text


@pytest.mark.parametrize('module_name', ['bc_sync','create_preview'])
def test_customer_extension_error_is_sanitized(caplog, module_name):
    import importlib, json
    module=importlib.import_module('clickup_integration.'+module_name)
    class Client:
        settings=SimpleNamespace(customer_invoicing_sync_path='customerInvoicing')
        def patch_company_path(self,*args,**kwargs): raise RuntimeError('private-extension-marker')
    result=module._apply_customer_invoicing_extension(bc_client=Client(),customer_id='customer-id',market='GT',invoicing_payload={'country':'GT'})
    assert result['status']=='failed'
    assert result['path']=='customerInvoicing'
    assert 'private-extension-marker' not in json.dumps(result)
    assert 'private-extension-marker' in caplog.text
