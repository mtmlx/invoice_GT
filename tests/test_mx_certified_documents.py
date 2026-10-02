import base64
from types import SimpleNamespace
import pytest
from business_central_client.client import BusinessCentralClient

class Reader(BusinessCentralClient):
    def __init__(self, link=None):
        self.settings=SimpleNamespace(environment='Production')
        self.link=link
        self.reads=[]
    def _resolve_company_id(self, *, company_id, market):
        assert company_id is None and market == 'MX'
        return 'mx-company'
    def _request(self, method, url, **kwargs):
        assert method=='GET'; self.reads.append(url)
        return {'value':[{'number':'B0003384','pdfBase64Content@odata.mediaReadLink':self.link or url+'(id)/pdfBase64Content','certifiedXmlBase64':base64.b64encode(b'<CFDI/>').decode()}]}
    def _request_bytes(self, method, url, **kwargs):
        assert method=='GET'; self.reads.append(url)
        return base64.b64encode(b'%PDF-1.4 example') if url.endswith('pdfBase64Content') else b'<CFDI/>'

def test_reads_original_pac_bytes_without_regeneration():
    r=Reader(); assert r.get_mx_certified_invoice_documents('B0003384')=={'pdf':b'%PDF-1.4 example','xml':b'<CFDI/>'}
    assert len(r.reads)==2

def test_foreign_document_link_rejected_before_authenticated_read():
    r=Reader('https://other.example/steal')
    with pytest.raises(ValueError,match='leaves'):r.get_mx_certified_invoice_documents('B0003384')
    assert len(r.reads)==1
