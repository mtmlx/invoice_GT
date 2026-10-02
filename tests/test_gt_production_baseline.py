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
            'Guid': 'Edm.Guid', 'Integer': 'Edm.Int32', 'Date': 'Edm.Date', 'DateTime': 'Edm.DateTimeOffset'}


def _al_tokens(source):
    return [token for token in re.findall(
        r"//[^\n]*|'(?:[^']|'')*'|\"[^\"]*\"|[A-Za-z_0-9]+|[^\s]", source)
        if not token.startswith('//')]


def _statement_end(tokens, start):
    """Locate a whole AL statement, including a nested dangling else."""
    if tokens[start] == 'begin':
        depth = 0
        for index in range(start, len(tokens)):
            if tokens[index] in {'begin', 'case'}:
                depth += 1
            elif tokens[index] == 'end':
                depth -= 1
                if depth == 0:
                    index += 1
                    return index + (index < len(tokens) and tokens[index] == ';')
        raise AssertionError('Unterminated AL block')
    if tokens[start] == 'if':
        then = tokens.index('then', start + 1)
        end = _statement_end(tokens, then + 1)
        if end < len(tokens) and tokens[end] == 'else':
            return _statement_end(tokens, end + 1)
        return end
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index] in {'(', '['}:
            depth += 1
        elif tokens[index] in {')', ']'}:
            depth -= 1
        elif tokens[index] == ';' and depth == 0:
            return index + 1
    raise AssertionError('Unterminated AL statement')


def _procedure_bodies(source):
    tokens = _al_tokens(source)
    result = {}
    for start, token in enumerate(tokens):
        if token != 'procedure':
            continue
        name = tokens[start + 1]
        begin = tokens.index('begin', start + 2)
        end = _statement_end(tokens, begin)
        # Exclude the separator after the procedure's outer end.
        if tokens[end - 1] == ';':
            end -= 1
        result[name] = tokens[begin:end]
    return result


def _procedure_declarations(source):
    tokens = _al_tokens(source)
    result = {}
    for start, token in enumerate(tokens):
        if token != 'procedure':
            continue
        name = tokens[start + 1]
        begin = tokens.index('begin', start + 2)
        prefix = start - (tokens[start - 1] == 'local')
        if tokens[prefix - 3:prefix] == ['[', 'TryFunction', ']']:
            prefix -= 3
        result[name] = tokens[prefix:begin]
    return result


def _specialize_gt(tokens):
    """Keep the Guatemala branch of explicit market checks; never erase others."""
    tokens = list(tokens)
    conditions = [(['if', 'IsMexicoCompany', '(', ')', 'then'], False),
                  (['if', 'not', 'IsMexicoCompany', '(', ')', 'then'], True),
                  (['if', "''", '<', '>', "''", 'then'], False)]
    index = 0
    while index < len(tokens):
        for condition, take_then in conditions:
            if tokens[index:index + len(condition)] != condition:
                continue
            then_start = index + len(condition)
            then_end = _statement_end(tokens, then_start)
            end = then_end
            selected = tokens[then_start:then_end] if take_then else []
            if end < len(tokens) and tokens[end] == 'else':
                end = _statement_end(tokens, end + 1)
                if not take_then:
                    selected = tokens[then_end + 1:end]
            tokens[index:end] = selected
            break
        else:
            index += 1
    return tokens


def _replace_tokens(tokens, original, replacement):
    index = 0
    while index <= len(tokens) - len(original):
        if tokens[index:index + len(original)] == original:
            tokens[index:index + len(original)] = replacement
            index += len(replacement)
        else:
            index += 1
    return tokens


def _canonical_blocks(tokens):
    """A begin/end around one statement has the same AL execution semantics."""
    tokens = list(tokens)
    changed = True
    while changed:
        changed = False
        for index, token in enumerate(tokens):
            if token != 'begin':
                continue
            end = _statement_end(tokens, index)
            closing = end - 1 - (tokens[end - 1] == ';')
            inner = tokens[index + 1:closing]
            if inner and _statement_end(inner, 0) == len(inner):
                tokens[index:end] = inner
                changed = True
                break
    return tokens


