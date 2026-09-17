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

    /**
     * 크기만. {@link #stat} 은 해시를 위해 파일을 끝까지 읽으므로, 크기 상한을 먼저 볼 때는 이걸 쓴다 —
     * 상한을 넘는 파일을 통째로 해시한 뒤에 거절하면 그 I/O 가 그대로 낭비다. 없으면 empty.
     */
    Optional<Long> size(String objectKey);

    /**
     * 오브젝트 봉인 — 이후 같은 키로의 업로드(write)를 거부한다. 멱등.
     * 완료 검증(§18)이 끝난 파일을, 아직 살아 있는 업로드 서명 URL(TTL 1h)로
     * 바꿔치기하는 것을 막는다: 검증된 해시(온체인 기록)와 실제 파일이 계속 같게.
     */
    void seal(String objectKey);

    /** 오브젝트 삭제. 이미 없으면 아무 일도 하지 않는다 — cleanup 이 같은 키를 두 번 지워도 안전해야 한다. */
    void delete(String objectKey);

    /**
     * 오브젝트의 일부만 읽는다 (형식 검사용). tar 처럼 "헤더만 띄엄띄엄 읽으면 목록을 알 수 있는" 파일을
     * 통째로 메모리에 올리지 않고 훑기 위한 것이다 — S3 에서는 Range 헤더 한 번에 대응된다.
     *
     * @return 실제로 읽은 바이트. 파일이 없으면 empty, offset 이 파일 끝을 넘으면 빈 배열.
     *         파일 끝에 걸리면 요청한 length 보다 짧을 수 있다.
     */
    Optional<byte[]> readRange(String objectKey, long offset, int length);

    record SignedUrl(String url, Instant expiresAt) {
    }

    /** sha256 은 0x 없는 64자리 hex — 파일 검증용 표기(규격 §1.4). */
    record ObjectStat(long sizeBytes, String sha256) {
    }
}
