import json
def test_snapshot(db,node):
    db.replace_file('a.py',[node],[]); payload=db.snapshot(); json.dumps(payload); assert payload['revision']==1
