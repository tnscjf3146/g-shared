# nginx 설정 (인프라 기록)

G-shared 배포 서버(EC2)의 nginx 리버스 프록시 설정 기록본입니다.

## 이 파일이 뭔가

- `default` — 서버 `/etc/nginx/sites-available/default`의 내용.
- nginx는 **서버에 직접설치(apt)** 돼 있고, 이 파일은 그 설정의 **백업·공유용 복사본**입니다.
- ⚠️ 이 파일을 저장소에서 고쳐도 **서버에 자동 반영되지 않습니다.** 서버 반영은 아래 절차대로 수동으로 합니다. (자동화는 CI/CD 도입 시)

## 라우팅 구조

```
브라우저 → nginx(:80/:443)
   ├─ /api     → localhost:8080 (백엔드 컨테이너)
   ├─ /ws      → localhost:8080 (워커 WebSocket, 443 블록에만 있음)
   ├─ /grafana → 127.0.0.1:3001 (관제 Grafana, 443 블록에만 있음)
   └─ /        → localhost:3000 (프론트 컨테이너)
```

`/grafana` 는 `infra/observability` 의 별도 compose 가 띄우는 Grafana 다. 그쪽은 루프백에만
묶여 있어서 이 경로가 인터넷에서 들어오는 유일한 길이다. Grafana 를 `/grafana` 하위 경로로
서비스하려면 Grafana 쪽에도 `GF_SERVER_ROOT_URL` 과 `GF_SERVER_SERVE_FROM_SUB_PATH` 가
있어야 한다. 둘은 `infra/observability/docker-compose.yml` 에 들어 있다.

워커 실행파일 `/downloads/G-shared.exe` 는 nginx 에 별도 location 이 없다. `/` 를 타고 프론트 컨테이너가 이미지 안의 `public/downloads/G-shared.exe` 를 내려준다(원본은 저장소 `system/G-shared/G-shared.exe`, `docker-compose.yml` 의 `additional_contexts` 로 빌드 때 복사).

## 서버에 반영하는 법

서버에서:

```bash
# 1. 백업 (되돌릴 수 있게 먼저)
sudo cp /etc/nginx/sites-available/default /etc/nginx/sites-available/default.bak

# 2. 이 저장소의 default 내용으로 교체
#    (저장소를 서버에 pull 받은 뒤)
sudo cp infra/nginx/default /etc/nginx/sites-available/default

# 3. 문법 검사 (통과해야 함)
sudo nginx -t

# 4. 적용 (무중단)
sudo systemctl reload nginx
```

### 3번을 건너뛰지 않는다

이 파일에는 `server` 블록 밖에 있는 것이 셋 있다. `map` 둘과 `log_format` 하나다.
`log_format gshared` 가 그 `map` 들을 참조하므로 **셋이 함께 있어야 한다.**

일부만 복사하면 `unknown "args_redacted" variable` 같은 오류가 나는데, 다행히 그 실패는
`nginx -t` 에서 걸린다. **검사를 통과하기 전에는 reload 하지 않는다.** 실행 중인 nginx 는
옛 설정을 그대로 들고 있으므로 이 단계에서 멈추면 서비스에 영향이 없다.

```
nginx: [emerg] unknown "args_redacted" variable   ← 여기서 멈춰야 한다
```

### log_format 을 바꿨을 때

`access_log ... gshared` 지시어는 두 `server` 블록 안에 각각 있다. 새 `server` 블록을
추가하면 **거기에도 넣어야 한다.** 빠뜨리면 그 블록만 조용히 기본 포맷(`combined`)으로
남아서 서명 URL 이 그대로 적힌다. 오류는 나지 않는다.

반영한 뒤 실제 요청으로 확인한다.

```bash
curl -sk -o /dev/null "https://j15e204.p.ssafy.io/api/storage/jobs/99/x?sig=TESTVALUE123"
sudo tail -1 /var/log/nginx/access.log       # sig=REDACTED 로 보여야 한다
sudo grep -c "TESTVALUE123" /var/log/nginx/access.log   # 0 이어야 한다
```

reload 는 새 요청부터 적용된다. 이미 열려 있던 연결은 옛 설정을 쓰므로, 방금 띄운 요청으로
확인해야 한다.

## 참고

- 방화벽(UFW): 22·80·443 개방됨.
- 프론트 컨테이너는 `docker-compose.yml`에서 `127.0.0.1:3000:3000`으로 매핑 (루프백만, 외부는 nginx 443 경유). MySQL 3306 도 `127.0.0.1` 바인딩.
- DB 를 GUI 툴로 직접 볼 때는 SSH 터널: `ssh -i ~/.ssh/J15E204T.pem -L 3306:127.0.0.1:3306 ubuntu@j15e204.p.ssafy.io`
- 접근 로그의 서명 URL 은 `log_format gshared` 가 `sig=` 값을 지운다. 이유와 위험은 `infra/observability/README.md` 의 "로그 내용이 곧 자격 증명이 될 수 있다" 절에 있다.
- `/grafana` 를 열면 인터넷 누구나 로그인 화면까지 닿는다. 관리자 암호가 유일한 장벽이므로 짐작할 수 없는 값으로 두고, 시험용 암호를 그대로 쓰지 않는다.
- HTTPS(443)는 Let's Encrypt 로 적용됨.
