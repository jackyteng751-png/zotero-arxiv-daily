"""Bridge existing Actions settings to Hydra without logging secret values."""
import os
from pathlib import Path

from omegaconf import OmegaConf


def prepare_config(environ, config_dir):
    base = OmegaConf.load(config_dir / 'base.yaml')
    custom = environ.get('CUSTOM_CONFIG', '').strip()
    if custom:
        config = OmegaConf.merge(base, OmegaConf.create(custom))
    else:
        if environ.get('USE_LLM_API', '').lower() not in {'true', '1'}:
            raise ValueError('This upstream version requires an LLM API. Set USE_LLM_API=true and OPENAI_API_KEY, or supply CUSTOM_CONFIG. The previous local LLM mode cannot be migrated automatically.')
        config = base
        mapping = {
            'ZOTERO_ID': 'zotero.user_id', 'ZOTERO_KEY': 'zotero.api_key',
            'SENDER': 'email.sender', 'RECEIVER': 'email.receiver',
            'SENDER_PASSWORD': 'email.sender_password', 'SMTP_SERVER': 'email.smtp_server',
            'OPENAI_API_KEY': 'llm.api.key', 'OPENAI_API_BASE': 'llm.api.base_url',
            'MODEL_NAME': 'llm.generation_kwargs.model', 'LANGUAGE': 'llm.language',
        }
        defaults = {'OPENAI_API_BASE': 'https://api.openai.com/v1', 'MODEL_NAME': 'gpt-4o', 'LANGUAGE': 'English'}
        for key, path in mapping.items():
            if environ.get(key):
                OmegaConf.update(config, path, '${oc.env:' + key + '}')
            elif key in defaults:
                OmegaConf.update(config, path, defaults[key])
        for key, path in {'SMTP_PORT': 'email.smtp_port', 'MAX_PAPER_NUM': 'executor.max_paper_num'}.items():
            if environ.get(key):
                OmegaConf.update(config, path, int(environ[key]))
        config.executor.send_empty = environ.get('SEND_EMPTY', '').lower() in {'true', '1'}
        query = environ.get('ARXIV_QUERY', '').strip()
        if not query:
            raise ValueError('ARXIV_QUERY is required when CUSTOM_CONFIG is unset.')
        config.source.arxiv.category = query.split('+')
        config.source.arxiv.include_all_announce_types = True
        config.executor.source = ['arxiv']
        # Retain the original embedding model and LLM sampling settings.
        config.reranker.local.model = 'avsolatorio/GIST-small-Embedding-v0'
        config.reranker.local.encode_kwargs = None
        config.llm.generation_kwargs.max_tokens = None
        config.llm.generation_kwargs.temperature = 0
        patterns = [p.strip() for p in environ.get('ZOTERO_IGNORE', '').splitlines() if p.strip() and not p.lstrip().startswith('#')]
        if patterns:
            raise ValueError('ZOTERO_IGNORE uses gitignore rules; the new ignore_path uses glob rules. Convert the patterns in CUSTOM_CONFIG before upgrading to avoid changing collection selection.')
    if environ.get('DEBUG'):
        config.executor.debug = environ['DEBUG'].lower() in {'true', '1'}
    OmegaConf.to_container(config, resolve=True, throw_on_missing=True)
    OmegaConf.save(config, config_dir / 'custom.yaml', resolve=False)


if __name__ == '__main__':
    prepare_config(os.environ, Path(__file__).resolve().parents[1] / 'config')
