"""The three hardcoded home-directory paths must be configurable.

Loco used to pin /home/jahanzaib/models/presets.ini,
/home/jahanzaib/models/system-prompts and
/home/jahanzaib/Desktop/Loco/memory/loco.db, which made three endpoints fail
with 500 on any other machine. Each path now honours an environment variable
and keeps its original default.
"""
import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loco import presets, systemprompts  # noqa: E402
from loco.tools import search as search_tool  # noqa: E402


def _reload(module, env, name, value):
    """Reload *module* with the env var set to *value* (None = unset)."""
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        return importlib.reload(module)
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


def test_presets_path_is_configurable(tmp_path):
    target = tmp_path / "presets.ini"
    module = _reload(presets, {}, "LOCO_PRESETS_INI", str(target))
    assert module.PRESETS == target
    module.write_model("smollm2-135m-instruct", {"ctx-size": 4096})
    assert target.exists()
    assert "[smollm2-135m-instruct]" in target.read_text()


def test_presets_default_is_unchanged():
    module = _reload(presets, {}, "LOCO_PRESETS_INI", None)
    assert str(module.PRESETS) == "/home/jahanzaib/models/presets.ini"


def test_system_prompts_dir_is_configurable(tmp_path):
    module = _reload(systemprompts, {}, "LOCO_SYSTEM_PROMPTS_DIR", str(tmp_path))
    assert module.DIR == tmp_path
    module.put("lfm3-1.7b", "hello")
    assert (tmp_path / "lfm3-1.7b.md").read_text() == "hello"
    assert module.get("lfm3-1.7b") == "hello"


def test_search_db_is_configurable(tmp_path):
    db = tmp_path / "notes.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE notes(id TEXT PRIMARY KEY, content TEXT NOT NULL)")
    conn.execute("INSERT INTO notes VALUES ('a', 'alpha note')")
    conn.commit()
    conn.close()
    module = _reload(search_tool, {}, "LOCO_MEMORY_DB", str(db))
    assert module.DB_PATH == str(db)
    rows = module.search("alpha")
    assert [r["content"] for r in rows] == ["alpha note"]


def test_search_db_defaults_to_the_repo_memory_store():
    module = _reload(search_tool, {}, "LOCO_MEMORY_DB", None)
    assert module.DB_PATH == str(ROOT / "memory" / "loco.db")
    assert Path(module.DB_PATH).exists()


@pytest.mark.skipif(not (ROOT / "memory" / "loco.db").exists(),
                    reason="memory store not built in this checkout")
def test_search_tool_reads_the_loco_memory_store():
    module = _reload(search_tool, {}, "LOCO_MEMORY_DB", None)
    module.search("probe-that-matches-nothing")  # must not raise
