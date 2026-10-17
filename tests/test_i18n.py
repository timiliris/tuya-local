"""The web catalog is shared by Docker and desktop playback."""
import json
from pathlib import Path

def test_catalogs():
    catalogs=json.loads((Path(__file__).resolve().parents[1]/'web/locales.json').read_text())
    assert set(catalogs)=={'fr','en','de','nl','es'}
    for language,strings in catalogs.items():
        assert set(strings)==set(catalogs['fr']),language
        assert all(isinstance(value,str) and value.strip() for value in strings.values())

if __name__=='__main__':
    test_catalogs()
    print('Shared interface translations passed')
