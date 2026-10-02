from pathlib import Path

import pytest
from omegaconf import OmegaConf

from scripts.prepare_config import prepare_config


@pytest.fixture
def legacy_env(monkeypatch):
    values = {'ZOTERO_ID': '123', 'ZOTERO_KEY': 'fake-key', 'SENDER': 'sender@example.com',
              'RECEIVER': 'receiver@example.com', 'SENDER_PASSWORD': 'fake-password',
              'SMTP_SERVER': 'smtp.example.com', 'SMTP_PORT': '465', 'OPENAI_API_KEY': 'fake-api-key',
              'USE_LLM_API': 'true', 'ARXIV_QUERY': 'quant-ph+cond-mat.mes-hall',
              'MAX_PAPER_NUM': '12', 'LANGUAGE': 'Chinese', 'SEND_EMPTY': 'true'}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def setup_config(tmp_path):
    base = Path(__file__).resolve().parents[1] / 'config/base.yaml'
    OmegaConf.save(OmegaConf.load(base), tmp_path / 'base.yaml')
    return tmp_path


def test_legacy_migration(tmp_path, legacy_env):
    prepare_config(legacy_env, setup_config(tmp_path))
    config = OmegaConf.merge(OmegaConf.load(tmp_path / 'base.yaml'), OmegaConf.load(tmp_path / 'custom.yaml'))
    assert config.source.arxiv.category == ['quant-ph', 'cond-mat.mes-hall']
    assert config.source.arxiv.include_all_announce_types is True
    assert config.executor.max_paper_num == 12
    assert config.executor.send_empty is True
    assert config.email.smtp_server == 'smtp.example.com'
    assert config.llm.language == 'Chinese'
    assert config.reranker.local.model == 'avsolatorio/GIST-small-Embedding-v0'
    assert config.reranker.local.encode_kwargs is None
    assert config.llm.generation_kwargs.temperature == 0
    assert config.llm.generation_kwargs.max_tokens is None
    saved = (tmp_path / 'custom.yaml').read_text()
    assert 'fake-password' not in saved
    assert 'fake-api-key' not in saved


@pytest.mark.parametrize('changes', [{'USE_LLM_API': 'false'}, {'ZOTERO_IGNORE': 'archive/**'}])
def test_incompatible_legacy_settings_fail_before_write(tmp_path, legacy_env, changes):
    legacy_env.update(changes)
    with pytest.raises(ValueError):
        prepare_config(legacy_env, setup_config(tmp_path))
    assert not (tmp_path / 'custom.yaml').exists()


def test_custom_config_overrides_legacy(tmp_path, legacy_env):
    legacy_env['CUSTOM_CONFIG'] = OmegaConf.to_yaml(OmegaConf.create({
        'zotero': {'user_id': '123', 'api_key': '${oc.env:ZOTERO_KEY}'},
        'email': {'sender': 'a@example.com', 'receiver': 'b@example.com', 'smtp_server': 'smtp.test', 'smtp_port': 465, 'sender_password': '${oc.env:SENDER_PASSWORD}'},
        'llm': {'api': {'key': '${oc.env:OPENAI_API_KEY}', 'base_url': 'https://api.example.com/v1'}, 'generation_kwargs': {'model': 'custom-model'}},
        'executor': {'source': ['arxiv']}, 'source': {'arxiv': {'category': ['quant-ph']}},
    }))
    legacy_env['DEBUG'] = 'true'
    prepare_config(legacy_env, setup_config(tmp_path))
    config = OmegaConf.load(tmp_path / 'custom.yaml')
    assert config.executor.debug is True
    assert config.source.arxiv.include_all_announce_types is False
    assert config.llm.generation_kwargs.model == 'custom-model'
