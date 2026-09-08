package com.gshared.back.job.dto.response;

import org.springframework.data.domain.Page;

import java.util.List;

/**
 * 목록 조회 응답. 내용(content) + 페이지 메타정보.
 * Spring 의 Page 객체를 프론트가 쓰기 쉬운 형태로 재포장한다.
 */
public record JobPageResponse(
        List<JobSummaryResponse> content,
        int page,           // 현재 페이지 (0부터)
        int totalPages,     // 전체 페이지 수
        long totalElements  // 전체 Job 개수
) {
    public static JobPageResponse from(Page<JobSummaryResponse> page) {
        return new JobPageResponse(
                page.getContent(),
                page.getNumber(),
                page.getTotalPages(),
                page.getTotalElements()
        );
    }
}
