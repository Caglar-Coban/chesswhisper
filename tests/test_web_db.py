from chesswhisper.web.db import init_db, get_db
from chesswhisper.web.models import User


def test_init_db_creates_tables_and_supports_crud(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    db_gen = get_db()
    db = next(db_gen)
    try:
        db.add(User(username="alice", password_hash="hash", is_admin=False))
        db.commit()

        found = db.query(User).filter_by(username="alice").one()
        assert found.username == "alice"
        assert found.is_admin is False
        assert found.created_at is not None
    finally:
        db_gen.close()

    assert db_path.exists()
