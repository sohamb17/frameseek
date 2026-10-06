# Build context is the repository root (see docker-compose.yml).
# Stage 1: build the React UI.
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: build the Go API as a static binary.
FROM golang:1.25-alpine AS api
WORKDIR /src
COPY api/go.mod api/go.sum ./
RUN go mod download
COPY api/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/server ./cmd/server

# Stage 3: small runtime image that serves the API and the built UI on one port.
FROM alpine:3.22
RUN apk add --no-cache ca-certificates && adduser -D -u 10001 app
COPY --from=api /out/server /app/server
COPY --from=web /web/dist /app/web
ENV FRAMESEEK_WEB_DIR=/app/web FRAMESEEK_CONFIG_DIR=/app/configs FRAMESEEK_DATA_DIR=/data FRAMESEEK_ADDR=:8080
EXPOSE 8080
CMD ["/app/server"]
