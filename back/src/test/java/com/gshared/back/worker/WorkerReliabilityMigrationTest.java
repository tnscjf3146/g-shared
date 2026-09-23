package com.gshared.back.worker;

import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class WorkerReliabilityMigrationTest {
    @Test
    void migrationAddsNonNullZeroBasedCounters() throws Exception {
        String sql = Files.readString(Path.of(
                "src/main/resources/db/migration/V23__worker_reliability_counts.sql"));

        assertThat(sql).contains("completed_count BIGINT NOT NULL DEFAULT 0");
        assertThat(sql).contains("failed_count BIGINT NOT NULL DEFAULT 0");
        assertThat(sql).doesNotContain("UPDATE jobs").doesNotContain("UPDATE workers");
    }
}
