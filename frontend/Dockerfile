FROM node:20-alpine AS build
WORKDIR /app
# 通知案内などに埋め込む（空なら無視）。docker-compose の build.args から渡す
ARG VITE_ALT_APP_HOSTNAME=
ENV VITE_ALT_APP_HOSTNAME=$VITE_ALT_APP_HOSTNAME
# サブパス配信: VITE_BASE_PATH=/meetingminutesnotebook/ と VITE_API_BASE=/meetingminutesnotebook（末尾スラなし可）
ARG VITE_BASE_PATH=/
ENV VITE_BASE_PATH=$VITE_BASE_PATH
ARG VITE_API_BASE=
ENV VITE_API_BASE=$VITE_API_BASE
COPY package.json ./
RUN npm install
COPY . .
# ビルドログで確認用（サブパスが効かないときは .env の VITE_BASE_PATH と compose の引数渡しを疑う）
RUN printf '>>> build: VITE_BASE_PATH=%s VITE_API_BASE=%s\n' "${VITE_BASE_PATH:-}" "${VITE_API_BASE:-}" && npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
