import pytest
from marktradar import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.bootstrap(c)
    yield c
    c.close()