def _gt_email_body(name, body):
    tokens = _specialize_gt(body)
    # These getters are independently verified below before specializing calls.
    for original, replacement in [
        (['EnsureSupportedCompany', '(', ')', ';'], []),
        (['GetExpectedSender', '(', ')'], ['ExpectedSenderLbl']),
        (['GetSenderDisplayName', '(', ')'], ["'Consuelo Velasquez'"]),
        (['GetMexicoInvoiceEmailDetails', '(', 'PostedInvoice', ')'], ["''"]),
        (['GetDeliveryCc', '(', 'Audit', ')'], ["''"]),
    ]:
        tokens = _replace_tokens(tokens, original, replacement)
    if name == 'TryPrepareInvoiceEmail':
        tokens = ["''" if token == 'CcRecipients' else token for token in tokens]
        tokens = _specialize_gt(tokens)
    elif name in {'SendApprovedInvoiceEmail', 'SendApprovedInvoiceTestEmailToMario'}:
        # Only the two approved native compose calls have additional arguments.
        # A similarly named argument on any other call must remain protected.
        for index, token in enumerate(tokens):
            if token != 'TryPrepareInvoiceEmail':
                continue
            assert tokens[index + 1] == '('
            depth, start, arguments = 0, index + 2, []
            for closing in range(start, len(tokens)):
                current = tokens[closing]
                if current == '(':
                    depth += 1
                elif current == ')':
                    if depth == 0:
                        arguments.append(tokens[start:closing])
                        break
                    depth -= 1
                elif current == ',' and depth == 0:
                    arguments.append(tokens[start:closing])
                    start = closing + 1
            if len(arguments) == 9:
                assert arguments[2] == ['XmlAttachmentBlob'] and arguments[7] == ["''"]
                arguments = [argument for position, argument in enumerate(arguments) if position not in {2, 7}]
                flattened = [value for position, argument in enumerate(arguments)
                             for value in ([','] if position else []) + argument]
                tokens[index + 2:closing] = flattened
            else:
                assert len(arguments) == 7
    if name == 'BuildCommandEraEmailBody':
        # Resolve constant string concatenation after the GT footer getters.
        tokens = _replace_tokens(tokens, ['+', "''"], [])
        index = 0
        while index + 2 < len(tokens):
            left, plus, right = tokens[index:index + 3]
            if plus == '+' and left.startswith("'") and right.startswith("'"):
                value = left[1:-1].replace("''", "'") + right[1:-1].replace("''", "'")
                tokens[index:index + 3] = ["'" + value.replace("'", "''") + "'"]
            else:
                index += 1
    return _canonical_blocks(tokens)


def _digest(tokens):
    return hashlib.sha256(json.dumps(tokens, ensure_ascii=False).encode()).hexdigest()


def _gt_email_declaration(name, declaration):
    tokens = list(declaration)
    if name in {'SendApprovedInvoiceEmail', 'SendApprovedInvoiceTestEmailToMario'}:
        tokens = _replace_tokens(tokens,
            ['XmlAttachmentBlob', ':', 'Codeunit', '"Temp Blob"', ';'], [])
    if name == 'SendApprovedInvoiceTestEmailToMario':
        tokens = _replace_tokens(tokens, ['local', 'procedure', 'SendInternalCanary'],
                                 ['procedure', name])
        tokens = _replace_tokens(tokens, [';', 'ExpectedPdfSha256', ':', 'Text'], [])
    if name == 'TryPrepareInvoiceEmail':
        tokens = _replace_tokens(tokens,
            [';', 'var', 'XmlAttachmentBlob', ':', 'Codeunit', '"Temp Blob"'], [])
        tokens = _replace_tokens(tokens, [';', 'CcRecipients', ':', 'Text'], [])
    return tokens


