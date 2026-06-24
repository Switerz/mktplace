-- Seeds: dim_empresa
INSERT INTO marts.dim_empresa (empresa_id, nome_empresa, nome_normalizado, ativo, created_at, updated_at) VALUES
  (1, 'GoBeauté', 'gobeaute', true, NOW(), NOW())
ON CONFLICT (empresa_id) DO UPDATE
  SET nome_empresa = EXCLUDED.nome_empresa,
      updated_at = NOW();

-- Seeds: dim_loja
-- brand_key deve coincidir exatamente com o campo `brand` do Data Mart
INSERT INTO marts.dim_loja (loja_id, empresa_id, brand_key, nome_loja, nome_normalizado, ativo, created_at, updated_at) VALUES
  (1, 1, 'apice',    'ÁPICE',    'apice',    true,  NOW(), NOW()),
  (2, 1, 'barbours', 'BARBOURS', 'barbours', true,  NOW(), NOW()),
  (3, 1, 'kokeshi',  'KOKESHI',  'kokeshi',  true,  NOW(), NOW()),
  (4, 1, 'lescent',  'LESCENT',  'lescent',  true,  NOW(), NOW()),
  (5, 1, 'rituaria', 'RITUÁRIA', 'rituaria', true,  NOW(), NOW())
ON CONFLICT (loja_id) DO UPDATE
  SET nome_loja        = EXCLUDED.nome_loja,
      brand_key        = EXCLUDED.brand_key,
      ativo            = EXCLUDED.ativo,
      updated_at       = NOW();

-- Nota: azbuy e gocase existem no Data Mart mas estão FORA do escopo GoBeauté.
-- Não incluir aqui. O pipeline deve filtrar: WHERE brand IN (SELECT brand_key FROM marts.dim_loja).
