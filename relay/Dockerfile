# Relay server image. The static web client is copied in alongside it so a
# single container serves both the WebSocket relay and GET /.
FROM node:22-alpine

WORKDIR /app

# Install production deps first for better layer caching.
COPY relay/package.json relay/package-lock.json* ./relay/
WORKDIR /app/relay
RUN npm ci --omit=dev || npm install --omit=dev

# App source + the static client it serves.
WORKDIR /app
COPY relay/ ./relay/
COPY client/ ./client/

WORKDIR /app/relay
ENV PORT=8080
EXPOSE 8080
CMD ["node", "src/server.js"]
