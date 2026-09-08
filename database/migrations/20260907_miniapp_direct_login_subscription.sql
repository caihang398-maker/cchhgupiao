USE `stock_quant_saas`;

INSERT INTO `subscription_plans`
  (`plan_code`, `plan_name`, `duration_months`, `price`, `currency`, `daily_query_limit`, `features_json`, `status`)
VALUES
  ('half_year', '半年卡', 6, 52.00, 'CNY', NULL,
   JSON_ARRAY('全部核心功能', '策略复盘', '市场地图', '数据体检'), 'active')
ON DUPLICATE KEY UPDATE
  `plan_name` = VALUES(`plan_name`),
  `duration_months` = VALUES(`duration_months`),
  `price` = VALUES(`price`),
  `daily_query_limit` = VALUES(`daily_query_limit`),
  `features_json` = VALUES(`features_json`),
  `status` = VALUES(`status`);