# Derived from recovered .51 source at commit 89d5e9d. Its complete token hash
# matches source-provenance.json: feab2683...f9bc52eb5b8d18. These hashes cover
# procedure declarations (including TryFunction and variable types) and the
# Guatemala executable path, not the shared file's new Mexico-only branches.
GT_EMAIL_PROCEDURE_HASHES = {
    'SendApprovedInvoiceEmail': '40f425de6ac18f88bd4d24e43c2a4eeb988c42763a246092c535a5463baad0eb',
    'SendApprovedInvoiceTestEmailToMario': 'cfa5f52f0daeee531f0a4942b59d737e8c12f727130993ac50f55416de520d1e',
    'GetApprovedInvoiceTestEmailEvidence': '40071f71d675fae72987fde403d79b44bfee8e9296ef0cb6d0d05966ff7972e1',
    'BuildSenderEvidence': '45c5e3c1f148d69c214de6faf6238ce4ec2064c6427428fe54258db8d4e31530',
    'BuildOutboxEvidence': '3ef0658c993f5e42eea866c9b97b85bf6ab78eff5701c31584c71977613cab73',
    'GetSendFailureEvidence': 'cc077df9dc81a205dbb162f269abfca413e64b188396618509313820fb5c923a',
    'IsStamped': '8b404cdc36ce249be50107e9534e806fc1c43a645eb1d57774109772e5e8030f',
    'GetOrCreateAudit': 'b852ddb8b9e0b69d3864ff2c85eeeb53f46082aeb604230f22ad8c7508d7619a',
    'ResolveRequiredSenderAccount': '2ab03ea7491efd8ab8f803ca627a4e270f64b374df824c21641cd258d6f0ab4f',
    'ReconcileNativeSentEmail': '8a951c9771eb36554cc34be8002b191c2e6be5ab5ab44e5557b22c50f95dc9b8',
    'HasNativeSentEmailEvidence': 'c49442ddd4c6f3b2273eecfab9e10a6af02b7c06a16bb4855a51c5e13e75484e',
    'IsMatchingInternalCanaryMessage': '1656e1d8e4479981a3b5f5199e6e984470c0df96767168dde86ec8788118b0c6',
    'FailAudit': 'd6b2b9420273ecb459ff3fa962f195470f7a726cf2d710fcb0182b882988b98a',
    'TryRenderApprovedInvoicePdf': 'acfef02e970a8ac3755451f2bfe03593c04cd8f4f0e776c440fbb98eb8d2d9da',
    'TryPrepareInvoiceEmail': '00758d1437708199a0a30ef4ad02bb07331bfd4c6b53897c4d6ff68283fa89f0',
    'TrySendInvoiceEmail': '4229ca98f6ea24a7ef17423dd01873a5d49e0c27e5b48725110dfe7dc16b3921',
    'BuildCommandEraEmailBody': '4d9f57072c441078c38409beec8290f03092dc2fbe71e0651ab81c80535b2377',
    'EscapeHtml': '4207c9848156d2c62da828798149f204ab788984a00f007b80bbae20265b5815',
    'ReadTextField': 'a8ebdc1878573ec33658b50aa184235b3ddb5903abe80cd7a4269fdeb0d553b6',
}


def _assert_gt_shared_email_preserved(source):
    bodies = _procedure_bodies(source)
    declarations = _procedure_declarations(source)
    expected_labels = {
        'ExpectedSenderLbl': 'consuelo@mtmlogix.com',
        'TestRecipientLbl': 'mario@mtmlogix.com',
        'LayoutNameLbl': 'MTMGTInvoiceStandard202606OnePage',
        'LogoUrlLbl': 'https://mhth6mu5g8.execute-api.us-east-1.amazonaws.com/assets/mtm-logix-email-logo-porcelain-v1.png',
        'ScenarioNotConfiguredErr': 'The MTM Invoice Customer Delivery email scenario is not assigned to an email account.',
        'WrongSenderErr': 'The MTM Invoice Customer Delivery email scenario is assigned to %1. It must be assigned to %2.',
    }
    labels = dict(re.findall(r"(\w+)\s*:\s*Label\s*'([^']*)'", source))
    for name, expected in expected_labels.items():
        assert labels.get(name) == expected, name
    for name, parameters, result in [
        ('GetExpectedSender', '', 'Text'),
        ('GetSenderDisplayName', '', 'Text'),
        ('GetDeliveryCc', 'Audit: Record "MTM Invoice Email Audit"', 'Text'),
        ('GetMexicoInvoiceEmailDetails', 'PostedInvoice: Record "Sales Invoice Header"', 'Text'),
        ('IsMexicoCompany', '', 'Boolean'),
        ('EnsureSupportedCompany', '', ''),
    ]:
        expected = f'local procedure {name}({parameters})' + (f': {result}' if result else '')
        assert declarations[name] == _al_tokens(expected), name
    assert bodies['IsMexicoCompany'] == _al_tokens("begin exit(CompanyName() = 'MTM_MX_PROD'); end")
    assert bodies['EnsureSupportedCompany'] == _al_tokens(
        "begin if not (CompanyName() in ['MTM_GT_PROD', 'MTM_MX_PROD']) then "
        "Error('Approved invoice delivery is scoped to MTM_GT_PROD and MTM_MX_PROD.'); end")
    for name, expected in [
        ('GetExpectedSender', 'exit(ExpectedSenderLbl);'),
        ('GetSenderDisplayName', "exit('Consuelo Velasquez');"),
        ('GetDeliveryCc', "exit('');"),
        ('SendApprovedInvoiceTestEmailToMario', "SendInternalCanary(PostedInvoice, '');"),
    ]:
        assert _gt_email_body(name, bodies[name]) == _al_tokens(expected), name
    # GT exits before every Mexico detail. Content after this proven early exit
    # may change for Mexico without introducing text into Guatemala's email.
    details = _specialize_gt(bodies['GetMexicoInvoiceEmailDetails'])
    prefix = _al_tokens("begin exit('');")
    assert details[:len(prefix)] == prefix, 'GT email must omit Mexico details'
    preparation_guard = _al_tokens("begin if not IsMexicoCompany() then "
        "Error('Explicit fiscal email preparation is scoped to MTM_MX_PROD.');")
    assert bodies['PrepareInvoiceEmailDelivery'][:len(preparation_guard)] == preparation_guard
    for name, expected in GT_EMAIL_PROCEDURE_HASHES.items():
        current = 'SendInternalCanary' if name == 'SendApprovedInvoiceTestEmailToMario' else name
        gt_tokens = _gt_email_declaration(name, declarations[current]) + _gt_email_body(name, bodies[current])
        assert _digest(gt_tokens) == expected, name


