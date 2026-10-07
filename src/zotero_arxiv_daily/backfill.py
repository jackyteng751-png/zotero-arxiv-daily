"""One-off approximate recovery; never changes the daily RSS pipeline.

The original submission timestamp is NOT the actual announcement timestamp.
Infer the ordinary announcement schedule and the following Beijing 06:00 run
after RSS refresh. Moderation delays, holidays, replacements and cross-list
changes cannot be reconstructed by this first-submission search.
"""
import html
import json
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import hydra
import requests
from loguru import logger
from omegaconf import DictConfig
from time import sleep

from zotero_arxiv_daily.construct_email import render_email
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.retriever.arxiv_retriever import _retry_delay
from zotero_arxiv_daily.utils import send_email

API_URL = 'https://export.arxiv.org/api/query'
PAGE_SIZE = 100
MAX_RESULTS = 20000
EASTERN = ZoneInfo('America/New_York')


def missed_dates(start: str, end: str, excluded: str) -> list[date]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not 0 <= (last - first).days <= 90:
        raise ValueError('Backfill range must be between 1 and 91 days')
    skip = {date.fromisoformat(v.strip()) for v in excluded.split(',') if v.strip()}
    return [first + timedelta(days=i) for i in range((last-first).days+1)
            if first + timedelta(days=i) not in skip]


def inferred_run_date(submitted: datetime) -> date:
    """Ordinary Eastern 14:00 cutoff -> announcement -> refreshed RSS -> 06:00 CST.

    Before-cutoff Friday submissions announce Sunday; after-cutoff Friday
    through before-cutoff Monday announce Monday. No Friday/Saturday postings.
    The 06:00 Beijing cron is BEFORE the same day's RSS refresh, so it reads
    the preceding refresh. Hence the run date is announcement date + 2 days.
    """
    eastern = submitted.astimezone(EASTERN)
    cutoff = eastern.date()
    if eastern.time() >= time(14):
        cutoff += timedelta(days=1)
    if cutoff.weekday() == 4:
        announcement = cutoff + timedelta(days=2)
    elif cutoff.weekday() == 5:
        announcement = cutoff + timedelta(days=2)
    elif cutoff.weekday() == 6:
        announcement = cutoff + timedelta(days=1)
    else:
        announcement = cutoff
    return announcement + timedelta(days=2)


def fetch_page(params: dict):
    for attempt in range(5):
        headers = {}
        try:
            response = requests.get(API_URL, params=params, timeout=(10, 90))
            headers = response.headers
            if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
                response.raise_for_status()
            if response.status_code >= 400:
                raise requests.RequestException(f'HTTP {response.status_code}')
            feed = feedparser.parse(response.content)
            if feed.get('bozo') or 'opensearch_totalresults' not in feed.feed:
                raise ValueError('Invalid arXiv API feed; refusing partial recovery')
            return feed
        except requests.HTTPError:
            raise
        except (requests.RequestException, ValueError) as exc:
            if attempt == 4:
                raise RuntimeError('Historical arXiv retrieval exhausted retries') from exc
            delay = _retry_delay(attempt, headers)
            logger.warning(f'Historical arXiv request failed ({type(exc).__name__}); retry in {delay:.0f}s')
            sleep(delay)
    raise AssertionError('unreachable')


