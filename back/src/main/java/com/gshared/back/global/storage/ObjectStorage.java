package com.gshared.back.global.storage;

import java.time.Duration;
import java.time.Instant;
import java.util.Optional;

/**
 * 오브젝트 스토리지 창구. Backend 는 파일을 직접 주고받지 않고 <b>서명 URL 만 발급</b>한다 —
 * 데이터셋 내려받기·결과물 올리기는 워커가 그 URL 로 스토리지와 직접 한다 (규격 §11, §17).
 *
 * <p>구현체를 갈아끼울 수 있게 인터페이스로 둔다. 지금은 Backend 자신이 파일을 들고 있는
 * {@link LocalObjectStorage} 하나뿐이고, S3/MinIO 로 옮길 때 이 계약만 지키면 호출부는 그대로다.
 */
public interface ObjectStorage {

    /** 올리기용 서명 URL. 워커는 이 URL 에 PUT 으로 본문을 그대로 보낸다. */
    SignedUrl presignUpload(String objectKey, String contentType, Duration ttl);

    /** 내려받기용 서명 URL. */
    SignedUrl presignDownload(String objectKey, Duration ttl);

    /** 오브젝트가 실제로 존재하는지와 그 크기·해시. 없으면 empty. (업로드 완료 검증용 — 규격 §18) */
    Optional<ObjectStat> stat(String objectKey);

    /** 오브젝트 삭제. 이미 없으면 아무 일도 하지 않는다 — cleanup 이 같은 키를 두 번 지워도 안전해야 한다. */
    void delete(String objectKey);

    record SignedUrl(String url, Instant expiresAt) {
    }

    /** sha256 은 0x 없는 64자리 hex — 파일 검증용 표기(규격 §1.4). */
    record ObjectStat(long sizeBytes, String sha256) {
    }
}
