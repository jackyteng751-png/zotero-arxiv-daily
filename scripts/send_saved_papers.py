"""Email an additional slice of an already ranked public-paper report.

No historical retrieval, Zotero fetch, reranking or LLM requests are performed.
"""
import html
import json
import math
import os
import re
from datetime import date
from pathlib import Path

from loguru import logger
from omegaconf import OmegaConf
from zotero_arxiv_daily.utils import send_email

ARXIV_URL = re.compile(r'https://arxiv\.org/abs/([A-Za-z0-9./-]+?)(?:v\d+)?$')


def paper_key(row: dict) -> str:
    match = ARXIV_URL.fullmatch(row['url'])
    if not match:
        raise ValueError('Saved report contains an invalid arXiv URL')
    return match.group(1)


def select_rows(report: dict, start_rank: int, count: int) -> list[dict]:
    if start_rank < 1 or not 1 <= count <= 200:
        raise ValueError('Require a positive starting rank and 1..200 papers')
    papers = report['papers']
    previous_count = report['selected_count']
    if not isinstance(previous_count, int) or not 0 <= previous_count <= len(papers):
        raise ValueError('Invalid previous selection count')
    if start_rank <= previous_count:
        raise ValueError('Requested ranks overlap the previous email')
    rows = papers[start_rank - 1:start_rank - 1 + count]
    if len(rows) != count:
        raise ValueError('Not enough saved candidates; refusing a partial email')
    previous_ids = {paper_key(p) for p in papers[:previous_count]}
    keys = [paper_key(p) for p in rows]
    if len(set(keys)) != count or set(keys) & previous_ids:
        raise ValueError('Duplicate papers found; refusing to resend them')
    for row in rows:
        if not isinstance(row['title'], str) or not row['title'].strip():
            raise ValueError('Missing paper title')
        if not math.isfinite(float(row['score'])):
            raise ValueError('Invalid relevance score')
    return rows


def send_saved_report(config, report: dict, start_rank: int, count: int):
    start = date.fromisoformat(report['start']).isoformat()
    end = date.fromisoformat(report['end']).isoformat()
    rows = select_rows(report, start_rank, count)
    last_rank = start_rank + count - 1
    parts = [
        '<!doctype html><html><body>',
        f'<h2>Additional arXiv papers: {start} to {end}</h2>',
        f'<p>{count} additional papers, original relevance ranks {start_rank}–{last_rank}. '
        'The previously emailed top papers are excluded.</p>',
        '<p>This is a title/link supplement from the same approximate historical '
        'candidate report. No new AI summaries were generated. '
        'The daily paper limit and schedule are unchanged.</p>',
        f'<ol start="{start_rank}">',
    ]
    for row in rows:
        url = html.escape(row['url'], quote=True)
        title = html.escape(row['title'])
        score = float(row['score'])
        parts.append(f'<li><p><a href="{url}">{title}</a><br>'
                     f'Relevance score: {score:.2f} / 10</p></li>')
    parts.append('</ol></body></html>')
    subject = f'arXiv additional {count} papers {start} to {end} (ranks {start_rank}-{last_rank})'
    send_email(config, ''.join(parts), subject=subject)
    logger.info(f'Additional-paper email sent successfully: {count} papers; ranks {start_rank}-{last_rank}')
    receipt = {'start': start, 'end': end, 'start_rank': start_rank,
               'end_rank': last_rank, 'selected_count': count,
               'source_run_id': os.environ.get('SOURCE_RUN_ID'),
               'smtp_accepted': True, 'llm_requested': False, 'papers': rows}
    path = Path('outputs/additional-papers/report.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    path = Path('outputs/saved-papers/report.json')
    if path.stat().st_size > 10_000_000:
        raise ValueError('Saved report is unexpectedly large')
    report = json.loads(path.read_text(encoding='utf-8'))
    config = OmegaConf.merge(OmegaConf.load('config/base.yaml'),
                             OmegaConf.load('config/custom.yaml'))
    send_saved_report(config, report, int(os.environ['START_RANK']),
                      int(os.environ['PAPER_COUNT']))


if __name__ == '__main__':
    main()
