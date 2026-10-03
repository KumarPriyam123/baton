# syntax=docker/dockerfile:1
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# Unit-test image: `docker compose -f compose.yaml -f compose.test.yaml run --rm web-test`
FROM deps AS test
COPY . .
CMD ["npm", "run", "test"]

FROM deps AS build
COPY . .
RUN npm run build

# Runtime image (last stage = default target): static files behind nginx.
FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
