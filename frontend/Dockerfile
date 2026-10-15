# Pulse frontend — production build served by nginx.
#
# Vite bakes `VITE_API_BASE` into the bundle at build time, so it must be
# supplied as a build argument (see docs/DEPLOY.md).

FROM node:24-alpine AS build
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
