-- Seeds: dim_marketplace
INSERT INTO marts.dim_marketplace (marketplace_id, nome_marketplace, slug, ativo) VALUES
  (1, 'TikTok Shop',    'tiktok',        true),
  (2, 'Mercado Livre',  'mercadolivre',  true),
  (3, 'Shopee',         'shopee',        true),
  (4, 'Magalu',         'magalu',        false),
  (5, 'Amazon',         'amazon',        false)
ON CONFLICT (marketplace_id) DO UPDATE
  SET nome_marketplace = EXCLUDED.nome_marketplace,
      slug = EXCLUDED.slug,
      ativo = EXCLUDED.ativo;

