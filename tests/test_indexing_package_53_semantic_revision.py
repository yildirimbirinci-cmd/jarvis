def test_revision(db,node):
    assert db.revision==0; assert db.replace_file('a.py',[node],[]) is True; assert db.revision==1
