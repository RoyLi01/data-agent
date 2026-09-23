from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if os.getenv('AGENT_LOAD_DOTENV','1') != '0':
    load_dotenv(ROOT / '.env')

@dataclass
class Settings:
    data_dir: Path
    mode: str = 'offline'
    transport: str = 'stdio'
    max_calls: int = 12
    max_rows: int = 5000
    query_timeout: float = 5.0
    as_of: str = '2026-09-22'

    @classmethod
    def env(cls):
        data = Path(os.getenv('AGENT_DATA_DIR', str(ROOT / 'runtime')))
        if not data.is_absolute(): data = ROOT / data
        return cls(data, os.getenv('AGENT_MODE','offline'), os.getenv('AGENT_TRANSPORT','stdio'))
