"""Preserve the observed production API and recovered GT executable source.

These are recovery/release guards, not evidence of the installed app version
or sandbox execution. Additive Mexico API actions are deliberately allowed.
"""
import hashlib
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'bc_extension/customer_invoicing_sync/src'
EVIDENCE = ROOT / 'docs/gt-baseline'
CONTRACT = json.loads((EVIDENCE / 'production-api-contract.json').read_text())
TYPE_MAP = {'Text': 'Edm.String', 'Decimal': 'Edm.Decimal', 'Boolean': 'Edm.Boolean',
            'Guid': 'Edm.Guid', 'Integer': 'Edm.Int32'}


@pytest.mark.parametrize('entity,expected', CONTRACT.items())
def test_recovered_extension_preserves_production_invoice_api(entity, expected):
    sources = [p.read_text() for p in SRC.glob('*.Page.al')]
    matches = [s for s in sources if re.search(r"EntityName\s*=\s*'" + entity + "'", s)]
    assert len(matches) == 1, entity
    source = matches[0]
    fields = set(re.findall(r'\bfield\(\s*(\w+)\s*;', source))
    assert set(expected['fields']) <= fields
    actions = {}
    for name, params in re.findall(r'\[ServiceEnabled\]\s*procedure\s+(\w+)\((.*?)\)', source, re.S):
        pairs = [p.strip().split(':', 1) for p in params.split(';')]
        actions[name[0].lower() + name[1:]] = [
            [name.strip(), TYPE_MAP[kind.strip()]] for name, kind in pairs
            if kind.strip() != 'WebServiceActionContext'
        ]
    for name, params in expected['actions'].items():
        assert actions.get(name) == params, name


def test_guatemala_source_is_preserved_from_recovered_51_package():
    provenance = json.loads((EVIDENCE / 'source-provenance.json').read_text())
    # The Mexico patch extends these three objects. All other recovered objects,
    # including GT stamping, credit memos, email workers and setup, stay exact.
    extended = {'MXPostedInvoiceCfdiMgt.Codeunit.al',
                'MtmCustomerInvoicingApi.PermissionSet.al',
                'PostedInvoiceFelDescriptionApi.Page.al'}
    for obj in provenance['objects']:
        path = ROOT / obj['repository_path']
        if path.name in extended:
            continue
        tokens = re.findall(r"//[^\n]*|'(?:[^']|'')*'|\"[^\"]*\"|[A-Za-z_0-9]+|[^\s]", path.read_text())
        digest = hashlib.sha256(json.dumps(tokens, ensure_ascii=False).encode()).hexdigest()
        assert digest == obj['token_sha256'], path.name
