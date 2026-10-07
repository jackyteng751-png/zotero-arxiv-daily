from datetime import date, datetime
from types import SimpleNamespace

import pytest
from zotero_arxiv_daily import backfill as b


def test_missed_dates():
    dates = b.missed_dates('2026-09-12', '2026-10-06',
                          '2026-09-13,2026-09-14,2026-09-19,2026-09-20,2026-09-21,2026-09-22')
    assert len(dates) == 19
    assert date(2026, 9, 22) not in dates
    assert dates[-1] == date(2026, 10, 6)


@pytest.mark.parametrize('start,end', [('2026-10-06','2026-09-12'),
                                      ('2026-01-01','2026-10-06')])
def test_invalid_range(start, end):
    with pytest.raises(ValueError):
        b.missed_dates(start, end, '')


@pytest.mark.parametrize('timestamp,expected', [
    ('2026-09-10T17:59:00+00:00', '2026-09-12'),  # Thu before 14:00 EDT
    ('2026-09-10T18:00:00+00:00', '2026-09-15'),  # Thu after cutoff -> Sun -> Tue
    ('2026-09-11T17:59:00+00:00', '2026-09-15'),
    ('2026-09-11T18:00:00+00:00', '2026-09-16'),  # Fri after cutoff -> Mon -> Wed
    ('2026-09-13T12:00:00+00:00', '2026-09-16'),
    ('2026-09-14T17:59:00+00:00', '2026-09-16'),
    ('2026-09-14T18:00:00+00:00', '2026-09-17'),
    ('2026-01-06T18:59:00+00:00', '2026-01-08'),  # winter EST, cutoff 19 UTC
])
def test_inferred_schedule(timestamp, expected):
    assert b.inferred_run_date(datetime.fromisoformat(timestamp)) == date.fromisoformat(expected)


def test_limits():
    assert b.select_recommendations([1, 2, 3], -1) == [1, 2, 3]
    assert b.select_recommendations([1, 2, 3], 1) == [1]
    assert b.select_recommendations([1], 0) == []
    with pytest.raises(ValueError):
        b.select_recommendations([], -2)


def entry(id='2609.12345v1', published='2026-09-10T12:00:00Z'):
    return SimpleNamespace(id=f'http://arxiv.org/abs/{id}', published=published,
                           title='A  paper', summary='An abstract',
                           authors=[SimpleNamespace(name='Author')],
                           get=lambda key, default=None: {'term': 'cs.AI'} if key == 'arxiv_primary_category' else default)


def test_retrieval_filters_dates_and_deduplicates(config, monkeypatch):
    calls = []
    def fetch(params):
        calls.append(params)
        return SimpleNamespace(feed=SimpleNamespace(opensearch_totalresults='3'),
                               entries=[entry(), entry(), entry('2609.45678', '2026-09-14T12:00:00Z')])
    monkeypatch.setattr(b, 'fetch_page', fetch)
    papers, metadata = b.retrieve_candidates(config, [date(2026, 9, 12)])
    assert len(papers) == 1
    assert papers[0].url == 'https://arxiv.org/abs/2609.12345v1'
    assert papers[0].full_text is None
    assert metadata[papers[0].url]['estimated_missed_run_date'] == '2026-09-12'
    assert 'cat:cs.AI OR cat:cs.CV' in calls[0]['search_query']


def test_incomplete_page_fails(config, monkeypatch):
    monkeypatch.setattr(b, 'fetch_page', lambda params: SimpleNamespace(
        feed=SimpleNamespace(opensearch_totalresults='5'), entries=[]))
    with pytest.raises(RuntimeError, match='Empty arXiv page'):
        b.retrieve_candidates(config, [date(2026, 9, 12)])


def test_retry_http_429(monkeypatch):
    replies = iter([SimpleNamespace(status_code=429, headers={'Retry-After': '42'}),
                    SimpleNamespace(status_code=200, headers={}, content=b'feed')])
    monkeypatch.setattr(b.requests, 'get', lambda *args, **kwargs: next(replies))
    delays = []
    monkeypatch.setattr(b, 'sleep', delays.append)
    # Real feedparser objects support get() and attribute/dict access.
    parsed = b.feedparser.FeedParserDict(feed={'opensearch_totalresults': '0'}, entries=[])
    monkeypatch.setattr(b.feedparser, 'parse', lambda content: parsed)
    assert b.fetch_page({}) is parsed
    assert len(delays) == 1 and delays[0] >= 42


def test_backfill_sends_once_and_report_has_no_corpus(config, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('BACKFILL_START', '2026-09-12')
    monkeypatch.setenv('BACKFILL_END', '2026-09-12')
    monkeypatch.setenv('BACKFILL_EXCLUDE', '')
    p = b.Paper('arxiv', 'Public paper', ['Author'], 'Public abstract', 'https://arxiv.org/abs/2609.12345')
    p.score = 1.5
    monkeypatch.setattr(b, 'retrieve_candidates', lambda config, days: ([p], {p.url: {}}))
    fake = SimpleNamespace(fetch_zotero_corpus=lambda: ['PRIVATE CORPUS'],
                           filter_corpus=lambda corpus: corpus,
                           reranker=SimpleNamespace(rerank=lambda papers, corpus: papers),
                           openai_client=None)
    monkeypatch.setattr(b, 'Executor', lambda cfg: fake)
    monkeypatch.setattr(b.Paper, 'generate_tldr', lambda *args: None)
    sent = []
    monkeypatch.setattr(b, 'send_email', lambda config, content, subject: sent.append((content, subject)))
    b.run_backfill(config)
    assert len(sent) == 1
    assert 'approximate' in sent[0][1]
    report = (tmp_path / 'outputs/backfill/report.json').read_text()
    assert 'PRIVATE CORPUS' not in report
    assert 'selected_count' in report
