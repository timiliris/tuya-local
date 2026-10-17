import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class TranslationTests(unittest.TestCase):
    def test_all_catalogs_cover_the_same_strings_and_parameters(self):
        catalogs=json.loads((ROOT/'web/locales.json').read_text())
        self.assertEqual(set(catalogs),{'fr','en','nl','de','es'})
        for lang,values in catalogs.items():
            self.assertEqual(set(values),set(catalogs['fr']),lang)
            for key,value in values.items():
                self.assertTrue(value.strip(),(lang,key))
                self.assertEqual(re.findall(r'\{\w+\}',key),re.findall(r'\{\w+\}',value),(lang,key))

    def test_all_static_and_dynamic_translation_keys_exist(self):
        catalogs=json.loads((ROOT/'web/locales.json').read_text())
        keys=set(catalogs['fr'])
        class Markup(HTMLParser):
            def handle_starttag(self,tag,attrs):
                for name,value in attrs:
                    if name.startswith('data-i18n'):self.keys.append(value)
        parser=Markup();parser.keys=[];parser.feed((ROOT/'web/index.html').read_text())
        for key in parser.keys:self.assertIn(key,keys)
        js='\n'.join((ROOT/'web'/name).read_text() for name in ['app.js','display.js','settings.js','ai.js'])
        for match in re.finditer(r"\bt\('([^']*)'\)",js):self.assertIn(match.group(1),keys)

    def test_standalone_compose_does_not_reference_old_bridge(self):
        for name in ['docker-compose.yml']:
            text=(ROOT/name).read_text()
            for removed in ['host.docker.internal','external:','18787','engine_data','tuya-lsc-test']:
                self.assertNotIn(removed,text)
        self.assertIn('/app/src/console.py',(ROOT/'Dockerfile').read_text())

if __name__=='__main__':unittest.main()
