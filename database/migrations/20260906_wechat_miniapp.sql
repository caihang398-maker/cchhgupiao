USE `stock_quant_saas`;

CREATE TABLE IF NOT EXISTS `miniapp_user_bindings` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `provider` VARCHAR(20) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL DEFAULT 'wechat',
  `app_id` VARCHAR(64) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `open_id` VARCHAR(128) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `union_id` VARCHAR(128) CHARACTER SET ascii COLLATE ascii_general_ci NULL,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `last_login_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_miniapp_binding_openid` (`provider`, `app_id`, `open_id`),
  UNIQUE KEY `uk_miniapp_binding_user` (`provider`, `app_id`, `user_id`),
  KEY `idx_miniapp_binding_unionid` (`union_id`),
  CONSTRAINT `fk_miniapp_binding_user`
    FOREIGN KEY (`user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
