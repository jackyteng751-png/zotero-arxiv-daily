# Upgrade record (2026-10-02)

Original version: `d64df3a` (backup branch `backup/before-upstream-sync-2026-10-02`).
Upstream version: `edc6863` (2026-10-01).
Migration branch: `migration/upstream-2026-10-02`.

The updated retriever builds paper metadata from the arXiv RSS feed. It no longer
requests metadata from `export.arxiv.org/api/query`, the endpoint responsible for
the reported HTTP 429/503 failure. RSS failures now use exponential backoff,
random jitter, and the server's `Retry-After` header, with five attempts.

## Existing Actions settings

Both workflows run `scripts/prepare_config.py` before the application. If
`CUSTOM_CONFIG` is present it is authoritative. Otherwise, existing settings are
converted automatically at runtime:

| Old setting | New config field |
| --- | --- |
| ZOTERO_ID / ZOTERO_KEY | zotero.user_id / zotero.api_key |
| ARXIV_QUERY | source.arxiv.category (split on `+`) |
| SMTP_SERVER / SMTP_PORT | email.smtp_server / email.smtp_port |
| SENDER / RECEIVER / SENDER_PASSWORD | email sender, receiver, sender_password |
| OPENAI_API_KEY / OPENAI_API_BASE | llm.api.key / llm.api.base_url |
| MODEL_NAME / LANGUAGE | llm.generation_kwargs.model / llm.language |
| SEND_EMPTY / MAX_PAPER_NUM | executor.send_empty / executor.max_paper_num |

Without `CUSTOM_CONFIG`, `source.arxiv.include_all_announce_types` is set to true
to preserve the original custom change: include every RSS announcement type.
With `CUSTOM_CONFIG`, explicitly set this option to true if you want that behavior.
`include_cross_list` alone only includes new and cross-listed papers.

Secrets stay in GitHub Actions; generated YAML keeps environment references for
credentials. Workflows no longer print the custom configuration into logs.
The checkout uses the selected branch of this fork, so REPOSITORY and REF no longer
redirect execution to another repository or override the branch under test.
The original daily schedule remains `0 22 * * *` (06:00 Asia/Shanghai).

## Compatibility gates

The new upstream requires an OpenAI-compatible LLM API. If USE_LLM_API is false
or unset, automatic migration stops before running the application. Do not switch
to a paid API without deliberately choosing the provider and model.

ZOTERO_IGNORE is retained as zotero.legacy_ignore_patterns and evaluated by the
original gitignore parser, preserving exclusion and negation rules. New
zotero.ignore_path settings continue to use glob semantics. SEND_EMPTY can be
read from either Variables or Secrets, with Variables taking precedence.

Example CUSTOM_CONFIG (replace categories, server, model and rules with your own):

```yaml
zotero:
  user_id: ${oc.env:ZOTERO_ID}
  api_key: ${oc.env:ZOTERO_KEY}
  ignore_path: null
email:
  sender: ${oc.env:SENDER}
  receiver: ${oc.env:RECEIVER}
  sender_password: ${oc.env:SENDER_PASSWORD}
  smtp_server: smtp.example.com
  smtp_port: 465
llm:
  api:
    key: ${oc.env:OPENAI_API_KEY}
    base_url: ${oc.env:OPENAI_API_BASE,https://api.openai.com/v1}
  generation_kwargs:
    model: your-existing-model
  language: Chinese
source:
  arxiv:
    category: [quant-ph]
    include_all_announce_types: true
executor:
  source: [arxiv]
  max_paper_num: 100
  send_empty: false
```

## Deployment and rollback

Local validation: Python 3.13.16, 123 tests passed and 1 slow model-download test
excluded. Regression checks cover 429/503 recovery, Retry-After seconds and dates,
retry exhaustion, announcement types, credential references, legacy config
round-tripping and LLM API parameters. Workflow YAML and Python syntax also passed.
The actual configuration was verified on 2026-10-07: CI run 37559851449 passed;
Test run 37559949118 retrieved papers, generated summaries without TLDR failures,
and logged Email sent successfully. Three optional affiliation fields had list
parsing errors; a follow-up parser accepts JSON and Python string lists using
json.loads or ast.literal_eval, with regression tests and no second live model run.
The original Zotero exclusion rules remain unchanged. The core dependency versions
match uv.lock; heavy model dependencies were not installed for the offline tests.

Push the backup and migration branches after GitHub authentication is available.
Run the Test workflow on the migration branch using the repository's existing
Secrets and Variables. This sends one debug email and invokes the configured LLM.
Check paper scope, collection filtering, SMTP delivery and LLM behavior before
merging the migration branch into main. Do not replace main before these checks.

For rollback, restore the files from the backup branch in a new rollback commit;
avoid a force push. Secrets and Variables are not changed by a Git merge.

The upstream now uses Python 3.13. Automatic legacy conversion keeps the old
`avsolatorio/GIST-small-Embedding-v0` embedding model and temperature=0.
When using CUSTOM_CONFIG, the upstream embedding model is the default unless
you explicitly override reranker.local.model and encode_kwargs.
# Historical catch-up (2026-10-07)

`Historical catch-up (approximate)` is a manual-only workflow, independent of
the daily RSS workflow. Its date inputs are missed Beijing 06:00 run dates,
not submission dates. Exclude successful run dates explicitly. It searches
original submissions, estimates their ordinary announcement/RSS schedule,
deduplicates paper IDs, ranks all candidates against the current filtered
Zotero corpus, and sends one aggregate email using the existing overall
`MAX_PAPER_NUM` limit (`-1` means all). Only selected abstracts are sent to
the existing model for summaries; historical full text/affiliations are not
requested. The public-paper candidate report is retained as an artifact for
30 days, without Zotero corpus, email addresses or credentials.

This cannot reconstruct historical RSS exactly: moderation delays, holidays,
old-paper replacements and later cross-listing changes may cause omissions
or duplicates. Successful days are excluded by estimated schedule, not by a
reliable past-sent-paper ledger. Do not rerun the send workflow automatically
after an uncertain SMTP result, since that may duplicate the email.

Zotero collection paths are fetched afresh each run. Sync collection changes
to Zotero's cloud, then update the GitHub Actions repository variable
`ZOTERO_IGNORE` if excluded folders were renamed/moved. One gitignore-style
path per line; keep the existing trailing `/` for folders. With no
`CUSTOM_CONFIG`, all nonexcluded papers with abstracts are used. If a
`CUSTOM_CONFIG` is later added, its `zotero.include_path`/`ignore_path` become
the authoritative filtering configuration instead of the legacy variable.
