# Build stage
FROM node:18-alpine AS build
WORKDIR /app

# Allow build-time injection of the API URL for Vite
ARG VITE_API_URL
ENV VITE_API_URL=${VITE_API_URL}

# Copy package files and install
COPY packages/frontend/package.json packages/frontend/package-lock.json* ./
COPY packages/frontend/tsconfig*.json ./
COPY packages/frontend/ ./

RUN npm ci --silent
RUN npm run build

# Production stage: Nginx
FROM nginx:stable-alpine

# Copy built assets
COPY --from=build /app/dist /usr/share/nginx/html

# Custom nginx config to serve SPA and proxy /api to backend
COPY packages/frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
