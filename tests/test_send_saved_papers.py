import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('send_saved_papers',
    Path(__file__).resolve().parents[1] / 'scripts/send_saved_papers.py')
saved = importlib.util.module_from_spec(spec)
spec.loader.exec_module(saved)


def make_report():
    return {'start': '2026-10-22', 'end': '2026-10-26', 'selected_count': 20,
            'papers': [{'title': f'Paper {i}', 'url': f'https://arxiv.org/abs/2610.{i:05d}v1',
                        'score': 8 - i / 100} for i in range(1, 81)]}


def test_selects_next_40_without_first_20():
    report = make_report()
    rows = saved.select_rows(report, 21, 40)
    assert len(rows) == 40
    assert rows[0]['title'] == 'Paper 21'
    assert rows[-1]['title'] == 'Paper 60'


@pytest.mark.parametrize('start,count', [(0,40), (1,40), (20,40), (21,0), (21,201), (70,40)])
def test_invalid_or_overlapping_selection(start, count):
    with pytest.raises(ValueError):
        saved.select_rows(make_report(), start, count)


def test_detects_duplicate_versions_of_previous_paper():
    report = make_report()
    report['papers'][20]['url'] = 'https://arxiv.org/abs/2610.00001v2'
    with pytest.raises(ValueError, match='Duplicate'):
        saved.select_rows(report, 21, 40)


def test_rejects_non_arxiv_links():
    report = make_report()
    report['papers'][20]['url'] = 'javascript:alert(1)'
    with pytest.raises(ValueError):
        saved.select_rows(report, 21, 40)


def test_sends_one_40_paper_email_and_public_receipt(config, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    report = make_report()
    report['papers'][20]['title'] = '<script>untrusted</script>'
    original_limit = config.executor.max_paper_num
    sent = []
    monkeypatch.setattr(saved, 'send_email', lambda cfg, content, subject: sent.append((content,subject)))
    saved.send_saved_report(config, report, 21, 40)
    assert len(sent) == 1
    assert sent[0][0].count('<li>') == 40
    assert '&lt;script&gt;' in sent[0][0] and '<script>' not in sent[0][0]
    assert 'No new AI summaries' in sent[0][0]
    assert 'ranks 21-60' in sent[0][1]
    assert config.executor.max_paper_num == original_limit
    receipt = json.loads(Path('outputs/additional-papers/report.json').read_text())
    assert receipt['selected_count'] == 40
    assert receipt['smtp_accepted'] and not receipt['llm_requested']
