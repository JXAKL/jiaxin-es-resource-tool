"""Read one existing share from the local index; never export a whole catalog."""
import json, os, sqlite3
from pathlib import Path
root=Path(__file__).resolve().parents[1]
paths=[Path(os.environ['LOCALAPPDATA'])/'EroScriptsOrganizer/.state/catalog.sqlite3',root/'.state/catalog.sqlite3',root/'build/performance-2.1.3/catalog.sqlite3']
for file in paths:
    if not file.is_file():continue
    with sqlite3.connect(file.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute("select data from topics where data like '%https://mega.nz/folder/%' order by id desc limit 30")
        for row in rows:
            links=json.loads(row[0]).get('links',[])
            selected=next((u for u in links if u.startswith('https://mega.nz/folder/') and '#' in u),None)
            if selected:
                target=root/'build/live-upgrade-input.json'
                target.write_text(json.dumps({'items':[{'url':selected}],'options':{}}),encoding='utf-8')
                print('Selected one existing public share; address withheld.')
                raise SystemExit(0)
raise SystemExit('No cached public share available.')
