package com.gshared.back.job.service;

import com.gshared.back.global.storage.LocalObjectStorage;
import com.gshared.back.job.domain.Job;
import com.gshared.back.user.domain.User;
import com.gshared.back.workload.domain.Workload;
import com.gshared.back.workload.repository.WorkloadRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import java.io.ByteArrayInputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 다운로드 저장 파일 이름 (프런트 요청 2026-09-18).
 * 크로스 오리진 다운로드에서 브라우저가 프런트 지정 이름을 무시하므로,
 * 서버가 규칙대로 조립한 이름을 Content-Disposition 으로 실어 주는지 검증한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class JobDownloadFilenameTest {

    private static final String KEY = "jobs/999999/result.tar";

    @Autowired
    private JobService jobService;
    @Autowired
    private WorkloadRepository workloadRepository;
    @Autowired
    private LocalObjectStorage storage;
    @Autowired
    private MockMvc mockMvc;

    @AfterEach
    void cleanUp() {
        storage.delete(KEY);   // 실제 파일을 쓰는 테스트라 흔적을 지운다
    }

    /** 저장 없이 이름 규칙만 보는 용도의 Job. (다운로드 이름은 상태·저장 여부와 무관한 순수 조립) */
    private Job jobWith(Long workloadId, String adapterName) {
        User owner = User.createUser("name-test@gshared.io", "encoded", "이름테스터");
        Job job = Job.create(owner, workloadId, 1L, 1L, "AUTO", 1, 16, 0.0002, 512,
                100.0, "runtime-xgb-v1", null, 9L);
        job.recordResultUpload(KEY, 1L, "ab".repeat(32));
        if (adapterName != null) {
            job.renameAdapter(adapterName);
        }
        return job;
    }

    @Test
    @DisplayName("학습 결과(어댑터): <어댑터이름>-v<버전>.<확장자> — 이름은 서버가 정본")
    void adapterFilename() {
        Job job = jobWith(1L, "cs-상담봇");
        assertThat(jobService.downloadFilename(job)).isEqualTo("cs-상담봇-v1.tar");
    }

    @Test
    @DisplayName("어댑터 이름이 없으면: <워크로드이름>-job-<id>.<확장자> — 스토리지 키를 노출하지 않는다")
    void workloadFilename() {
        Workload seed = workloadRepository.findAll().get(0);
        Job job = jobWith(seed.getId(), null);
        assertThat(jobService.downloadFilename(job))
                .startsWith(seed.getName().replaceAll("[\\\\/:*?\"<>|\\r\\n]", "_"))
                .contains("-job-")
                .endsWith(".tar");
    }

    @Test
    @DisplayName("파일 이름의 경로·금지 문자는 _ 로 — 헤더 주입과 저장 오류 방지")
    void unsafeCharactersReplaced() {
        Job job = jobWith(1L, "a/b\\c\"d\r\ne");
        assertThat(jobService.downloadFilename(job)).isEqualTo("a_b_c_d__e-v1.tar");
    }

    @Test
    @DisplayName("서명 URL 의 filename 이 Content-Disposition(RFC 5987, 한글) 으로 내려온다")
    void contentDispositionHeader() throws Exception {
        storage.write(KEY, new ByteArrayInputStream("adapter-bytes".getBytes(StandardCharsets.UTF_8)));
        String url = storage.presignDownload(KEY, Duration.ofMinutes(15), "cs-상담봇-v2.tar").url();
        URI uri = URI.create(url);

        // 문자열이 아니라 URI 로 넘긴다 — 문자열이면 MockMvc 가 쿼리의 퍼센트 인코딩을 풀지 않아
        // (실제 톰캣과 달리) filename 이 이중 인코딩된다.
        String disposition = mockMvc.perform(get(URI.create(uri.getPath() + "?" + uri.getRawQuery())))
                .andExpect(status().isOk())
                .andReturn().getResponse().getHeader(HttpHeaders.CONTENT_DISPOSITION);
        // 비 ASCII 를 아는 브라우저용(RFC 5987) + 모르는 클라이언트용 ASCII 대체 이름
        assertThat(disposition).contains("filename*=UTF-8''cs-%EC%83%81%EB%8B%B4%EB%B4%87-v2.tar");
        assertThat(disposition).contains("attachment");
    }

    @Test
    @DisplayName("filename 없이 발급된 기존 URL 은 예전처럼 저장 키 이름으로 동작한다 — 하위 호환")
    void withoutFilenameStillWorks() throws Exception {
        storage.write(KEY, new ByteArrayInputStream("x".getBytes(StandardCharsets.UTF_8)));
        String url = storage.presignDownload(KEY, Duration.ofMinutes(15)).url();
        URI uri = URI.create(url);

        mockMvc.perform(get(uri.getPath() + "?" + uri.getRawQuery()))
                .andExpect(status().isOk())
                .andExpect(header().string(HttpHeaders.CONTENT_DISPOSITION, containsString("result.tar")));
    }
}