def retrieve_candidates(config: DictConfig, days: list[date]) -> tuple[list[Paper], dict]:
    if not days:
        return [], {}
    categories = list(config.source.arxiv.category or [])
    if not categories or any(not re.fullmatch(r'[A-Za-z0-9.*-]+', c) for c in categories):
        raise ValueError('Backfill requires valid arXiv category names')
    # A full preceding week includes the weekend submission cutoff window.
    lower = min(days) - timedelta(days=7)
    upper = max(days)
    query = '(' + ' OR '.join(f'cat:{c}' for c in categories) + ')'
    query += f' AND submittedDate:[{lower:%Y%m%d}0000 TO {upper:%Y%m%d}2359]'
    target = set(days)
    candidates, metadata, seen = [], {}, set()
    offset, expected = 0, None
    include_cross = (config.source.arxiv.get('include_cross_list', False)
                     or config.source.arxiv.get('include_all_announce_types', False))
    while True:
        if offset:
            sleep(3)  # arXiv API asks for at least three seconds between requests.
        feed = fetch_page({'search_query': query, 'start': offset,
                           'max_results': PAGE_SIZE, 'sortBy': 'submittedDate',
                           'sortOrder': 'ascending'})
        total = int(feed.feed.opensearch_totalresults)
        if total > MAX_RESULTS:
            raise RuntimeError('Too many results; narrow the dates instead of silently truncating')
        if expected is None:
            expected = total
        elif total != expected:
            raise RuntimeError('arXiv result count changed during pagination; retry later')
        entries = feed.entries
        if not entries and offset < total:
            raise RuntimeError('Empty arXiv page before end; refusing incomplete email')
        for entry in entries:
            submitted = datetime.fromisoformat(entry.published.replace('Z', '+00:00'))
            run_day = inferred_run_date(submitted)
            if run_day not in target:
                continue
            primary = entry.get('arxiv_primary_category', {}).get('term', '')
            if not include_cross and primary not in categories:
                continue
            paper_id = entry.id.rsplit('/abs/', 1)[-1]
            base_id = re.sub(r'v\d+$', '', paper_id)
            if base_id in seen:
                continue
            seen.add(base_id)
            url = f'https://arxiv.org/abs/{paper_id}'
            paper = Paper(source='arxiv', title=' '.join(entry.title.split()),
                          authors=[a.name for a in entry.authors],
                          abstract=' '.join(entry.summary.split()), url=url,
                          pdf_url=f'https://arxiv.org/pdf/{paper_id}')
            candidates.append(paper)
            metadata[url] = {'original_submission_utc': submitted.isoformat(),
                             'estimated_missed_run_date': run_day.isoformat()}
        offset += len(entries)
        logger.info(f'Historical pages processed: {offset}/{total}; candidates: {len(candidates)}')
        if offset >= total:
            break
    return candidates, metadata


def select_recommendations(papers: list[Paper], limit: int) -> list[Paper]:
    if limit == -1:
        return papers
    if limit < 0:
        raise ValueError('max_paper_num must be -1 or nonnegative')
    return papers[:limit]


def run_backfill(config: DictConfig):
    start, end = os.environ['BACKFILL_START'], os.environ['BACKFILL_END']
    excluded = os.environ.get('BACKFILL_EXCLUDE', '')
    days = missed_dates(start, end, excluded)
    if not days:
        raise ValueError('No missed dates selected')
    candidates, metadata = retrieve_candidates(config, days)
    executor = Executor(config)
    corpus = executor.filter_corpus(executor.fetch_zotero_corpus())
    if not corpus:
        raise RuntimeError('No Zotero corpus remains; no catch-up email sent')
    ranked = executor.reranker.rerank(candidates, corpus) if candidates else []
    selected = select_recommendations(ranked, int(config.executor.max_paper_num))
    # Historical catch-up uses abstracts only: no bulk full-text download and
    # no guessed affiliations. Only selected papers go to the configured LLM.
    for paper in selected:
        paper.generate_tldr(executor.openai_client, config.llm)
    report = {'start': start, 'end': end, 'missed_run_dates': [d.isoformat() for d in days],
              'excluded_successful_dates': excluded, 'approximate': True,
              'candidate_count': len(candidates), 'selected_count': len(selected),
              'scope': 'first submissions; inferred schedule; abstracts only; revisions not recovered',
              'papers': [{'title': p.title, 'url': p.url, 'score': float(p.score),
                          **metadata[p.url]} for p in ranked]}
    path = Path('outputs/backfill/report.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    notice = (f'<p>Historical catch-up: {html.escape(start)} to {html.escape(end)}. '
              f'{len(days)} missed run dates; {len(candidates)} candidates; {len(selected)} recommendations.</p>'
              '<p>Approximate recovery from original submission timestamps and normal arXiv schedule. '
              'Successful run dates were excluded by estimate. Moderation delays, holidays, revisions '
              'and historical cross-list changes may cause omissions or duplicates. '
              'Summaries use abstracts only; the original daily configuration is unchanged.</p>')
    content = render_email(selected)
    content = content.replace('<body>', '<body>' + notice, 1)
    send_email(config, content, subject=f'arXiv catch-up {start} to {end} (approximate)')
    logger.info(f'Catch-up email sent successfully; selected {len(selected)} papers')


@hydra.main(version_base=None, config_path='../../config', config_name='default')
def main(config: DictConfig):
    run_backfill(config)


if __name__ == '__main__':
    main()
