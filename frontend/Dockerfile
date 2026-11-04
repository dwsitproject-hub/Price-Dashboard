# AASTAR frontend image — build the SPA, serve it with Nginx and reverse-proxy /api to the backend.
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_GIT_SHA=docker
ARG VITE_HUB_SDK_URL=
ARG VITE_HUB_SDK_KEY=
ENV VITE_GIT_SHA=$VITE_GIT_SHA VITE_HUB_SDK_URL=$VITE_HUB_SDK_URL VITE_HUB_SDK_KEY=$VITE_HUB_SDK_KEY
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /src/dist /usr/share/nginx/html
RUN rm -f /usr/share/nginx/html/assets/*.map
COPY nginx.security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY nginx.docker.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
