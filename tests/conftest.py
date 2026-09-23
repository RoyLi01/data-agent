import pytest
from data_agent.config import Settings
from data_agent.seed import seed
from data_agent.engine import Engine

@pytest.fixture
def settings(tmp_path):
    s=Settings(tmp_path,transport='local');seed(tmp_path);return s

@pytest.fixture
def engine(settings):return Engine(settings)
