"""Gallery lookup is independent of pricing/source classification and target EAN."""
import unittest
from types import SimpleNamespace
from runtime_mode import RuntimeConfig, RuntimeMode
from services.preparation_orchestrator import read_preparation_sources


class PreparationGalleryTests(unittest.TestCase):
    def test_both_source_types_lookup_original_ean_in_selected_account(self):
        old_ean = '4260606057143'
        for source_type in ('AFTERCOOL', 'OTTO_DIRECT'):
            with self.subTest(source_type=source_type):
                calls = []
                class Lookup:
                    def lookup_gallery_by_ean(self, account, ean, **kwargs):
                        calls.append(('gallery', account, ean, kwargs.get('factory_hint')))
                        return {'account':account,'ean':ean,'verified':True,'data_scope':'PRODUCTION','image_urls':['https://images.example/a.jpg']}
                    def lookup_product(self, account, ean, **kwargs):
                        calls.append(('product',account,ean))
                        return {'status':'FOUND'}
                otto = SimpleNamespace(
                    get_product=lambda sku: {'sku':sku,'ean':old_ean,'pricing':{'standardPrice':{'amount':100,'currency':'EUR'}}},
                    get_product_prices=lambda sku: {'sku':sku,'standardPrice':{'amount':100,'currency':'EUR'}},
                    get_product_delivery_information=lambda sku: {'sku':sku},
                )
                result=read_preparation_sources(
                    {'source_type':source_type,'observed_sku':'original-sku','confirmed_ean':old_ean,'factory_id':'factory','target_sku':'4072102511612'},
                    account='JV',config=RuntimeConfig(mode=RuntimeMode.PRODUCTION,otto_read_enabled=True,aftercool_read_enabled=True),
                    otto_factory=lambda:otto,aftercool_factory=Lookup,
                )
                expected_hint = None if source_type == 'OTTO_DIRECT' else 'factory'
                self.assertEqual(calls[0],('gallery','JV',old_ean,expected_hint))
                self.assertEqual(result['aftercool_gallery']['ean'],old_ean)
                if source_type=='OTTO_DIRECT':
                    self.assertIsNone(result['aftercool'])
                    self.assertEqual(len(calls),1)
                else:
                    self.assertEqual(calls[1],('product','JV',old_ean))
