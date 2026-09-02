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

# 1단계에서 만든 jar만 가져옴
COPY --from=build /app/build/libs/*.jar app.jar

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
