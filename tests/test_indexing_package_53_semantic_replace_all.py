def test_replace_all(db,node):
    assert db.replace_all([('a.py',[node],[])]) is True; assert db.replace_all([('a.py',[node],[])]) is False
