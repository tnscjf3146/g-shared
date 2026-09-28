-- MySQL dump 10.13  Distrib 8.4.11, for Linux (x86_64)
--
-- Host: localhost    Database: gshared
-- ------------------------------------------------------
-- Server version	8.4.11

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Current Database: `gshared`
--

CREATE DATABASE /*!32312 IF NOT EXISTS*/ `gshared` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci */ /*!80016 DEFAULT ENCRYPTION='N' */;

USE `gshared`;

--
-- Table structure for table `base_models`
--

DROP TABLE IF EXISTS `base_models`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `base_models` (
  `model_id` bigint NOT NULL AUTO_INCREMENT,
  `slug` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `workload_id` bigint NOT NULL,
  `display_name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `model_ref` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `model_revision` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `param_count` bigint DEFAULT NULL,
  `min_vram_mb` int DEFAULT NULL,
  `weights_hash` char(66) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `source_uri` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`model_id`),
  UNIQUE KEY `uk_base_models_slug` (`slug`),
  KEY `fk_base_models_workload` (`workload_id`),
  CONSTRAINT `fk_base_models_workload` FOREIGN KEY (`workload_id`) REFERENCES `workloads` (`workload_id`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `base_models`
--

LOCK TABLES `base_models` WRITE;
/*!40000 ALTER TABLE `base_models` DISABLE KEYS */;
INSERT INTO `base_models` VALUES (1,'qwen2.5-7b',1,'Qwen 2.5 7B','Qwen/Qwen2.5-7B',NULL,7000000000,NULL,NULL,NULL,0,'2026-09-17 01:13:50.278850','2026-09-17 01:13:50.278850'),(2,'qwen3-0.6b',1,'Qwen3 0.6B','Qwen/Qwen3-0.6B','c1899de289a04d12100db370d81485cdf75e47ca',600000000,NULL,NULL,'https://huggingface.co/Qwen/Qwen3-0.6B',1,'2026-09-17 01:13:52.003971','2026-09-17 01:13:52.003971'),(3,'qwen3-1.7b',1,'Qwen3 1.7B','Qwen/Qwen3-1.7B','70d244cc86ccca08cf5af4e1e306ecf908b1ad5e',1700000000,NULL,NULL,'https://huggingface.co/Qwen/Qwen3-1.7B',1,'2026-09-17 01:13:52.006545','2026-09-17 01:13:52.006545');
/*!40000 ALTER TABLE `base_models` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `datasets`
--

DROP TABLE IF EXISTS `datasets`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `datasets` (
  `dataset_id` bigint NOT NULL AUTO_INCREMENT,
  `owner_id` bigint NOT NULL,
  `size_bytes` bigint NOT NULL,
  `sample_count` int NOT NULL,
  `total_tokens` bigint NOT NULL,
  `effective_tokens` bigint NOT NULL,
  `avg_seq_length` double NOT NULL,
  `max_seq_length` int NOT NULL,
  `data_hash` char(66) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `storage_uri` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `uploaded_at` datetime(6) DEFAULT NULL,
  `expires_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`dataset_id`),
  KEY `fk_datasets_owner` (`owner_id`),
  CONSTRAINT `fk_datasets_owner` FOREIGN KEY (`owner_id`) REFERENCES `users` (`user_id`)
) ENGINE=InnoDB AUTO_INCREMENT=1769 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `datasets`
--

