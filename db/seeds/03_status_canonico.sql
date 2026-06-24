-- Seeds: dim_status_pedido
-- marketplace_id NULL = status canônico (não vinculado a plataforma)
-- marketplace_id preenchido = raw status da plataforma

-- Status canônicos
INSERT INTO marts.dim_status_pedido (marketplace_id, raw_status, status_canonico, descricao) VALUES
  (NULL, 'pending',    'pending',    'Aguardando pagamento'),
  (NULL, 'paid',       'paid',       'Pago, em processamento'),
  (NULL, 'processing', 'processing', 'Pago, preparando envio'),
  (NULL, 'shipped',    'shipped',    'Em trânsito'),
  (NULL, 'delivered',  'delivered',  'Entregue'),
  (NULL, 'cancelled',  'cancelled',  'Cancelado'),
  (NULL, 'returned',   'returned',   'Devolvido ou reembolsado'),
  (NULL, 'on_hold',    'on_hold',    'Retido para revisão'),
  (NULL, 'unknown',    'unknown',    'Status não mapeado')
ON CONFLICT DO NOTHING;

-- TikTok Shop → canônico (marketplace_id = 1)
INSERT INTO marts.dim_status_pedido (marketplace_id, raw_status, status_canonico, descricao) VALUES
  (1, 'COMPLETED',           'delivered',  'Pedido entregue e finalizado'),
  (1, 'CANCELLED',           'cancelled',  'Pedido cancelado'),
  (1, 'DELIVERED',           'delivered',  'Entregue (pedido ainda aberto)'),
  (1, 'UNPAID',              'pending',    'Aguardando pagamento'),
  (1, 'IN_TRANSIT',          'shipped',    'Em trânsito para entrega'),
  (1, 'AWAITING_COLLECTION', 'shipped',    'Aguardando coleta pela transportadora'),
  (1, 'AWAITING_SHIPMENT',   'processing', 'Pago, aguardando envio pelo seller'),
  (1, 'ON_HOLD',             'on_hold',    'Pedido retido (fraude/revisão)')
ON CONFLICT DO NOTHING;

-- Mercado Livre → canônico (marketplace_id = 2)
INSERT INTO marts.dim_status_pedido (marketplace_id, raw_status, status_canonico, descricao) VALUES
  (2, 'paid',               'paid',       'Pago e ativo'),
  (2, 'cancelled',          'cancelled',  'Cancelado'),
  (2, 'partially_refunded', 'returned',   'Reembolsado parcialmente'),
  (2, 'pending_cancel',     'cancelled',  'Cancelamento pendente de confirmação')
ON CONFLICT DO NOTHING;
