import unittest
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from database import Base
import models
from routers import mapping
from services import okb_sync


class New3RegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    async def test_unknown_category_rejected_without_mapping_write(self):
        for account, factory in [('JV', '504487'), ('XL', '483563')]:
            with self.assertRaises(HTTPException) as caught:
                await mapping.assign_category(mapping.CategoryMappingBase(
                    product_ean='4000000000001', factory_id=factory,
                    account=account, category_id='N/A'), self.db)
            self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(self.db.query(models.ProductMapping).count(), 0)

    async def test_invalid_ai_category_rejected(self):
        request = mapping.AutoMapProductModel(ean='4000000000001', factory_id='504487',
            account='JV', title='Audit product', description='', specifics='', gallery_url='', picture_urls='')
        with patch.object(mapping, 'resolve_openai_key', return_value='fake'), patch.object(
                mapping, 'predict_category', new=AsyncMock(return_value='N/A')):
            with self.assertRaises(HTTPException) as caught:
                await mapping.api_predict_category(request, models.User(id=1), self.db)
            self.assertEqual(caught.exception.status_code, 400)

    async def test_source_attributes_without_category_are_partial(self):
        row={'EAN':'4000000000001','Artikelbeschreibung':'Unmatched Audit Thing',
             'Startpreis':'100.00','CustomItemSpecifics':'{"Farbe":"Blau"}'}
        with patch.object(okb_sync, 'fetch_product_from_okb', new=AsyncMock(return_value=None)), patch.object(
                okb_sync, 'fetch_product_from_aftercool', new=AsyncMock(return_value=row)):
            result=await okb_sync.sync_product_data('4000000000001','504487','JV')
        self.assertEqual(result['status'],'partial')
        self.assertIsNone(result['category_id'])
        self.assertEqual(result['attributes']['Farbe'],'Blau')
