package com.gshared.back.worker.service;

import com.gshared.back.worker.domain.GpuGrade;
import com.gshared.back.worker.dto.response.GpuResponse;
import com.gshared.back.worker.repository.WorkerRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * GPU 등급 조회.
 *
 * <p>등급 테이블은 없다. 지금 붙어 있는 워커를 모델명으로 묶어 만들며, 그래서 목록은
 * 시간에 따라 달라진다 — 아무도 PC 를 켜 두지 않으면 빈 목록이다. 그게 사실이라
 * 빈 목록을 감추지 않는다(고를 수 있는 GPU 가 없다는 것을 화면이 알아야 한다).
 */
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class GpuCatalogService {

    private final WorkerRepository workerRepository;

    public List<GpuResponse> getGpus() {
        return grades().stream().map(GpuResponse::from).toList();
    }

    /** 다른 서비스(견적·GPU 후보)가 같은 묶음을 봐야 하므로 등급 자체도 내어 준다. */
    public List<GpuGrade> grades() {
        return GpuGrade.from(workerRepository.findAvailableWorkers());
    }
}
