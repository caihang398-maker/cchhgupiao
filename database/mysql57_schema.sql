-- A股量化软件服务数据库结构
-- 兼容 MySQL 5.7.9 及以上版本。
-- 所有应用 DATETIME 时间统一按 UTC 保存。

CREATE DATABASE IF NOT EXISTS `stock_quant_saas`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE `stock_quant_saas`;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE IF NOT EXISTS `sys_users` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `login_name` VARCHAR(64) NULL COMMENT '管理员可使用admin，普通用户通常为空',
  `mobile` VARCHAR(20) NOT NULL,
  `real_name` VARCHAR(64) NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL COMMENT '应用层bcrypt/Argon2哈希，禁止明文',
  `status` VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT 'active/disabled/locked',
  `service_started_at` DATETIME NULL COMMENT '当前服务开始时间缓存',
  `service_expires_at` DATETIME NULL COMMENT '当前服务到期时间，按左闭右开判断',
  `last_login_at` DATETIME NULL,
  `last_login_ip` VARCHAR(45) NULL,
  `password_changed_at` DATETIME NULL,
  `failed_login_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `locked_until` DATETIME NULL,
  `remark` VARCHAR(500) NULL,
  `created_by` BIGINT UNSIGNED NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sys_users_mobile` (`mobile`),
  UNIQUE KEY `uk_sys_users_login_name` (`login_name`),
  KEY `idx_sys_users_expiry` (`status`, `service_expires_at`),
  KEY `idx_sys_users_name` (`real_name`),
  CONSTRAINT `fk_sys_users_created_by`
    FOREIGN KEY (`created_by`) REFERENCES `sys_users` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `sys_roles` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `role_code` VARCHAR(64) NOT NULL,
  `role_name` VARCHAR(100) NOT NULL,
  `description` VARCHAR(500) NULL,
  `is_system` TINYINT(1) NOT NULL DEFAULT 0,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sys_roles_code` (`role_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `sys_permissions` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `permission_code` VARCHAR(100) NOT NULL,
  `permission_name` VARCHAR(100) NOT NULL,
  `module_code` VARCHAR(64) NOT NULL,
  `description` VARCHAR(500) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sys_permissions_code` (`permission_code`),
  KEY `idx_sys_permissions_module` (`module_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `sys_user_roles` (
  `user_id` BIGINT UNSIGNED NOT NULL,
  `role_id` BIGINT UNSIGNED NOT NULL,
  `granted_by` BIGINT UNSIGNED NULL,
  `granted_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`, `role_id`),
  KEY `idx_sys_user_roles_role` (`role_id`),
  KEY `idx_sys_user_roles_granted_by` (`granted_by`),
  CONSTRAINT `fk_sys_user_roles_user`
    FOREIGN KEY (`user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_sys_user_roles_role`
    FOREIGN KEY (`role_id`) REFERENCES `sys_roles` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_sys_user_roles_granted_by`
    FOREIGN KEY (`granted_by`) REFERENCES `sys_users` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `sys_role_permissions` (
  `role_id` BIGINT UNSIGNED NOT NULL,
  `permission_id` BIGINT UNSIGNED NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`role_id`, `permission_id`),
  KEY `idx_sys_role_permissions_permission` (`permission_id`),
  CONSTRAINT `fk_sys_role_permissions_role`
    FOREIGN KEY (`role_id`) REFERENCES `sys_roles` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_sys_role_permissions_permission`
    FOREIGN KEY (`permission_id`) REFERENCES `sys_permissions` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `subscription_plans` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `plan_code` VARCHAR(64) NOT NULL,
  `plan_name` VARCHAR(100) NOT NULL,
  `duration_months` SMALLINT UNSIGNED NOT NULL DEFAULT 1,
  `price` DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  `currency` CHAR(3) NOT NULL DEFAULT 'CNY',
  `daily_query_limit` INT UNSIGNED NULL COMMENT 'NULL表示不限制',
  `features_json` JSON NULL,
  `status` VARCHAR(20) NOT NULL DEFAULT 'active',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_subscription_plans_code` (`plan_code`),
  KEY `idx_subscription_plans_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `subscription_orders` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_no` VARCHAR(40) NOT NULL,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `plan_id` BIGINT UNSIGNED NOT NULL,
  `plan_name_snapshot` VARCHAR(100) NOT NULL,
  `duration_months_snapshot` SMALLINT UNSIGNED NOT NULL,
  `order_amount` DECIMAL(12,2) NOT NULL,
  `paid_amount` DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  `currency` CHAR(3) NOT NULL DEFAULT 'CNY',
  `order_status` VARCHAR(20) NOT NULL DEFAULT 'pending'
    COMMENT 'pending/paid/cancelled/refunded/closed',
  `pay_channel` VARCHAR(30) NULL,
  `external_trade_no` VARCHAR(100) NULL,
  `paid_at` DATETIME NULL,
  `service_start_at` DATETIME NULL,
  `service_end_at` DATETIME NULL,
  `created_by` BIGINT UNSIGNED NULL COMMENT '后台手工开通人',
  `remark` VARCHAR(500) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_subscription_orders_no` (`order_no`),
  UNIQUE KEY `uk_subscription_orders_external_no` (`external_trade_no`),
  KEY `idx_subscription_orders_user_time` (`user_id`, `created_at`),
  KEY `idx_subscription_orders_status` (`order_status`, `created_at`),
  CONSTRAINT `fk_subscription_orders_user`
    FOREIGN KEY (`user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_subscription_orders_plan`
    FOREIGN KEY (`plan_id`) REFERENCES `subscription_plans` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_subscription_orders_created_by`
    FOREIGN KEY (`created_by`) REFERENCES `sys_users` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `payment_transactions` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_id` BIGINT UNSIGNED NOT NULL,
  `transaction_no` VARCHAR(100) NOT NULL,
  `transaction_type` VARCHAR(20) NOT NULL DEFAULT 'payment'
    COMMENT 'payment/refund',
  `channel` VARCHAR(30) NOT NULL,
  `amount` DECIMAL(12,2) NOT NULL,
  `status` VARCHAR(20) NOT NULL COMMENT 'pending/success/failed/closed',
  `channel_payload` JSON NULL,
  `occurred_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_payment_transactions_no` (`transaction_no`),
  KEY `idx_payment_transactions_order` (`order_id`, `created_at`),
  CONSTRAINT `fk_payment_transactions_order`
    FOREIGN KEY (`order_id`) REFERENCES `subscription_orders` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `subscription_periods` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `order_id` BIGINT UNSIGNED NULL,
  `plan_id` BIGINT UNSIGNED NULL,
  `start_at` DATETIME NOT NULL,
  `end_at` DATETIME NOT NULL,
  `source_type` VARCHAR(20) NOT NULL DEFAULT 'order'
    COMMENT 'order/manual/gift/adjustment',
  `status` VARCHAR(20) NOT NULL DEFAULT 'active'
    COMMENT 'active/cancelled/refunded',
  `granted_by` BIGINT UNSIGNED NULL,
  `remark` VARCHAR(500) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_subscription_periods_user` (`user_id`, `start_at`, `end_at`),
  KEY `idx_subscription_periods_end` (`status`, `end_at`),
  KEY `idx_subscription_periods_order` (`order_id`),
  CONSTRAINT `fk_subscription_periods_user`
    FOREIGN KEY (`user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_subscription_periods_order`
    FOREIGN KEY (`order_id`) REFERENCES `subscription_orders` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT `fk_subscription_periods_plan`
    FOREIGN KEY (`plan_id`) REFERENCES `subscription_plans` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT `fk_subscription_periods_granted_by`
    FOREIGN KEY (`granted_by`) REFERENCES `sys_users` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
  `status` VARCHAR(20) NOT NULL DEFAULT 'creating'
    COMMENT 'creating/ready/completed/failed/expired',
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
  `process_status` VARCHAR(20) NOT NULL DEFAULT 'received'
    COMMENT 'received/processing/processed/ignored/failed',
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

CREATE TABLE IF NOT EXISTS `auth_sessions` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `session_token_hash` CHAR(64) NOT NULL,
  `refresh_token_hash` CHAR(64) NULL,
  `ip_address` VARCHAR(45) NULL,
  `user_agent` VARCHAR(500) NULL,
  `expires_at` DATETIME NOT NULL,
  `last_seen_at` DATETIME NULL,
  `revoked_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_auth_sessions_token` (`session_token_hash`),
  KEY `idx_auth_sessions_user` (`user_id`, `expires_at`),
  KEY `idx_auth_sessions_expiry` (`expires_at`, `revoked_at`),
  CONSTRAINT `fk_auth_sessions_user`
    FOREIGN KEY (`user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `login_logs` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT UNSIGNED NULL,
  `login_identifier` VARCHAR(100) NOT NULL,
  `success` TINYINT(1) NOT NULL,
  `failure_reason` VARCHAR(200) NULL,
  `ip_address` VARCHAR(45) NULL,
  `user_agent` VARCHAR(500) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_login_logs_user_time` (`user_id`, `created_at`),
  KEY `idx_login_logs_identifier_time` (`login_identifier`, `created_at`),
  KEY `idx_login_logs_ip_time` (`ip_address`, `created_at`),
  CONSTRAINT `fk_login_logs_user`
    FOREIGN KEY (`user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `audit_logs` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `operator_user_id` BIGINT UNSIGNED NULL,
  `action_code` VARCHAR(100) NOT NULL,
  `target_type` VARCHAR(64) NOT NULL,
  `target_id` VARCHAR(64) NULL,
  `request_id` CHAR(36) NULL,
  `before_json` JSON NULL,
  `after_json` JSON NULL,
  `ip_address` VARCHAR(45) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_audit_logs_operator_time` (`operator_user_id`, `created_at`),
  KEY `idx_audit_logs_target` (`target_type`, `target_id`, `created_at`),
  KEY `idx_audit_logs_action_time` (`action_code`, `created_at`),
  CONSTRAINT `fk_audit_logs_operator`
    FOREIGN KEY (`operator_user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `scan_runs` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `trade_date` DATE NOT NULL,
  `run_time` DATETIME NOT NULL,
  `scan_size` INT UNSIGNED NOT NULL,
  `top_n` INT UNSIGNED NOT NULL,
  `min_turnover` DECIMAL(20,2) NOT NULL,
  `strategy_version` VARCHAR(40) NULL COMMENT '策略与评分规则版本',
  `parameters_json` JSON NULL,
  `status` VARCHAR(20) NOT NULL,
  `note` VARCHAR(1000) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_scan_runs_date_status` (`trade_date`, `status`, `run_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `recommendations` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `run_id` BIGINT UNSIGNED NOT NULL,
  `trade_date` DATE NOT NULL,
  `horizon` VARCHAR(20) NOT NULL,
  `symbol` VARCHAR(16) NOT NULL,
  `stock_name` VARCHAR(64) NOT NULL,
  `score` DECIMAL(7,2) NOT NULL,
  `base_score` DECIMAL(7,2) NOT NULL,
  `fundamental_score` DECIMAL(7,2) NULL,
  `rating` VARCHAR(20) NOT NULL,
  `priority` VARCHAR(20) NOT NULL,
  `action` VARCHAR(30) NOT NULL,
  `close_price` DECIMAL(18,4) NOT NULL,
  `buy_zone_low` DECIMAL(18,4) NOT NULL,
  `buy_zone_high` DECIMAL(18,4) NOT NULL,
  `stop_loss` DECIMAL(18,4) NOT NULL,
  `take_profit_1` DECIMAL(18,4) NOT NULL,
  `take_profit_2` DECIMAL(18,4) NOT NULL,
  `trailing_stop` DECIMAL(18,4) NOT NULL,
  `position_pct` DECIMAL(8,6) NOT NULL,
  `industry` VARCHAR(100) NULL,
  `topics_json` JSON NULL,
  `theme_strength` DECIMAL(7,2) NULL,
  `net_inflow_3d` DECIMAL(20,2) NULL,
  `net_inflow_5d` DECIMAL(20,2) NULL,
  `net_inflow_10d` DECIMAL(20,2) NULL,
  `pe_est` DECIMAL(18,6) NULL,
  `pb_est` DECIMAL(18,6) NULL,
  `industry_pe_median` DECIMAL(18,6) NULL,
  `industry_pb_median` DECIMAL(18,6) NULL,
  `operating_cash_flow` DECIMAL(24,2) NULL,
  `operating_cash_flow_per_share` DECIMAL(18,6) NULL,
  `dividend_count` DECIMAL(10,2) NULL,
  `dividend_year_ratio` DECIMAL(8,6) NULL,
  `valuation_normal` TINYINT(1) NOT NULL DEFAULT 0,
  `cashflow_good` TINYINT(1) NOT NULL DEFAULT 0,
  `dividend_stable` TINYINT(1) NOT NULL DEFAULT 0,
  `reasons_json` JSON NULL,
  `sell_triggers_json` JSON NULL,
  `warnings_json` JSON NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_recommendations_run_symbol_horizon`
    (`run_id`, `symbol`, `horizon`),
  KEY `idx_recommendations_date_horizon` (`trade_date`, `horizon`, `score`),
  KEY `idx_recommendations_symbol_date` (`symbol`, `trade_date`),
  KEY `idx_recommendations_industry_date` (`industry`, `trade_date`),
  CONSTRAINT `fk_recommendations_run`
    FOREIGN KEY (`run_id`) REFERENCES `scan_runs` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `market_snapshots` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `run_id` BIGINT UNSIGNED NULL,
  `trade_date` DATE NOT NULL,
  `total_count` INT UNSIGNED NULL,
  `up_count` INT UNSIGNED NULL,
  `down_count` INT UNSIGNED NULL,
  `flat_count` INT UNSIGNED NULL,
  `limit_up_count` INT UNSIGNED NULL,
  `limit_down_count` INT UNSIGNED NULL,
  `strong_count` INT UNSIGNED NULL,
  `weak_count` INT UNSIGNED NULL,
  `turnover` DECIMAL(24,2) NULL,
  `median_change` DECIMAL(12,6) NULL,
  `up_ratio` DECIMAL(12,8) NULL,
  `adv_dec_ratio` DECIMAL(12,8) NULL,
  `temperature` DECIMAL(7,2) NULL,
  `temperature_label` VARCHAR(30) NULL,
  `global_score` DECIMAL(7,2) NULL,
  `global_label` VARCHAR(30) NULL,
  `global_indices_json` JSON NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_market_snapshots_date` (`trade_date`, `created_at`),
  KEY `idx_market_snapshots_run` (`run_id`),
  CONSTRAINT `fk_market_snapshots_run`
    FOREIGN KEY (`run_id`) REFERENCES `scan_runs` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `capital_hotspots` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `run_id` BIGINT UNSIGNED NOT NULL,
  `trade_date` DATE NOT NULL,
  `period_code` VARCHAR(20) NOT NULL COMMENT 'daily/weekly/monthly',
  `category` VARCHAR(20) NOT NULL COMMENT 'industry/topic',
  `rank_no` INT UNSIGNED NULL,
  `hotspot_name` VARCHAR(100) NOT NULL,
  `net_inflow_yi` DECIMAL(20,4) NULL,
  `change_pct` DECIMAL(12,6) NULL,
  `stock_count` INT UNSIGNED NULL,
  `leader` VARCHAR(64) NULL,
  `leader_change_pct` DECIMAL(12,6) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_capital_hotspots_run_item`
    (`run_id`, `period_code`, `category`, `hotspot_name`),
  KEY `idx_capital_hotspots_query`
    (`trade_date`, `period_code`, `category`, `rank_no`),
  CONSTRAINT `fk_capital_hotspots_run`
    FOREIGN KEY (`run_id`) REFERENCES `scan_runs` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `sentiment_snapshots` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `trade_date` DATE NOT NULL,
  `limit_up_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `broken_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `limit_down_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `first_board_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `second_board_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `third_board_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `high_board_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `max_streak` SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  `seal_rate` DECIMAL(12,8) NOT NULL DEFAULT 0,
  `broken_rate` DECIMAL(12,8) NOT NULL DEFAULT 0,
  `promotion_rate` DECIMAL(12,8) NOT NULL DEFAULT 0,
  `previous_premium` DECIMAL(12,6) NOT NULL DEFAULT 0,
  `previous_red_rate` DECIMAL(12,8) NOT NULL DEFAULT 0,
  `emotion_score` DECIMAL(7,2) NOT NULL DEFAULT 0,
  `emotion_stage` VARCHAR(20) NOT NULL,
  `components_json` JSON NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sentiment_snapshots_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `limit_up_ladder` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `trade_date` DATE NOT NULL,
  `symbol` VARCHAR(16) NOT NULL,
  `stock_code` VARCHAR(10) NOT NULL,
  `stock_name` VARCHAR(64) NOT NULL,
  `streak` SMALLINT UNSIGNED NOT NULL,
  `change_pct` DECIMAL(12,6) NULL,
  `price` DECIMAL(18,4) NULL,
  `turnover` DECIMAL(24,2) NULL,
  `turnover_rate` DECIMAL(12,6) NULL,
  `seal_amount` DECIMAL(24,2) NULL,
  `first_seal_time` VARCHAR(10) NULL,
  `last_seal_time` VARCHAR(10) NULL,
  `break_count` SMALLINT UNSIGNED NULL,
  `industry` VARCHAR(100) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_limit_up_ladder_date_symbol` (`trade_date`, `symbol`),
  KEY `idx_limit_up_ladder_date_streak` (`trade_date`, `streak`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `recommendation_outcomes` (
  `recommendation_id` BIGINT UNSIGNED NOT NULL,
  `run_id` BIGINT UNSIGNED NOT NULL,
  `trade_date` DATE NOT NULL,
  `horizon` VARCHAR(20) NOT NULL,
  `symbol` VARCHAR(16) NOT NULL,
  `stock_name` VARCHAR(64) NULL,
  `entry_price` DECIMAL(18,4) NOT NULL,
  `available_days` SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  `t1_return` DECIMAL(14,8) NULL,
  `t5_return` DECIMAL(14,8) NULL,
  `t20_return` DECIMAL(14,8) NULL,
  `max_gain_20` DECIMAL(14,8) NULL,
  `max_drawdown_20` DECIMAL(14,8) NULL,
  `stop_hit` TINYINT(1) NOT NULL DEFAULT 0,
  `stop_hit_date` DATE NULL,
  `target1_hit` TINYINT(1) NOT NULL DEFAULT 0,
  `target1_hit_date` DATE NULL,
  `target2_hit` TINYINT(1) NOT NULL DEFAULT 0,
  `target2_hit_date` DATE NULL,
  `target1_before_stop` TINYINT(1) NULL,
  `review_status` VARCHAR(20) NOT NULL,
  `evaluated_through` DATE NULL,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`recommendation_id`),
  KEY `idx_recommendation_outcomes_date` (`trade_date`, `horizon`),
  KEY `idx_recommendation_outcomes_symbol` (`symbol`, `trade_date`),
  CONSTRAINT `fk_recommendation_outcomes_recommendation`
    FOREIGN KEY (`recommendation_id`) REFERENCES `recommendations` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_recommendation_outcomes_run`
    FOREIGN KEY (`run_id`) REFERENCES `scan_runs` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `user_query_records` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `request_id` CHAR(36) NOT NULL,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `query_date` DATE NOT NULL COMMENT '用户当地日期，Asia/Shanghai',
  `query_type` VARCHAR(40) NOT NULL
    COMMENT 'recommendation_list/stock_analysis/hotspot/search/export',
  `stock_symbol` VARCHAR(16) NULL,
  `stock_name` VARCHAR(64) NULL,
  `source_run_id` BIGINT UNSIGNED NULL,
  `data_trade_date` DATE NULL,
  `request_params_json` JSON NULL,
  `result_snapshot_json` JSON NULL COMMENT '保存用户当时看到的结果',
  `result_status` VARCHAR(20) NOT NULL DEFAULT 'success',
  `duration_ms` INT UNSIGNED NULL,
  `ip_address` VARCHAR(45) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_query_records_request` (`request_id`),
  KEY `idx_user_query_records_user_day`
    (`user_id`, `query_date`, `created_at`),
  KEY `idx_user_query_records_stock`
    (`user_id`, `stock_symbol`, `created_at`),
  KEY `idx_user_query_records_type_day`
    (`query_type`, `query_date`),
  CONSTRAINT `fk_user_query_records_user`
    FOREIGN KEY (`user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_user_query_records_run`
    FOREIGN KEY (`source_run_id`) REFERENCES `scan_runs` (`id`)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `user_stock_analysis_results` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `query_record_id` BIGINT UNSIGNED NOT NULL,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `trade_date` DATE NOT NULL,
  `symbol` VARCHAR(16) NOT NULL,
  `stock_name` VARCHAR(64) NOT NULL,
  `score` DECIMAL(7,2) NULL,
  `rating` VARCHAR(20) NULL,
  `action` VARCHAR(30) NULL,
  `close_price` DECIMAL(18,4) NULL,
  `buy_zone_low` DECIMAL(18,4) NULL,
  `buy_zone_high` DECIMAL(18,4) NULL,
  `stop_loss` DECIMAL(18,4) NULL,
  `take_profit_1` DECIMAL(18,4) NULL,
  `take_profit_2` DECIMAL(18,4) NULL,
  `trailing_stop` DECIMAL(18,4) NULL,
  `position_pct` DECIMAL(8,6) NULL,
  `reasons_json` JSON NULL,
  `sell_triggers_json` JSON NULL,
  `warnings_json` JSON NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_stock_analysis_query` (`query_record_id`),
  KEY `idx_user_stock_analysis_user_day`
    (`user_id`, `trade_date`, `created_at`),
  KEY `idx_user_stock_analysis_symbol_day`
    (`symbol`, `trade_date`, `created_at`),
  CONSTRAINT `fk_user_stock_analysis_query`
    FOREIGN KEY (`query_record_id`) REFERENCES `user_query_records` (`id`)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_user_stock_analysis_user`
    FOREIGN KEY (`user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `user_daily_usage` (
  `user_id` BIGINT UNSIGNED NOT NULL,
  `usage_date` DATE NOT NULL,
  `login_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `query_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `stock_analysis_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `recommendation_view_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `hotspot_view_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `export_count` INT UNSIGNED NOT NULL DEFAULT 0,
  `last_activity_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`, `usage_date`),
  KEY `idx_user_daily_usage_date` (`usage_date`, `query_count`),
  CONSTRAINT `fk_user_daily_usage_user`
    FOREIGN KEY (`user_id`) REFERENCES `sys_users` (`id`)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `notification_channels` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `channel_code` VARCHAR(40) NOT NULL,
  `channel_name` VARCHAR(100) NOT NULL,
  `provider` VARCHAR(40) NOT NULL COMMENT '企业微信/钉钉/飞书/短信',
  `webhook_url` VARCHAR(1000) NULL,
  `enabled` TINYINT(1) NOT NULL DEFAULT 0,
  `usage_scene` VARCHAR(200) NULL COMMENT '买点提醒/止损提醒/到期提醒等',
  `remark` VARCHAR(500) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_notification_channels_code` (`channel_code`),
  KEY `idx_notification_channels_enabled` (`enabled`, `provider`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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

CREATE OR REPLACE VIEW `v_user_account_status` AS
SELECT
  u.`id`,
  u.`login_name`,
  u.`mobile`,
  u.`real_name`,
  u.`status`,
  u.`service_started_at`,
  u.`service_expires_at`,
  CASE
    WHEN u.`deleted_at` IS NOT NULL THEN 'deleted'
    WHEN u.`status` <> 'active' THEN u.`status`
    WHEN u.`service_started_at` IS NULL OR u.`service_expires_at` IS NULL THEN 'not_opened'
    WHEN UTC_TIMESTAMP() < u.`service_started_at` THEN 'not_started'
    WHEN UTC_TIMESTAMP() >= u.`service_expires_at` THEN 'expired'
    ELSE 'valid'
  END AS `service_status`,
  CASE
    WHEN u.`service_expires_at` IS NULL THEN NULL
    ELSE TIMESTAMPDIFF(DAY, UTC_TIMESTAMP(), u.`service_expires_at`)
  END AS `remaining_days`,
  u.`last_login_at`,
  u.`created_at`,
  u.`updated_at`
FROM `sys_users` u;

INSERT INTO `sys_roles`
  (`role_code`, `role_name`, `description`, `is_system`)
VALUES
  ('ADMIN', '系统管理员', '管理账号、权限、套餐、订单和全部业务数据', 1),
  ('MEMBER', '付费会员', '在有效服务期内使用股票分析与推荐功能', 1)
ON DUPLICATE KEY UPDATE
  `role_name` = VALUES(`role_name`),
  `description` = VALUES(`description`),
  `is_system` = VALUES(`is_system`);

INSERT INTO `sys_permissions`
  (`permission_code`, `permission_name`, `module_code`, `description`)
VALUES
  ('dashboard.view', '查看市场总览', 'dashboard', NULL),
  ('recommendation.view', '查看推荐股票', 'recommendation', NULL),
  ('stock.analyze', '分析单只股票', 'stock', NULL),
  ('hotspot.view', '查看资金热点', 'hotspot', NULL),
  ('data.export', '导出查询数据', 'data', NULL),
  ('account.manage', '管理会员账号', 'account', NULL),
  ('subscription.manage', '管理套餐与到期时间', 'subscription', NULL),
  ('order.manage', '管理订单与支付', 'order', NULL),
  ('audit.view', '查看操作审计', 'audit', NULL),
  ('system.manage', '系统配置管理', 'system', NULL)
ON DUPLICATE KEY UPDATE
  `permission_name` = VALUES(`permission_name`),
  `module_code` = VALUES(`module_code`),
  `description` = VALUES(`description`);

INSERT IGNORE INTO `sys_role_permissions` (`role_id`, `permission_id`)
SELECT r.`id`, p.`id`
FROM `sys_roles` r
CROSS JOIN `sys_permissions` p
WHERE r.`role_code` = 'ADMIN';

INSERT IGNORE INTO `sys_role_permissions` (`role_id`, `permission_id`)
SELECT r.`id`, p.`id`
FROM `sys_roles` r
JOIN `sys_permissions` p
  ON p.`permission_code` IN (
    'dashboard.view',
    'recommendation.view',
    'stock.analyze',
    'hotspot.view',
    'data.export'
  )
WHERE r.`role_code` = 'MEMBER';

INSERT INTO `subscription_plans`
  (`plan_code`, `plan_name`, `duration_months`, `price`, `currency`, `daily_query_limit`, `features_json`, `status`)
VALUES
  ('trial_7d', '7天试用', 1, 0.00, 'CNY', 30,
   JSON_ARRAY('每日推荐', '个股买卖点', '持仓做T方案体验'), 'active'),
  ('month', '月卡', 1, 10.00, 'CNY', NULL,
   JSON_ARRAY('每日推荐', '个股买卖点', '持仓做T方案', '预警中心'), 'active'),
  ('quarter', '季卡', 3, 28.00, 'CNY', NULL,
   JSON_ARRAY('每日推荐', '可信度评分', '持仓中心', '预警中心', '策略回测'), 'active'),
  ('half_year', '半年卡', 6, 52.00, 'CNY', NULL,
   JSON_ARRAY('全部核心功能', '策略复盘', '市场地图', '数据体检'), 'active'),
  ('year', '年卡', 12, 98.00, 'CNY', NULL,
   JSON_ARRAY('全部功能', '策略回测', '会员专属复盘', '运营提醒'), 'active')
ON DUPLICATE KEY UPDATE
  `plan_name` = VALUES(`plan_name`),
  `duration_months` = VALUES(`duration_months`),
  `price` = VALUES(`price`),
  `daily_query_limit` = VALUES(`daily_query_limit`),
  `features_json` = VALUES(`features_json`),
  `status` = VALUES(`status`);

SET FOREIGN_KEY_CHECKS = 1;
