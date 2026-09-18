#!/usr/bin/env bash
#
# G-shared 운영 데이터 백업
#
# 무엇을  MySQL `gshared` 스키마와 오브젝트 스토리지(`storage/`) 두 가지.
# 어디서  배포 서버에서 ubuntu 계정으로 실행한다. 노트북에서 돌리는 것이 아니다.
# 왜      워커의 벤치마크 실측값과 완주한 작업 기록은 혼자 다시 만들 수 없다.
#         벤치마크는 공급자가 자기 PC 에서만 낼 수 있고, 완주 기록은 실제로 학습을
#         다시 끝내야 생긴다. 자세한 근거는 같은 디렉터리의 README.md 에 있다.
#
# 이 스크립트는 읽기만 한다. DB 에 쓰지 않고, 컨테이너를 재시작하지 않으며,
# 오래된 백업을 지우지도 않는다. 정리는 사람이 한다 — 지우는 코드에 버그가 나면
# 백업을 지키려고 만든 도구가 백업을 없앤다.
#
# 사용법
#   ./backup.sh
#   OUT_DIR=/mnt/other ./backup.sh        # 내보낼 위치를 바꾼다
#   SKIP_WRITE_CHECK=1 ./backup.sh        # 쓰기 확인을 건너뛴다 (권장하지 않는다)

# -e  실패하면 그 자리에서 멈춘다. 반쯤 만들어진 백업을 성공으로 착각하지 않기 위해서다.
# -u  정의되지 않은 변수를 쓰면 오류. 오타로 빈 경로에 쓰는 사고를 막는다.
# -o pipefail  파이프 중간이 실패해도 잡는다. 기본값은 마지막 명령의 결과만 본다.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/S15P21E204}"
OUT_DIR="${OUT_DIR:-$HOME}"
CONTAINER="${CONTAINER:-gshared-mysql}"
DATABASE="${DATABASE:-gshared}"

# 두 파일이 같은 시점의 한 쌍이라는 것을 이름으로 묶는다. date 를 명령마다 따로
# 부르면 분이 넘어가는 순간 이름이 어긋나서 나중에 짝을 찾을 수 없다.
STAMP="$(date +%Y%m%d-%H%M)"
DB_FILE="$OUT_DIR/backup-db-$STAMP.sql"
STORAGE_FILE="$OUT_DIR/backup-storage-$STAMP.tar.gz"

echo "시각 도장: $STAMP"
echo "내보낼 위치: $OUT_DIR"
echo

# ── 1. 사전 확인 ────────────────────────────────────────────────────────────
# 워커가 결과물을 올리는 중에 tar 를 뜨면 반쯤 복사된 result.tar 이 백업에 들어간다.
# 서비스가 깨지지는 않지만 그 작업의 산출물만 못 쓰게 된다.
if [ "${SKIP_WRITE_CHECK:-0}" != "1" ]; then
    recent="$(sudo find "$PROJECT_DIR/storage" -newermt '-2 minutes' -print -quit 2>/dev/null || true)"
    if [ -n "$recent" ]; then
        echo "중단: storage 에 최근 2분 내 쓰기가 있다." >&2
        echo "      $recent" >&2
        echo "      작업이 결과물을 올리는 중일 수 있다. 잠시 뒤 다시 실행하라." >&2
        exit 1
    fi
fi

# ── 2. DB 덤프 ──────────────────────────────────────────────────────────────
# --single-transaction  InnoDB 의 MVCC 스냅샷으로 뜬다. 이 플래그가 없으면 mysqldump 가
#                       테이블을 잠그고 그동안 서비스가 멈춘다. 무중단은 이 한 줄에
#                       달려 있다. 지우지 마라.
# --routines            저장 프로시저와 함수. 기본값이 꺼짐이라 명시해야 한다.
# --triggers            트리거. 기본값이 켜짐이지만 적어 두어 의도를 남긴다.
#
# MYSQL_PWD 로 넘기는 이유: `-p"$PW"` 로 쓰면 컨테이너 안의 프로세스 목록에 암호가
# 그대로 보인다. 환경변수로 넘기면 argv 에 실리지 않고 경고도 나오지 않는다.
#
# `\$MYSQL_ROOT_PASSWORD` 의 역슬래시가 중요하다. 호스트 셸이 아니라 컨테이너 안의
# sh 가 변수를 풀어야 한다. 호스트에는 그 변수가 없어서 빈 암호가 된다.
#
# `>` 가 따옴표 바깥에 있는 것도 중요하다. 리디렉션은 호스트 셸이 처리하므로 파일이
# 서버에 생긴다. 따옴표 안에 넣으면 컨테이너 안에 생기고, 컨테이너를 지우면 백업도
# 함께 사라진다.
echo "DB 덤프: $DB_FILE"
docker exec "$CONTAINER" sh -c \
    "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" mysqldump -uroot --single-transaction --routines --triggers $DATABASE" \
    > "$DB_FILE"

