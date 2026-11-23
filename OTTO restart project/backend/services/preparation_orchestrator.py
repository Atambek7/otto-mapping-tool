"""Reusable source reads for explicit preparation; no work at import time."""
from integrations.otto_client import OttoMarketClient
from services.aftercool_lookup_service import AftercoolLookupService, aftercool_factory_hint
from services.image_processor import normalize_candidate_images
from services.otto_price_evidence import resolve_otto_standard_price, is_missing_otto_price_error, normalize_otto_product_variation
from services.portal_media_service import portal_gallery_urls_from_attributes
from services.preparation_service import PreparationError, select_target_image_candidates
from integrations.otto_client import OttoApiError


def normalize_preparation_images(
    *,
    context,
    account,
    config,
    otto_product,
    aftercool,
    aftercool_gallery,
    fetcher=None,
):
    if not getattr(config, "image_normalization_enabled", False):
        return {}
    sku = str(context["observed_sku"])
    candidates = select_target_image_candidates(
        otto_product,
        source_type=str(context["source_type"]),
        aftercool=aftercool,
        otto_source_ref=f"OTTO:/v5/products/{sku}",
        aftercool_gallery=aftercool_gallery,
        catalog_image_url=context.get("catalog_image_url") or context.get("image_url"),
        portal_gallery=portal_gallery_urls_from_attributes(context.get("source_attributes_json")),
    )
    if not candidates:
        return {}
    return normalize_candidate_images(
        account=account,
        urls=[url for url, _ref in candidates],
        public_base_url=getattr(config, "public_base_url", ""),
        storage_dir=getattr(config, "media_storage_dir", "/app/storage/media"),
        fetcher=fetcher,
    )


def read_preparation_sources(context, *, account, config, otto_factory=None, aftercool_factory=None, image_fetcher=None):
    if not config.otto_read_enabled:
        raise PreparationError('OTTO_READ_DISABLED', 'OTTO source reads are disabled.')
    if not config.aftercool_read_enabled:
        raise PreparationError('AFTERCOOL_READ_DISABLED', 'Aftercool source reads are disabled.')
    client = (otto_factory or (lambda: OttoMarketClient.from_environment(account, runtime_config=config)))()
    sku = str(context['observed_sku'])
    product = normalize_otto_product_variation(client.get_product(sku), expected_sku=sku,
                                              expected_ean=str(context['confirmed_ean']))
    if product is None:
        raise PreparationError('OTTO_SKU_MISMATCH', 'Source SKU/EAN was not confirmed.')
    price = resolve_otto_standard_price(product, None, expected_sku=sku, expected_ean=str(context['confirmed_ean']))
    prices = price.get('response') if price['evidence'].get('status') in {'CONFIRMED', 'ZERO'} else None
    if prices is None:
        try:
            prices = client.get_product_prices(sku)
        except OttoApiError as error:
            if not is_missing_otto_price_error(error):
                raise
            prices = {'sku': sku}
    delivery = client.get_product_delivery_information(sku)
    aftercool = None
    lookup = (aftercool_factory or (lambda: AftercoolLookupService(runtime_config=config)))()
    gallery_factory_hint = aftercool_factory_hint(context)
    gallery = (
        lookup.lookup_gallery_by_ean(account, str(context['confirmed_ean']), factory_hint=gallery_factory_hint)
        if gallery_factory_hint is not None
        else lookup.lookup_gallery_by_ean(account, str(context['confirmed_ean']))
    )
    if context['source_type'] != 'OTTO_DIRECT':
        aftercool = lookup.lookup_product(account, str(context['confirmed_ean']), factory_hint=str(context['factory_id']))
        if aftercool.get('status') != 'FOUND':
            raise PreparationError('AFTERCOOL_SOURCE_UNCONFIRMED', 'Aftercool source identity was not confirmed.')
    normalized_images = normalize_preparation_images(
        context=context,
        account=account,
        config=config,
        otto_product=product,
        aftercool=aftercool,
        aftercool_gallery=gallery,
        fetcher=image_fetcher,
    )
    return dict(
        otto_product=product,
        otto_prices=prices,
        fulfillment=delivery,
        aftercool=aftercool,
        aftercool_gallery=gallery,
        normalized_images=normalized_images,
    )