AUDIT_ADDITIONS = {
    'MtmInvoiceEmailAudit.Table.al': {
        'fields': r'(?m)^\s*field\((1[6-9]|2[0-4]);[^\n]+\}\s*$',
        'fields_hash': '0f1289a01f4694790401184509a059287c8d1d2ecbaf3d9e2e320269c5f7b8a3',
        'baseline_hash': '507ad58069f18c25cd568f084b8234136fc520676c49a193c51a010bf1e9c119',
    },
    'MtmInvoiceEmailAuditApi.Page.al': {
        'fields': r'(?m)^\s*field\((deliveryPrepared|ccRecipients|fiscalUuid|pdfAttachmentSha256|xmlAttachmentSha256|expectedExternalDocumentNumber|expectedAmountIncludingVat|expectedDueDate|preparedAt);[^\n]+\}\s*$',
        'fields_hash': '6394ed1eaf9be2242eb94bf01bd4f2bd1134006989c9ed95ce7114bd9bd41e2c',
        'baseline_hash': 'fb64f3ea9c33e0aa07961fa034aadfcc829e83ac591826c4fb374be7db27df86',
    },
}


def _assert_gt_additive_email_audit_preserved(name, source):
    allowed = AUDIT_ADDITIONS[name]
    fields = [match.group() for match in re.finditer(allowed['fields'], source)]
    assert len(fields) == 9
    assert _digest(_al_tokens(''.join(fields))) == allowed['fields_hash'], 'MX audit fields'
    source = re.sub(allowed['fields'], '', source)
    if name == 'MtmInvoiceEmailAudit.Table.al':
        pattern = r'(?ms)^\s*trigger On(?:Modify|Delete)\(\)\n.*?^\s*end;'
        triggers = [match.group() for match in re.finditer(pattern, source)]
        assert len(triggers) == 2
        # Both triggers guard only a prepared intent. Boolean defaults false,
        # and PrepareInvoiceEmailDelivery's first statement rejects Guatemala.
        # Any altered trigger or default requires explicit baseline review.
        assert _digest(_al_tokens(''.join(triggers))) == '77acda926c58c7410c1ae7f2a81d44b082ee6a3709a38565f3416e9bc4214fcd'
        source = re.sub(pattern, '', source)
    original_tokens = re.findall(r"//[^\n]*|'(?:[^']|'')*'|\"[^\"]*\"|[A-Za-z_0-9]+|[^\s]", source)
    assert _digest(original_tokens) == allowed['baseline_hash'], name


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
    # The Mexico patch extends these three API objects. Shared customer email
    # gets a separate exact GT execution comparison; every other object remains
    # token-identical, including GT stamping, credit memos, workers and setup.
    extended = {'MXPostedInvoiceCfdiMgt.Codeunit.al',
                'MtmCustomerInvoicingApi.PermissionSet.al',
                'PostedInvoiceFelDescriptionApi.Page.al'}
    for obj in provenance['objects']:
        path = ROOT / obj['repository_path']
        if path.name in extended:
            continue
        if path.name == 'MtmInvoiceCustomerEmailMgt.Codeunit.al':
            assert obj['token_sha256'] == 'feab26835501e7081165a9d3000ea5d53a6bd0a45967606ef2b9bc52eb5b8d18'
            _assert_gt_shared_email_preserved(path.read_text())
            continue
        if path.name in AUDIT_ADDITIONS:
            assert obj['token_sha256'] == AUDIT_ADDITIONS[path.name]['baseline_hash']
            _assert_gt_additive_email_audit_preserved(path.name, path.read_text())
            continue
        tokens = re.findall(r"//[^\n]*|'(?:[^']|'')*'|\"[^\"]*\"|[A-Za-z_0-9]+|[^\s]", path.read_text())
        digest = hashlib.sha256(json.dumps(tokens, ensure_ascii=False).encode()).hexdigest()
        assert digest == obj['token_sha256'], path.name