LOCK TABLES `datasets` WRITE;
/*!40000 ALTER TABLE `datasets` DISABLE KEYS */;
/*!40000 ALTER TABLE `datasets` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `flyway_schema_history`
--

DROP TABLE IF EXISTS `flyway_schema_history`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `flyway_schema_history` (
  `installed_rank` int NOT NULL,
  `version` varchar(50) DEFAULT NULL,
  `description` varchar(200) NOT NULL,
  `type` varchar(20) NOT NULL,
  `script` varchar(1000) NOT NULL,
  `checksum` int DEFAULT NULL,
  `installed_by` varchar(100) NOT NULL,
  `installed_on` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `execution_time` int NOT NULL,
  `success` tinyint(1) NOT NULL,
  PRIMARY KEY (`installed_rank`),
  KEY `flyway_schema_history_s_idx` (`success`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `flyway_schema_history`
--

LOCK TABLES `flyway_schema_history` WRITE;
/*!40000 ALTER TABLE `flyway_schema_history` DISABLE KEYS */;
INSERT INTO `flyway_schema_history` VALUES (1,'1','create users','SQL','V1__create_users.sql',679240131,'gshared','2026-09-17 01:13:47',77,1),(2,'2','add wallet address unique','SQL','V2__add_wallet_address_unique.sql',-827585223,'gshared','2026-09-17 01:13:47',206,1),(3,'3','create workers','SQL','V3__create_workers.sql',1210429500,'gshared','2026-09-17 01:13:47',103,1),(4,'4','create jobs','SQL','V4__create_jobs.sql',723496298,'gshared','2026-09-17 01:13:48',76,1),(5,'5','create datasets','SQL','V5__create_datasets.sql',550047871,'gshared','2026-09-17 01:13:48',81,1),(6,'6','create workloads and base models','SQL','V6__create_workloads_and_base_models.sql',1571867699,'gshared','2026-09-17 01:13:48',188,1),(7,'7','alter jobs for scheduler','SQL','V7__alter_jobs_for_scheduler.sql',-187941481,'gshared','2026-09-17 01:13:49',1194,1),(8,'8','catalog integer pk','SQL','V8__catalog_integer_pk.sql',427440902,'gshared','2026-09-17 01:13:50',953,1),(9,'9','catalog display fields','SQL','V9__catalog_display_fields.sql',-2054673313,'gshared','2026-09-17 01:13:51',101,1),(10,'10','worker contract v1','SQL','V10__worker_contract_v1.sql',366601290,'gshared','2026-09-17 01:13:51',587,1),(11,'11','job reconciliation','SQL','V11__job_reconciliation.sql',-1501948710,'gshared','2026-09-17 01:13:51',205,1),(12,'12','qwen3 catalog','SQL','V12__qwen3_catalog.sql',38092295,'gshared','2026-09-17 01:13:52',10,1),(13,'13','job escrow binding','SQL','V13__job_escrow_binding.sql',1011798819,'gshared','2026-09-17 01:13:52',368,1),(14,'14','runtime image canonical','SQL','V14__runtime_image_canonical.sql',-2111626025,'gshared','2026-09-17 01:13:52',26,1),(15,'15','job dataset upload','SQL','V15__job_dataset_upload.sql',-355103870,'gshared','2026-09-17 01:13:52',297,1),(16,'16','job refund confirmation','SQL','V16__job_refund_confirmation.sql',1774565530,'gshared','2026-09-18 05:46:07',448,1),(17,'17','job adapter input','SQL','V17__job_adapter_input.sql',-1856725871,'gshared','2026-09-18 05:46:08',398,1),(18,'18','job adapter identity','SQL','V18__job_adapter_identity.sql',-539300060,'gshared','2026-09-18 05:46:08',382,1),(19,'19','runtime image adapter capable','SQL','V19__runtime_image_adapter_capable.sql',-2053255910,'gshared','2026-09-18 05:46:08',11,1),(20,'20','timestamps utc to seoul','SQL','V20__timestamps_utc_to_seoul.sql',-169264474,'gshared','2026-09-18 05:46:08',21,1),(21,'21','worker peak vram and model vram floor','SQL','V21__worker_peak_vram_and_model_vram_floor.sql',-765312368,'gshared','2026-09-21 05:46:38',364,1),(22,'22','job cancel reason','SQL','V22__job_cancel_reason.sql',1714872333,'gshared','2026-09-21 07:53:59',466,1),(23,'23','worker reliability counts','SQL','V23__worker_reliability_counts.sql',1027811539,'gshared','2026-09-28 01:29:04',291,1),(24,'24','runtime image result verification','SQL','V24__runtime_image_result_verification.sql',195151559,'gshared','2026-09-28 01:29:04',9,1);
/*!40000 ALTER TABLE `flyway_schema_history` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `jobs`
--

DROP TABLE IF EXISTS `jobs`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `jobs` (
  `job_id` bigint NOT NULL AUTO_INCREMENT,
  `owner_id` bigint NOT NULL,
  `workload_id` bigint NOT NULL,
  `base_model_id` bigint NOT NULL,
  `dataset_id` bigint NOT NULL,
  `dataset_object_key` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `dataset_size_bytes` bigint DEFAULT NULL,
  `dataset_sha256` char(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `dataset_uploaded_at` datetime(6) DEFAULT NULL,
  `dataset_deleted_at` datetime(6) DEFAULT NULL,
  `adapter_required` tinyint(1) NOT NULL DEFAULT '0',
  `adapter_object_key` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `adapter_size_bytes` bigint DEFAULT NULL,
  `adapter_sha256` char(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `adapter_uploaded_at` datetime(6) DEFAULT NULL,
  `adapter_deleted_at` datetime(6) DEFAULT NULL,
  `adapter_name` varchar(60) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `adapter_version` int NOT NULL DEFAULT '1',
  `parent_job_id` bigint DEFAULT NULL,
  `training_mode` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `epochs` int NOT NULL,
  `batch_size` int NOT NULL,
  `learning_rate` double NOT NULL,
  `max_length` int NOT NULL,
  `predicted_runtime_sec` double NOT NULL,
  `scheduler_model_version` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `recommended_worker_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `provider_wallet` varchar(42) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `estimated_cost` bigint NOT NULL,
  `external_ref` char(66) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `escrow_chain_id` bigint DEFAULT NULL,
  `escrow_contract_address` varchar(42) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `escrow_job_id` varchar(78) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `fund_tx_hash` char(66) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `actual_runtime_sec` double DEFAULT NULL,
  `actual_cost` bigint DEFAULT NULL,
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `assigned_worker_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `accept_tx_hash` char(66) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `result_hash` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `result_object_key` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `result_size_bytes` bigint DEFAULT NULL,
  `result_sha256` char(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `result_url` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `submit_tx_hash` char(66) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `abort_tx_hash` char(66) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `fail_reason` text COLLATE utf8mb4_unicode_ci,
  `reconciliation_reason` text COLLATE utf8mb4_unicode_ci,
  `progress_percent` double DEFAULT NULL,
  `progress_step` int DEFAULT NULL,
  `progress_total_steps` int DEFAULT NULL,
  `progress_epoch` int DEFAULT NULL,
  `progress_total_epochs` int DEFAULT NULL,
  `progress_elapsed_sec` int DEFAULT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `refund_confirmed_at` datetime(6) DEFAULT NULL,
  `cancel_reason` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`job_id`),
  UNIQUE KEY `uk_jobs_external_ref` (`external_ref`),
  UNIQUE KEY `uk_jobs_fund_tx_hash` (`fund_tx_hash`),
  UNIQUE KEY `uk_jobs_escrow_job` (`escrow_chain_id`,`escrow_contract_address`,`escrow_job_id`),
  KEY `idx_jobs_owner_status` (`owner_id`,`status`),
  KEY `fk_jobs_dataset` (`dataset_id`),
  KEY `fk_jobs_worker` (`assigned_worker_id`),
  KEY `fk_jobs_workload` (`workload_id`),
  KEY `fk_jobs_base_model` (`base_model_id`),
  KEY `idx_jobs_status` (`status`),
  KEY `idx_jobs_dataset_cleanup` (`dataset_deleted_at`,`status`),
  KEY `idx_jobs_refund_recovery` (`status`,`refund_confirmed_at`),
  KEY `idx_jobs_adapter_cleanup` (`adapter_deleted_at`,`status`),
  KEY `idx_jobs_adapter_parent` (`parent_job_id`),
  CONSTRAINT `fk_jobs_base_model` FOREIGN KEY (`base_model_id`) REFERENCES `base_models` (`model_id`),
  CONSTRAINT `fk_jobs_dataset` FOREIGN KEY (`dataset_id`) REFERENCES `datasets` (`dataset_id`),
  CONSTRAINT `fk_jobs_owner` FOREIGN KEY (`owner_id`) REFERENCES `users` (`user_id`),
  CONSTRAINT `fk_jobs_worker` FOREIGN KEY (`assigned_worker_id`) REFERENCES `workers` (`worker_id`),
  CONSTRAINT `fk_jobs_workload` FOREIGN KEY (`workload_id`) REFERENCES `workloads` (`workload_id`)
) ENGINE=InnoDB AUTO_INCREMENT=1764 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `jobs`
--

LOCK TABLES `jobs` WRITE;
/*!40000 ALTER TABLE `jobs` DISABLE KEYS */;
/*!40000 ALTER TABLE `jobs` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `users`
--

DROP TABLE IF EXISTS `users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `user_id` bigint NOT NULL AUTO_INCREMENT,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `password` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `nickname` varchar(15) COLLATE utf8mb4_unicode_ci NOT NULL,
  `role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `wallet_address` varchar(42) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`user_id`),
  UNIQUE KEY `uk_users_email` (`email`),
  UNIQUE KEY `uk_users_wallet_address` (`wallet_address`)
) ENGINE=InnoDB AUTO_INCREMENT=3460 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `users`
--

LOCK TABLES `users` WRITE;
/*!40000 ALTER TABLE `users` DISABLE KEYS */;
/*!40000 ALTER TABLE `users` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `workers`
--

DROP TABLE IF EXISTS `workers`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `workers` (
  `worker_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `owner_id` bigint NOT NULL,
  `worker_token` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `operator_address` char(42) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `client_version` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `device_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `os_version` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `wsl_available` tinyint(1) DEFAULT NULL,
  `gpu_model` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `vram_mb` int DEFAULT NULL,
  `driver_version` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `benchmark_tokens_per_sec` double DEFAULT NULL,
  `benchmark_peak_vram_mb` int DEFAULT NULL,
  `benchmark_version` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `current_job_id` bigint DEFAULT NULL,
  `last_heartbeat_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `completed_count` bigint NOT NULL DEFAULT '0',
  `failed_count` bigint NOT NULL DEFAULT '0',
  PRIMARY KEY (`worker_id`),
  UNIQUE KEY `uk_workers_token` (`worker_token`),
  KEY `fk_workers_owner` (`owner_id`),
  CONSTRAINT `fk_workers_owner` FOREIGN KEY (`owner_id`) REFERENCES `users` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `workers`
--

LOCK TABLES `workers` WRITE;
/*!40000 ALTER TABLE `workers` DISABLE KEYS */;
/*!40000 ALTER TABLE `workers` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `workloads`
--

DROP TABLE IF EXISTS `workloads`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `workloads` (
  `workload_id` bigint NOT NULL AUTO_INCREMENT,
  `slug` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `kind` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'TRAIN_LORA',
  `name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `image_digest` char(71) COLLATE utf8mb4_unicode_ci NOT NULL,
  `image_repository` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `min_vram_mb` int NOT NULL,
  `default_config` json DEFAULT NULL,
  `limits` json DEFAULT NULL,
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`workload_id`),
  UNIQUE KEY `uk_workloads_slug` (`slug`)
) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `workloads`
--

LOCK TABLES `workloads` WRITE;
/*!40000 ALTER TABLE `workloads` DISABLE KEYS */;
INSERT INTO `workloads` VALUES (1,'qlora-sft-v1','TRAIN_LORA','QLoRA SFT 텍스트 파인튜닝','준비된 데이터셋으로 베이스 모델에 LoRA 어댑터를 학습합니다.','sha256:3f054d6d9fc28546818c424e2aa4167a23369463ecaa035e940673e77841e4ec','le3560/gshared-qlora-sft-runtime',8192,NULL,NULL,1,'2026-09-17 01:13:50.275177','2026-09-17 01:13:50.275177');
/*!40000 ALTER TABLE `workloads` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Dumping routines for database 'gshared'
--
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-09-28  1:30:35
