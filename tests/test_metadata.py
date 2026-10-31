import json,pathlib,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class PackageTests(unittest.TestCase):
 def test_required_files(self):
  for f in ['main.py','metadata.yaml','_conf_schema.json','requirements.txt','README.md','LICENSE']:
   self.assertTrue((ROOT/f).is_file(),f)
 def test_default_credentials_are_empty(self):
  schema=json.loads((ROOT/'_conf_schema.json').read_text(encoding='utf-8'))
  for k in ['ark_signing_token','service_token']:
   if k in schema:self.assertFalse(schema[k].get('default'))
if __name__=='__main__':unittest.main()
