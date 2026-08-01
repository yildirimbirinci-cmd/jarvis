def test_clear(db,node):
    assert db.clear() is False; db.replace_file('a.py',[node],[]); assert db.clear() is True; assert db.clear() is False
