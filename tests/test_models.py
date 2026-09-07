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
    for entry in data['entries']:
      entry['model']['archived'] = True
    cat = Catalog(dumps(data))
    self.assertEqual(cat.models(), [])
    self.assertEqual(len(cat.models(include_archive=True)), 1)
    data['entries'][1]['model']['name'] = 'Conflicting name'
    with self.assertRaises(ContractError):
      Catalog(dumps(data)).models(include_archive=True)
