import pytest
from data_agent.config import Settings
from data_agent.seed import seed
from data_agent.engine import Engine

@pytest.fixture(autouse=True)
def fixture_retrieval(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_BACKEND","fixture")
    monkeypatch.setenv("RERANK_BACKEND","fixture")

@pytest.fixture
def settings(tmp_path):
    s=Settings(tmp_path,transport='local');seed(tmp_path);return s

@pytest.fixture
def engine(settings):
    instance=Engine(settings)
    yield instance
    instance.memory.close()
