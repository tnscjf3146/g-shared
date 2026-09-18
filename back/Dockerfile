# ── 1단계: 빌드 (소스코드 → jar) ─────────────────────
FROM eclipse-temurin:21-jdk AS build
WORKDIR /app

# Gradle 설정 파일만 먼저 복사 → 의존성 다운로드 (캐시 활용)
COPY gradlew settings.gradle build.gradle ./
COPY gradle ./gradle
RUN chmod +x gradlew && ./gradlew dependencies --no-daemon || true

# 소스코드 복사 후 빌드 (테스트는 배포 빌드에서 제외)
COPY src ./src
RUN ./gradlew bootJar --no-daemon

# ── 2단계: 실행 (jar만 담은 가벼운 이미지) ─────────────
FROM eclipse-temurin:21-jre
WORKDIR /app

# JVM 기본 타임존. 없으면 이미지가 UTC 로 떠서 LocalDateTime.now()·@CreatedDate 가 UTC 벽시계를 저장하고,
# 브라우저(KST)는 존 없는 그 값을 로컬로 읽어 모든 서버 시각이 9시간 빠르게 보였다(2026-09-18).
# JDBC 의 serverTimezone=Asia/Seoul 과 같은 값이어야 한다. 이미 UTC 로 저장된 행은 V20 이 같은 배포에서 보정한다.
ENV TZ=Asia/Seoul

# 1단계에서 만든 jar만 가져옴
COPY --from=build /app/build/libs/*.jar app.jar

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
