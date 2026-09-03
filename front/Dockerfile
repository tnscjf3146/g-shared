# nginx 이미지에 정적 HTML을 넣어 서빙하는 컨테이너
# (진짜 React도 빌드 결과가 정적파일이라 같은 구조)
FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
EXPOSE 80
