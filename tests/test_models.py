"""Discovery keeps display grouping separate from exact execution configuration."""
from copy import deepcopy
import unittest
from openmodels import Catalog, ContractError, Manifest
from openmodels.contracts import dumps
from test_universal import fixture


class ModelTests(unittest.TestCase):
  def test_grouping_preserves_configurations_and_archive_filter(self):
    data, recipe, _ = fixture()
    entry = data['entries'][0]
    entry['model'] = {'id': 'example/model', 'name': 'Named model', 'family': 'segmentation',
                      'description': 'Example.', 'links': [], 'archived': False}
    altered = recipe.data
    altered['configuration']['scale'] = 2
    sibling = Manifest.create(altered)
    data['documents'][sibling.id] = sibling.raw
    data['entries'].append({**deepcopy(entry), 'recipe': sibling.id})
    cat = Catalog(dumps(data))
    model = cat.model('example/model')
    self.assertEqual({v['recipe'] for v in model['variants']}, {recipe.id, sibling.id})
    self.assertEqual(len(cat.models(query='named')), 1)
    model['links'].append('https://example.com/changed')
    self.assertEqual(cat.model('example/model')['links'], [])
    from web.models import detail
    page = detail(cat, cat.model('example/model'), lambda title, body: body, base_url='https://example.com/models/')
    self.assertIn('https://example.com/' + next(iter(data['locations'])), page)
    page = detail(cat, cat.model('example/model'), lambda title, body: body)
    self.assertIn('href="/' + next(iter(data['locations'])), page)
    for entry in data['entries']:
      entry['model']['archived'] = True
    cat = Catalog(dumps(data))
    self.assertEqual(cat.models(include_archive=False), [])
    self.assertEqual(len(cat.models()), 1)
    self.assertEqual(len(cat.models(include_archive=True)), 1)
    data['entries'][1]['model']['name'] = 'Conflicting name'
    with self.assertRaises(ContractError):
      Catalog(dumps(data)).models(include_archive=True)

  def test_naming_evidence_and_fallbacks_do_not_change_recipes(self):
    from index.names import model_metadata, source_label
    from index.registry import convert
    from web.models import listing, detail
    bundle = {'bundle_id': 'abcdefgh12345678', 'kind': 'driving', 'variant': 'standard', 'family': 'supercombo',
              'name': 'New model: Useful Name', 'introduced_by': {'commit': 'one', 'date': '2024-01-01'},
              'occurrences': [{'commit': 'one', 'date': '2024-01-01', 'status': 'merged'}],
              'files': [{'role': 'supercombo', 'oid': 'a' * 64, 'filename': 'supercombo.onnx', 'size': 1}]}
    base = {'bundle_id': bundle['bundle_id'], 'ref': 'one', 'artifacts': [{'role': 'supercombo', 'sha256': 'a' * 64}],
            'method': 'published'}
    records = [{**base, 'name': 'Fork alias', 'source': 'sunnypilot', 'url': 'https://example.com/fork'},
               {**base, 'name': 'Official Name', 'source': 'comma', 'url': 'https://example.com/upstream'}]
    model = model_metadata(bundle, records)
    self.assertEqual(model['name'], 'Official Name')
    self.assertEqual([n['name'] for n in model['names']], ['Official Name', 'Fork alias', 'Useful Name'])
    self.assertTrue(model['archived'])
    self.assertEqual(model_metadata(bundle, [])['name_kind'], 'source')
    for title in ('new model files', 'New model: deadbeef', 'New model: 12345/400', 'Revert Useful Model', 'wrong model', 'best model'):
      self.assertIsNone(source_label(title))
    bundle['occurrences'][0]['is_revert'] = True
    self.assertEqual(model_metadata(bundle, [])['name_kind'], 'generated')
    bundle['occurrences'][0]['is_revert'] = False
    bundle['name'] = 'rebase'
    generated = model_metadata(bundle, [])
    self.assertEqual(generated['name_kind'], 'generated')
    self.assertIn('abcdefgh', generated['name'])
    self.assertEqual(generated['id'], model['id'])
    with self.assertRaises(ContractError):
      model_metadata(bundle, [{**records[0], 'ref': 'unrelated'}])
    with self.assertRaises(ContractError):
      model_metadata(bundle, [{**records[0], 'artifacts': []}])
    archive = {'generated_at': '2024-01-01', 'upstream_head': 'one', 'bundles': [bundle],
               'files': [{'oid': 'a' * 64, 'size': 1}]}
    snapshot = convert(archive)
    original_documents = deepcopy(snapshot['documents'])
    cat = Catalog(dumps(snapshot))
    self.assertEqual(len(cat.models()), 1)
    self.assertIn('Generated label', listing(cat, lambda title, body: body))
    snapshot['entries'][0]['model'] = model
    renamed = Catalog(dumps(snapshot))
    self.assertEqual(renamed.data['documents'], original_documents)
    self.assertEqual(len(renamed.models(query='Fork alias')), 1)
    page = detail(renamed, renamed.models()[0], lambda title, body: body)
    self.assertIn('Fork alias', page)
    self.assertIn('https://example.com/upstream', page)
