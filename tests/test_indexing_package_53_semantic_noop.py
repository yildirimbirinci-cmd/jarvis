def test_noop(db,node):
    db.replace_file('a.py',[node],[]); assert db.replace_file('a.py',[node],[]) is False; assert db.revision==1