# users 테이블에 이메일과 비밀번호 해시, 지갑 주소가 들어 있다. 기본 644 로 두면
# 같은 서버의 다른 계정이 읽을 수 있다.
chmod 600 "$DB_FILE"

# ── 3. 스토리지 압축 ────────────────────────────────────────────────────────
# -C 로 기준 디렉터리를 옮긴 뒤 `storage` 를 담는다. 이렇게 해야 압축 파일 안의 경로가
# `storage/jobs/...` 가 된다. -C 없이 절대 경로를 주면 `home/ubuntu/...` 가 통째로
# 들어가서 풀 때 경로가 꼬인다.
#
# 내용물이 학습된 가중치(safetensors)라 압축률은 낮다. 부동소수점 배열에는 gzip 이
# 찾을 반복 패턴이 거의 없다. 10% 남짓 줄면 정상이다.
echo "스토리지 압축: $STORAGE_FILE"
sudo tar czf "$STORAGE_FILE" -C "$PROJECT_DIR" storage
sudo chown "$(id -u):$(id -g)" "$STORAGE_FILE"
chmod 600 "$STORAGE_FILE"

# ── 4. 검증 ────────────────────────────────────────────────────────────────
# 만들기만 하고 확인하지 않으면 "파일은 생겼는데 비어 있는" 상태를 못 잡는다.
# 백업이 실패하는 가장 흔한 방식이 그것이다.
echo
echo "검증 중"

# grep -c 는 0건일 때 종료 코드 1 을 내므로 set -e 에 걸린다. 직접 판정한다.
inserts="$(grep -c 'INSERT INTO' "$DB_FILE" || true)"
if [ "$inserts" -eq 0 ]; then
    echo "실패: 덤프에 INSERT 문이 하나도 없다." >&2
    exit 1
fi

# mysqldump 는 테이블당 확장 INSERT 하나를 쓴다. 그래서 이 숫자는 행 수가 아니라
# 데이터가 있는 테이블 수다. 개수 대신 이름으로 확인한다.
for table in users workers jobs; do
    if ! grep -q "INSERT INTO \`$table\`" "$DB_FILE"; then
        echo "실패: $table 테이블 데이터가 덤프에 없다." >&2
        exit 1
    fi
done

# 압축이 끝까지 읽히는지 본다. 중간에 끊겼으면 여기서 실패한다.
tar tzf "$STORAGE_FILE" > /dev/null

echo "  INSERT 문 ${inserts}건 (= 데이터가 있는 테이블 수)"
echo "  users·workers·jobs 포함 확인"
echo "  압축 무결성 확인"

# ── 5. 결과 ────────────────────────────────────────────────────────────────
echo
ls -lh "$DB_FILE" "$STORAGE_FILE"
echo
echo "아직 절반이다. 백업은 원본과 운명을 달리해야 백업이다."
echo "노트북에서 아래를 실행해 내려받아라."
echo
# 경로를 변수에서 그대로 찍는다. 문구에 `~/` 를 박아 두면 OUT_DIR 을 바꿨을 때
# 틀린 위치를 안내하게 된다.
echo "  scp -i ~/.ssh/J15E204T.pem \\"
echo "    'ubuntu@j15e204.p.ssafy.io:$DB_FILE' \\"
echo "    'ubuntu@j15e204.p.ssafy.io:$STORAGE_FILE' \\"
echo "    ~/backups/"
