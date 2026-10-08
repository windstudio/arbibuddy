"""User-observable layout regressions exercised through public document.render."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from scripts.documents.public import render
from tests.documents.model_led_occurrence_fixtures import with_occurrence_bindings
from tests.documents.model_led_program_documents_fixtures import create_case, request_for
from tests.documents.model_led_demand_forced_fixtures import create_case as create_demand_case, demand_request
from tests.documents.test_model_led_document_render import _create_archive, _defense_request

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{' + NS['w'] + '}'


def _read_parts(path):
    with ZipFile(path) as package:
        return {name: ET.fromstring(package.read('word/' + name + '.xml')) for name in ('document', 'styles', 'numbering')}


def _text(paragraph):
    return ''.join(item.text or '' for item in paragraph.findall('.//w:t', NS))


def _list_number(paragraph, styles):
    num = paragraph.find('w:pPr/w:numPr/w:numId', NS)
    if num is None:
        style = paragraph.find('w:pPr/w:pStyle', NS)
        if style is not None:
            match = styles.find(f"w:style[@w:styleId='{style.get(W + 'val')}']/w:pPr/w:numPr/w:numId", NS)
            num = match
    return None if num is None else num.get(W + 'val')


class PublicLayoutFeedbackTests(unittest.TestCase):
    def test_demand_explicit_signature_lines_are_separate_from_communication(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _confirmation = create_demand_case(workspace)
            request = demand_request(case_id, revision, mode='candidate')
            result = render(request, workspace)
            self.assertTrue(result['ok'], result)
            paragraphs = _read_parts(Path(result['result']['canonical_docx']))['document'].findall('w:body/w:p', NS)
            texts = [_text(p) for p in paragraphs]
            signature_index = texts.index('通知人：张三')
            self.assertEqual(texts[signature_index - 1], '')
            self.assertEqual(paragraphs[signature_index].find('w:pPr/w:pStyle', NS).get(W + 'val'), 'ArbiBuddyLegalSignature')
            self.assertIn('本函用于履行催告和沟通，不构成解除劳动合同通知。', texts)
            self.assertEqual(texts.count('通知人：张三'), 1)

    def test_demand_caller_signature_keeps_multiline_layout_in_candidate_and_final(self):
        for mode in ('candidate', 'external_final'):
            with self.subTest(mode=mode), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                _archive, case_id, revision, _confirmation = create_demand_case(workspace)
                request = demand_request(case_id, revision, mode=mode)
                request['sections'][-1]['full_text'] = request['sections'][-1]['full_text'].replace('通知人：张三', '催告人：张三')
                for binding in request['locked_bindings']:
                    if binding['kind'] == 'signature':
                        binding['rendered_value'] = '催告人：张三'
                result = render(with_occurrence_bindings(request), workspace)
                self.assertTrue(result['ok'], result)
                parts = _read_parts(Path(result['result']['canonical_docx']))
                paragraphs = parts['document'].findall('w:body/w:p', NS)
                texts = [_text(p) for p in paragraphs]
                index = texts.index('催告人：张三')
                self.assertEqual(texts[index - 1], '', 'body and explicit caller signature need a blank paragraph')
                self.assertEqual(texts[index + 1], '2026年9月8日')
                for paragraph in paragraphs[index:index + 2]:
                    self.assertEqual(paragraph.find('w:pPr/w:pStyle', NS).get(W + 'val'), 'ArbiBuddyLegalSignature')
                    self.assertEqual(paragraph.find('w:pPr/w:ind', NS).get(W + 'leftChars'), '1600')
                self.assertEqual(texts.count('催告人：张三'), 1, 'layout must not rewrite or duplicate the model signature')

    def test_demand_embedded_signature_text_is_not_guessed_or_rewritten(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _confirmation = create_demand_case(workspace)
            request = demand_request(case_id, revision, mode='candidate')
            communication = request['sections'][-1]
            communication['full_text'] = '沟通渠道请另行确认。通知人：张三\n2026年9月8日'
            result = render(with_occurrence_bindings(request), workspace)
            self.assertTrue(result['ok'], result)
            paragraphs = _read_parts(Path(result['result']['canonical_docx']))['document'].findall('w:body/w:p', NS)
            self.assertIn('沟通渠道请另行确认。通知人：张三', [_text(p) for p in paragraphs])
            self.assertFalse(any(p.find('w:pPr/w:pStyle', NS).get(W + 'val') == 'ArbiBuddyLegalSignature' for p in paragraphs if p.find('w:pPr/w:pStyle', NS) is not None))

    def test_independent_request_and_attachment_lists_each_start_at_one(self):
        with TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root)
            _archive, case_id, revision, _confirmation = create_case(workspace)
            request = request_for(case_id, revision, 'company_deregistration_restriction_request_shanghai', mode='candidate')
            requests = ['请求核实注销风险。', '请求视情协调。']
            attachments = ['仲裁受理通知书。', '注销风险材料。']
            for section in request['sections']:
                if section['heading'] == '申请事项':
                    section['full_text'] = '\n'.join(requests)
                elif section['heading'] == '附件':
                    section['full_text'] = '\n'.join(attachments)
            result = render(request, workspace)
            self.assertTrue(result['ok'], result)
            parts = _read_parts(Path(result['result']['canonical_docx']))
            paragraphs = parts['document'].findall('w:body/w:p', NS)
            ids = {_text(p): _list_number(p, parts['styles']) for p in paragraphs if _text(p) in requests + attachments}
            self.assertEqual(set(ids), set(requests + attachments))
            self.assertEqual(ids[requests[0]], ids[requests[1]], 'same request list must continue')
            self.assertEqual(ids[attachments[0]], ids[attachments[1]], 'same attachment list must continue')
            self.assertNotEqual(ids[requests[0]], ids[attachments[0]], 'independent attachment list must restart, not continue requests')
            for num_id in (ids[requests[0]], ids[attachments[0]]):
                number = parts['numbering'].find(f"w:num[@w:numId='{num_id}']", NS)
                override = number.find('w:lvlOverride/w:startOverride', NS)
                self.assertIsNotNone(override, 'independent list must explicitly start at one')
                self.assertEqual(override.get(W + 'val'), '1')

    def test_defense_and_program_closings_use_multiline_indented_signature_blocks(self):
        for kind in ('arbitration_defense', 'company_deregistration_restriction_request_shanghai'):
            with self.subTest(kind=kind), TemporaryDirectory() as temporary_root:
                workspace = Path(temporary_root)
                if kind == 'arbitration_defense':
                    _archive, case_id, revision = _create_archive(workspace)
                    request = _defense_request(case_id, revision, mode='candidate')
                    lines = ['答辩人：张三', '联系电话：13800000000', '签名：________________', '2026年9月7日']
                else:
                    _archive, case_id, revision, _confirmation = create_case(workspace)
                    request = request_for(case_id, revision, kind, mode='candidate')
                    lines = ['申请人：张三', '联系电话：13800000000', '签名：________________', '2026年9月8日']
                closing = next(section for section in request['sections'] if section['heading'] == '落款')
                # Keep the existing fixture's bound date unchanged.
                bound_date = next(binding['rendered_value'] for binding in request['locked_bindings'] if binding['kind'] == 'date' and binding['rendered_value'] in closing['full_text'])
                lines[-1] = bound_date
                closing['full_text'] = '\n'.join(lines)
                request = with_occurrence_bindings(request)
                result = render(request, workspace)
                self.assertTrue(result['ok'], result)
                paragraphs = _read_parts(Path(result['result']['canonical_docx']))['document'].findall('w:body/w:p', NS)
                texts = [_text(p) for p in paragraphs]
                self.assertNotIn('落款', texts)
                for text in lines:
                    paragraph = paragraphs[texts.index(text)]
                    self.assertEqual(paragraph.find('w:pPr/w:pStyle', NS).get(W + 'val'), 'ArbiBuddyLegalSignature')
                    self.assertEqual(paragraph.find('w:pPr/w:ind', NS).get(W + 'leftChars'), '1600')
                    alignment = paragraph.find('w:pPr/w:jc', NS)
                    self.assertIn(None if alignment is None else alignment.get(W + 'val'), (None, 'left'))
                self.assertEqual(texts[texts.index(lines[0]) - 1], '', 'body and signature block need an empty paragraph')


if __name__ == '__main__':
    unittest.main()
