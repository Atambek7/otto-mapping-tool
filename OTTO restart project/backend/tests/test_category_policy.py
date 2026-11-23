"""Shared category policy regressions; no DB or external API calls."""
import unittest
from unittest.mock import patch

from services import categories_repo as repo
from jobs.dispatcher import job_allowlist


class CategoryPolicyTests(unittest.TestCase):
    def test_required_only_for_marke_and_otto_legal_and_high_is_recommended(self):
        self.assertTrue(repo.is_required_attribute({'name': 'Marke'}))
        self.assertTrue(repo.is_required_attribute({'name': 'Geräteart laut ElektroG', 'featureRelevance': ['LEGAL']}))
        self.assertFalse(repo.is_required_attribute({'name': 'Farbe', 'relevance': 'HIGH'}))
        self.assertFalse(repo.is_required_attribute({'name': 'Art Leuchtmittel', 'relevance': 'HIGH'}))
        self.assertFalse(repo.is_required_attribute({'name': 'Ausführung', 'featureRelevance': ['LEGAL']}))
        self.assertFalse(repo.is_required_attribute({'name': 'Garantie', 'featureRelevance': ['LEGAL']}))
        self.assertFalse(repo.is_required_attribute({'name': 'Optional', 'mandatory': True}))

        self.assertTrue(repo.is_recommended_attribute({'name': 'Farbe', 'relevance': 'HIGH'}, category_name='Sofa', group_name='Sofas'))
        self.assertFalse(repo.is_recommended_attribute({'name': 'Art Leuchtmittel', 'attributeGroup': 'Beleuchtung', 'relevance': 'HIGH'}, category_name='Chesterfield-Sofa', group_name='Sofas'))

    def test_frontend_and_package_use_same_requirements_and_enums(self):
        group = {'group_name': 'TestGroup', 'attributes': {'attributes': [
            {'name': 'Farbe', 'relevance': 'HIGH', 'allowedValues': [{'value': 'Rot'}, 'Blau']},
            {'name': 'Relevanz Gefahrgut', 'relevance': 'LOW', 'featureRelevance': ['LEGAL']},
            {'name': 'Ausführung', 'relevance': 'HIGH'},
            {'name': 'Garantie', 'relevance': 'HIGH'},
        ]}}
        with patch.object(repo, 'get_category', return_value={'name': 'TestCat', 'categoryId': '1', 'category_group_id': '1', 'category_group': 'TestGroup'}), \
             patch.object(repo, 'get_category_group', return_value=group):
            package = repo.get_category_schema_attributes('TestCat')
            frontend = repo.get_frontend_category_schema('TestCat')
        self.assertEqual({a['name'] for a in package if a['required']},
                         {a['name'] for a in frontend if a['required']})
        self.assertEqual({a['name'] for a in package if a['recommended']},
                         {a['name'] for a in frontend if a['recommended']})
        self.assertEqual({a['name'] for a in package if a['required']}, {'Marke', 'Relevanz Gefahrgut'})
        self.assertEqual({a['name'] for a in package if a['recommended']}, {'Farbe'})
        self.assertNotIn('Garantie', {a['name'] for a in package})
        self.assertEqual(next(a for a in package if a['name'] == 'Farbe')['allowedValues'], ['Rot', 'Blau'])

    def test_chesterfield_sofa_excludes_lighting_and_marks_only_marke_required(self):
        repo._ALLOWED_ATTRS_CACHE.clear()
        package = repo.get_category_schema_attributes('Chesterfield-Sofa')
        frontend = repo.get_frontend_category_schema('Chesterfield-Sofa')
        allowed = repo.get_allowed_attribute_names('Chesterfield-Sofa')
        names = {a['name'] for a in package}
        self.assertNotIn('Art Leuchtmittel', names)
        self.assertNotIn('Art Leuchtmittel', allowed)
        self.assertNotIn('Art Leuchtmittel', {a['name'] for a in frontend})
        self.assertEqual([a['name'] for a in package if a['required']], ['Marke'])
        recommended = {a['name'] for a in package if a['recommended']}
        self.assertTrue({'Farbe', 'Grundfarbe', 'Bezug', 'Breite', 'Tiefe', 'Anzahl Sitzflächen'}.issubset(recommended))
        self.assertIn('Modellbezeichnung', names)

    def test_cross_category_leakage_across_other_groups(self):
        repo._ALLOWED_ATTRS_CACHE.clear()
        # Stuhl must not require or include Typ Netzstecker; Lederbett must not match "led"
        stuhl_names = {a['name'] for a in repo.get_category_schema_attributes('Stuhl')}
        self.assertNotIn('Typ Netzstecker', stuhl_names)
        lederbett_names = {a['name'] for a in repo.get_category_schema_attributes('Lederbett')}
        self.assertNotIn('Art Leuchtmittel', lederbett_names)
        # Lighting categories keep Beleuchtung attributes; Dekofiguren & Leuchten reflect live OTTO LEGAL attrs
        led_bild_names = {a['name'] for a in repo.get_category_schema_attributes('LED-Bild')}
        self.assertIn('Art Leuchtmittel', led_bild_names)
        skulptur = {a['name']: a for a in repo.get_category_schema_attributes('Skulptur')}
        self.assertNotIn('Belastbarkeit maximal', skulptur)
        self.assertNotIn('Material Korpus', skulptur)
        self.assertNotIn('Materialhinweis', skulptur)
        self.assertNotIn('BrandId', skulptur)
        self.assertTrue(skulptur['Relevanz Gefahrgut']['required'])
        self.assertTrue(skulptur['Relevanz Gefahrgut']['legal'])
        kronleuchter = {a['name']: a for a in repo.get_category_schema_attributes('Kronleuchter')}
        self.assertTrue(kronleuchter['Geräteart laut ElektroG']['required'])
        self.assertIn('Art Stromversorgung', kronleuchter)

    def test_unknown_category_has_no_illustrative_fallback(self):
        with patch.object(repo, 'get_category', return_value=None):
            self.assertEqual(repo.get_frontend_category_schema('unknown'), [])
            self.assertEqual(repo.get_category_schema_attributes('unknown'), [])

    def test_qc_rejects_previous_schema_instead_of_relabelling_it(self):
        from services.qc_service import QCError, require_current_category_schema
        definition = [{"name": "Marke", "required": True}]
        with patch.object(repo, 'get_category_schema_attributes', return_value=definition):
            require_current_category_schema({'categorySchema': {'category': 'Sofa', 'attributes': definition}})
            with self.assertRaises(QCError) as error:
                require_current_category_schema({'categorySchema': {'category': 'Sofa', 'attributes': []}})
            self.assertEqual(error.exception.code, 'CATEGORY_SCHEMA_REBUILD_REQUIRED')
            with self.assertRaises(QCError):
                require_current_category_schema({})

    def test_discovery_is_dispatchable_without_availability_writes(self):
        allowed = job_allowlist()
        self.assertTrue({'discovery.scan', 'discovery.reconcile', 'discovery.enrich'}.issubset(allowed))
        self.assertNotIn('otto.availability.quantity', allowed)


if __name__ == '__main__':
    unittest.main()