@pytest.mark.parametrize('before,after', [
    ("ExpectedSenderLbl: Label 'consuelo@mtmlogix.com'", "ExpectedSenderLbl: Label 'other@mtmlogix.com'"),
    ('Report::FacturaGTM', 'Report::OtherInvoice'),
    ('PostedInvoice.SetRecFilter();', ''),
    ('Audit."Native Sent Verified" := true;', 'Audit."Native Sent Verified" := false;'),
    ('Recipient := Customer."E-Mail";', "Recipient := 'other@mtmlogix.com';"),
    ("exit('Consuelo Velasquez');", "exit('Different Sender');"),
    ('Email.Send(EmailMessage, SenderAccount)', 'Email.Send(EmailMessage)'),
    ('LayoutNameLbl: Label', 'RemovedLayoutNameLbl: Label'),
    ('if IsMexicoCompany() then\n            Recipient := NormalizeRecipients(Recipient);',
     'Recipient := NormalizeRecipients(Recipient);'),
    ('var AttachmentBlob: Codeunit "Temp Blob";', 'var AttachmentBlob: Codeunit "Other Blob";'),
    ('[TryFunction]\n    local procedure TrySendInvoiceEmail', 'local procedure TrySendInvoiceEmail'),
    ("if not IsMexicoCompany() then\n            Error('Explicit fiscal email preparation is scoped to MTM_MX_PROD.');", ''),
])
def test_shared_email_guard_rejects_guatemala_behavior_regressions(before, after):
    source = (SRC / 'MtmInvoiceCustomerEmailMgt.Codeunit.al').read_text()
    assert before in source
    with pytest.raises(AssertionError):
        _assert_gt_shared_email_preserved(source.replace(before, after))


def test_shared_email_guard_allows_an_additional_explicitly_mexico_only_check():
    source = (SRC / 'MtmInvoiceCustomerEmailMgt.Codeunit.al').read_text()
    original = 'if IsMexicoCompany() then\n            Recipient := NormalizeRecipients(Recipient);'
    extra = "if IsMexicoCompany() then begin\n            Recipient := NormalizeRecipients(Recipient);\n            Error('Mexico-only review');\n        end;"
    assert original in source
    _assert_gt_shared_email_preserved(source.replace(original, extra))


@pytest.mark.parametrize('name,before,after', [
    ('MtmInvoiceEmailAudit.Table.al', 'if not xRec."Delivery Prepared" then', 'if xRec."Delivery Prepared" then'),
    ('MtmInvoiceEmailAudit.Table.al', 'field(4; Recipient; Text[250])', 'field(4; Recipient; Text[100])'),
    ('MtmInvoiceEmailAudit.Table.al', 'field(16; "Delivery Prepared"; Boolean) {', 'field(16; "Delivery Prepared"; Boolean) { InitValue = true;'),
    ('MtmInvoiceEmailAuditApi.Page.al', 'ModifyAllowed = false;', 'ModifyAllowed = true;'),
    ('MtmInvoiceEmailAuditApi.Page.al', 'field(recipient; Rec.Recipient)', 'field(recipient; Rec."CC Recipients")'),
])
def test_additive_audit_guard_rejects_guatemala_schema_or_trigger_regressions(name, before, after):
    source = (SRC / name).read_text()
    assert before in source
    with pytest.raises(AssertionError):
        _assert_gt_additive_email_audit_preserved(name, source.replace(before, after))
