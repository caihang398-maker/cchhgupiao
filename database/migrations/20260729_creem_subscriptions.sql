-- Creem subscription payment tables for MySQL 5.7.
-- Safe to execute repeatedly because all tables use IF NOT EXISTS.
USE `stock_quant_saas`;

CREATE TABLE IF NOT EXISTS `payment_provider_products` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `plan_id` BIGINT UNSIGNED NOT NULL,
  `provider` VARCHAR(30) NOT NULL DEFAULT 'creem',
  `environment` VARCHAR(20) NOT NULL COMMENT 'test/live',
  `external_product_id` VARCHAR(100) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `provider_price_minor` BIGINT UNSIGNED NOT NULL COMMENT '支付渠道最小货币单位金额',
  `provider_currency` CHAR(3) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `status` VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT 'active/disabled',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_provider_product_plan` (`provider`, `environment`, `plan_id`),
  UNIQUE KEY `uk_provider_product_external` (`provider`, `environment`, `external_product_id`),
  KEY `idx_provider_product_status` (`provider`, `environment`, `status`),
  CONSTRAINT `fk_provider_product_plan`
    FOREIGN KEY (`plan_id`) REFERENCES `subscription_plans` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `payment_checkout_sessions` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_id` BIGINT UNSIGNED NOT NULL,
  `provider` VARCHAR(30) NOT NULL DEFAULT 'creem',
  `environment` VARCHAR(20) NOT NULL COMMENT 'test/live',
  `request_id` VARCHAR(100) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `checkout_id` VARCHAR(100) CHARACTER SET ascii COLLATE ascii_general_ci NULL,
  `checkout_url` VARCHAR(1000) NULL,
  `provider_customer_id` VARCHAR(100) CHARACTER SET ascii COLLATE ascii_general_ci NULL,
  `provider_subscription_id` VARCHAR(100) CHARACTER SET ascii COLLATE ascii_general_ci NULL,
  `status` VARCHAR(20) NOT NULL DEFAULT 'creating',
  `metadata_json` JSON NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_checkout_order` (`order_id`),
  UNIQUE KEY `uk_checkout_request` (`provider`, `environment`, `request_id`),
  UNIQUE KEY `uk_checkout_external` (`provider`, `environment`, `checkout_id`),
  KEY `idx_checkout_subscription` (`provider_subscription_id`),
  KEY `idx_checkout_status` (`status`, `created_at`),
  CONSTRAINT `fk_checkout_order`
    FOREIGN KEY (`order_id`) REFERENCES `subscription_orders` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `payment_subscriptions` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `provider` VARCHAR(30) NOT NULL DEFAULT 'creem',
  `environment` VARCHAR(20) NOT NULL COMMENT 'test/live',
  `provider_subscription_id` VARCHAR(100) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `provider_customer_id` VARCHAR(100) CHARACTER SET ascii COLLATE ascii_general_ci NULL,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `plan_id` BIGINT UNSIGNED NOT NULL,
  `initial_order_id` BIGINT UNSIGNED NULL,
  `status` VARCHAR(30) NOT NULL DEFAULT 'pending',
  `current_period_start_at` DATETIME NULL,
  `current_period_end_at` DATETIME NULL,
  `next_transaction_at` DATETIME NULL,
  `canceled_at` DATETIME NULL,
  `metadata_json` JSON NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_payment_subscription_external`
    (`provider`, `environment`, `provider_subscription_id`),
  KEY `idx_payment_subscription_user` (`user_id`, `status`, `updated_at`),
  KEY `idx_payment_subscription_plan` (`plan_id`),
  CONSTRAINT `fk_payment_subscription_user`
    FOREIGN KEY (`user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_payment_subscription_plan`
    FOREIGN KEY (`plan_id`) REFERENCES `subscription_plans` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_payment_subscription_order`
    FOREIGN KEY (`initial_order_id`) REFERENCES `subscription_orders` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `payment_webhook_events` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `provider` VARCHAR(30) NOT NULL DEFAULT 'creem',
  `environment` VARCHAR(20) NOT NULL COMMENT 'test/live',
  `event_id` VARCHAR(120) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `event_type` VARCHAR(80) NOT NULL,
  `payload_sha256` CHAR(64) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
  `payload_json` JSON NOT NULL,
  `signature` VARCHAR(255) CHARACTER SET ascii COLLATE ascii_general_ci NULL,
  `process_status` VARCHAR(20) NOT NULL DEFAULT 'received',
  `attempts` SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  `error_message` VARCHAR(1000) NULL,
  `received_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_attempt_at` DATETIME NULL,
  `processed_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_payment_webhook_event` (`provider`, `environment`, `event_id`),
  KEY `idx_payment_webhook_status` (`process_status`, `received_at`),
  KEY `idx_payment_webhook_type` (`event_type`, `received_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
